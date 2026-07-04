"""Core Speech Recognition and phonetic matching engine for Chess moves (Role 1).

Maintains the offline Vosk speech model instance, streams mono channel PCM audio,
and implements similarity matching using RapidFuzz Levenshtein distance.
"""

import json
import queue
import sys
import time
from typing import Any, Dict, List, Optional

from rapidfuzz.distance import Levenshtein

# Audio/STT stack is optional: on dev machines without PortAudio or a Vosk
# install, the pure-text path (match_text) must still work.
try:
    import sounddevice
    import vosk
except (ImportError, OSError):  # OSError: PortAudio DLL missing
    sounddevice = None
    vosk = None

from . import config
from . import phonetics


def match_text(transcript: str, legal_moves: List[str],
               pieces: Optional[Dict[str, str]] = None) -> str:
    """Match an already-transcribed utterance against the current legal moves.

    Pure function — no audio, no hardware. This is the second half of
    listen_for_move(), split out so the pipeline can run text-only.

    Args:
        transcript: The heard/typed utterance, e.g. "pawn to e four".
        legal_moves: Current legal moves in UCI format (e.g. ['e2e4', 'g1f3']).
        pieces: Optional map of UCI move -> spoken piece word ("pawn".."king"),
            from the rules authority. With it, a spoken piece name only matches
            moves of that piece ("knight to g3" can no longer hit the pawn
            move g2g3). Without it, any piece word matches any move.

    Returns:
        The matched UCI move string, or 'unrecognized' if confidence is too
        low or the best score is an ambiguous tie.
    """
    if not legal_moves:
        return "unrecognized"

    text = (transcript or "").strip().lower()

    # Noise/Silence Guard
    if not text or text == "[unk]":
        return "unrecognized"

    phrases_cache = {move: phonetics.generate_phrases(move, (pieces or {}).get(move))
                     for move in legal_moves}

    # Perform Levenshtein similarity evaluation over all phrase permutations
    best_score: float = -1.0
    best_moves: List[str] = []

    for move, move_phrases in phrases_cache.items():
        for phrase in move_phrases:
            # Compute similarity [0.0 - 1.0] using rapidfuzz
            score = Levenshtein.normalized_similarity(text, phrase)

            if score > best_score:
                best_score = score
                best_moves = [move]
            elif score == best_score:
                # Avoid duplicate records for the same move
                if move not in best_moves:
                    best_moves.append(move)

    # Confidence Threshold and Ambiguity Tie-Breaker Guards
    if best_score < config.CONFIDENCE_THRESHOLD:
        return "unrecognized"

    if len(best_moves) > 1:
        # Muddy transcript resulted in an exact tie between different UCI moves
        return "unrecognized"

    return best_moves[0]


class VoiceMatchEngine:
    """Core class for speech-to-text capture and move validation.

    Loads the Vosk Model once during initialization to avoid game-play latency spikes.
    """

    def __init__(self, model_path: Optional[str] = None) -> None:
        """Initializes the offline Vosk speech recognition model.

        Args:
            model_path: Optional file path to the offline Vosk model. If omitted,
                        defaults to the configuration path (config.MODEL_PATH).
        """
        if vosk is None or sounddevice is None:
            raise RuntimeError(
                "vosk/sounddevice are not installed; live audio capture is "
                "unavailable. Use match_text() / text mode instead."
            )
        path = model_path or config.MODEL_PATH
        # The model is loaded ONCE here to prevent runtime turn latency.
        try:
            self.model = vosk.Model(path)
        except Exception as e:
            raise RuntimeError(
                f"Failed to load Vosk model from path '{path}'. "
                f"Please verify the model directory exists. Error: {e}"
            ) from e

    def listen_for_move(self, legal_moves: List[str],
                        pieces: Optional[Dict[str, str]] = None) -> str:
        """Captures audio from the microphone and matches it against the current legal moves.

        This method is blocking. It configures a dynamic grammar engine with
        only the relevant vocabulary words to improve accuracy and speed.

        Args:
            legal_moves: A list of current legal moves in standard UCI format
                         (e.g., ['e2e4', 'g1f3', 'e1g1']).
            pieces: Optional map of UCI move -> spoken piece word ("pawn"..
                    "king") from the rules authority; restricts piece-name
                    phrases (and the Vosk grammar) to the piece that actually
                    moves. See match_text.

        Returns:
            The matched UCI move string (e.g., 'e2e4', 'e1g1') if confidence
            is high enough and matches are unambiguous; otherwise 'unrecognized'.
        """
        # If no legal moves are provided, there is nothing to match.
        if not legal_moves:
            return "unrecognized"

        # Step 1: Precompute all phonetic phrases and flatten vocabulary
        phrases_cache = {move: phonetics.generate_phrases(move, (pieces or {}).get(move))
                         for move in legal_moves}
        unique_words = sorted(list(set(
            word.lower().strip()
            for phrases in phrases_cache.values()
            for phrase in phrases
            for word in phrase.split()
            if word.lower().strip()
        )))
        
        # We ensure at least some valid grammar words exist.
        if not unique_words:
            return "unrecognized"

        # Step 2: Initialize KaldiRecognizer with dynamic grammar including '[unk]'
        grammar_list = unique_words + ["[unk]"]
        grammar_json = json.dumps(grammar_list)
        rec = vosk.KaldiRecognizer(self.model, config.SAMPLE_RATE, grammar_json)
        rec.SetWords(True)  # Enable detailed word outputs (allows reading confidence scores if needed)

        # Thread-safe queue for audio chunk streaming
        audio_queue: queue.Queue = queue.Queue()

        def audio_callback(indata: memoryview, frames: int, time_info: Any, status: sounddevice.CallbackFlags) -> None:
            """Pushes incoming raw audio data to the queue.

            Args:
                indata: Raw buffer memoryview containing signed 16-bit PCM bytes.
                frames: Number of frames in this chunk.
                time_info: PortAudio time structure (Any due to CFFI CData type).
                status: PortAudio stream flags.
            """
            if status:
                print(f"Audio callback status warning: {status}", file=sys.stderr)
            audio_queue.put(bytes(indata))

        transcript_json = ""
        speech_started = False
        deadline = time.time() + config.LISTEN_TIMEOUT

        # Step 3: Stream and capture audio, pipe into Vosk.
        # Managed with RawInputStream context manager to prevent stream leaks and double-exceptions.
        #
        # Vosk fires an endpoint (AcceptWaveform -> True) on ANY pause,
        # including the leading silence before the player has spoken. Those
        # empty endpoints must NOT end the listen -- breaking on them made
        # every turn after a TTS announcement fail instantly ("I didn't catch
        # that") while the player was still drawing breath. Only an endpoint
        # that contains actual words ends the loop.
        try:
            with sounddevice.RawInputStream(
                samplerate=config.SAMPLE_RATE,
                blocksize=4000,  # Block size representing ~250ms of audio
                dtype=config.DTYPE,
                channels=config.CHANNELS,
                device=config.DEVICE_INDEX,
                callback=audio_callback,
            ) as stream:
                print("[VOICE] listening...")
                while time.time() < deadline:
                    try:
                        # 0.2s timeout to prevent thread deadlock if callback stops
                        data = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        if not stream.active:
                            break
                        continue

                    if rec.AcceptWaveform(data):
                        result_json = rec.Result()
                        text = json.loads(result_json).get("text", "")
                        if text.replace("[unk]", "").strip():
                            transcript_json = result_json
                            break
                        # Endpoint fired on silence/noise only: the player just
                        # hasn't spoken yet. Keep listening.
                        speech_started = False
                    elif not speech_started:
                        partial = json.loads(rec.PartialResult()).get("partial", "")
                        if partial.replace("[unk]", "").strip():
                            # Speech has begun -- give the utterance room to
                            # finish even if the no-speech deadline is close.
                            speech_started = True
                            deadline = max(deadline,
                                           time.time() + config.UTTERANCE_TIMEOUT)
                if not transcript_json:
                    # Deadline passed (or stream died): salvage anything heard.
                    transcript_json = rec.FinalResult()
        except Exception as e:
            # Propagate error with clean context
            raise RuntimeError(f"Error occurred during sound capture or decoding: {e}") from e

        if not transcript_json:
            return "unrecognized"

        result = json.loads(transcript_json)
        text = result.get("text", "").strip().lower()
        print(f"[VOICE] heard: {text!r}")

        # Steps 4-6 (noise guard, similarity scoring, confidence/ambiguity
        # guards) live in match_text so the text-only pipeline shares them.
        return match_text(text, legal_moves, pieces)
