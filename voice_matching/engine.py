"""Core Speech Recognition and phonetic matching engine for Chess moves (Role 1).

Maintains the offline Vosk speech model instance, streams mono channel PCM audio,
and implements similarity matching using RapidFuzz Levenshtein distance.

Upstream 0.2.0 additions merged here: a spoken confirmation layer (the engine
reads the understood move back and waits for yes/no before returning it) and
stricter matching (ambiguity margin between best and runner-up move, rejection
of transcripts containing any '[unk]' token).
"""

import json
import queue
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional

from rapidfuzz import process as rf_process
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


@dataclass
class MatchResult:
    """Three-way classification of one utterance (Role 1's return contract).

    status:
        "legal"        -- confidently matches a move in legal_moves; move set.
        "illegal"      -- confidently matches a well-formed move that is NOT
                          legal right now (identified in move).
        "unrecognized" -- no confident match to anything move-shaped; move None.
    """
    status: Literal["legal", "illegal", "unrecognized"]
    move: Optional[str] = None


# How many top-scoring phrases to pull from each candidate pool. Only needs to
# be deep enough to expose the best-scoring RIVAL move for the ambiguity
# margin; 100 leaves ample room even when one move's variants crowd the top.
_TOP_K = 100


def _reject_transcript(text: str) -> bool:
    """Noise guards: empty transcript, or '[unk]' content per REJECT_PARTIAL_UNK."""
    if not text:
        return True
    if config.REJECT_PARTIAL_UNK:
        return "[unk]" in text.split()
    return text == "[unk]"


def _best_score_per_move(text: str, phrases, moves,
                         skip: frozenset = frozenset()) -> Dict[str, float]:
    """Best similarity per move over parallel (phrases, move_of_phrase) arrays.

    Uses rapidfuzz's C-level batch scorer -- the superset pool is ~800k
    phrases, far beyond what a Python loop can score per utterance on a Pi.
    """
    hits = rf_process.extract(text, phrases,
                              scorer=Levenshtein.normalized_similarity,
                              limit=_TOP_K)
    best: Dict[str, float] = {}
    for _phrase, score, idx in hits:
        move = moves[idx]
        if move in skip:
            continue
        if score > best.get(move, -1.0):
            best[move] = score
    return best


def _rank_and_guard(move_scores: Dict[str, float]) -> Optional[str]:
    """Applies the confidence threshold + ambiguity margin; None when unsure.

    The best-scoring move must reach config.CONFIDENCE_THRESHOLD and beat the
    runner-up move by config.AMBIGUITY_MARGIN (near-ties are rejected, not
    just exact ties).
    """
    if not move_scores:
        return None
    ranked = sorted(move_scores.items(), key=lambda item: item[1], reverse=True)
    best_move, best_score = ranked[0]
    if best_score < config.CONFIDENCE_THRESHOLD:
        return None
    # The 1e-9 epsilon keeps a gap of EXACTLY the margin from being rejected
    # by float error: 1- and 2-edit rivals over a 20-char transcript score
    # 0.95 and 0.90, but 0.95 - 0.90 computes as 0.04999... < 0.05.
    if (len(ranked) > 1
            and best_score - ranked[1][1] < config.AMBIGUITY_MARGIN - 1e-9):
        return None
    return best_move


def _match_from_phrases(text: str, phrases_cache: Dict[str, List[str]]) -> str:
    """Maps a transcript to a single move from the given phrase cache, or
    'unrecognized' when unsure (noise guards + threshold + ambiguity margin).
    """
    if _reject_transcript(text):
        return "unrecognized"
    phrases: List[str] = []
    moves: List[str] = []
    for move, move_phrases in phrases_cache.items():
        for phrase in move_phrases:
            phrases.append(phrase)
            moves.append(move)
    best = _rank_and_guard(_best_score_per_move(text, phrases, moves))
    return best if best is not None else "unrecognized"


# White/black castle UCIs share IDENTICAL phrase lists ("kingside castle" is
# both e1g1 and e8g8), so in the position-independent superset each pair is
# one spoken candidate, not two -- left unmerged they would tie and force
# every castle utterance to "unrecognized".
_CASTLE_TWINS = (("e1g1", "e8g8"), ("e1c1", "e8c8"))


def _merge_castle_twins(move_scores: Dict[str, float], legal_set: frozenset,
                        pieces: Optional[Dict[str, str]]) -> None:
    """Collapses each castle twin pair to its legal-king-move UCI if there is
    one (so "castle" resolves to the playable side), else to whichever scored.
    """
    for a, b in _CASTLE_TWINS:
        scores = [s for s in (move_scores.get(a), move_scores.get(b)) if s is not None]
        if not scores:
            continue

        def _is_legal_castle(uci: str) -> bool:
            # A legal e1g1 that is a ROOK slide (pieces map) is not castling
            # and must not absorb the castle-phrase score.
            return uci in legal_set and (pieces is None or pieces.get(uci) == "king")

        if _is_legal_castle(a):
            keep = a
        elif _is_legal_castle(b):
            keep = b
        else:
            keep = a if move_scores.get(a) is not None else b
        combined = max(scores + [move_scores.get(keep, -1.0)])
        move_scores.pop(a, None)
        move_scores.pop(b, None)
        move_scores[keep] = combined


def _classify(text: str, legal_moves: List[str],
              phrases_cache: Dict[str, List[str]],
              pieces: Optional[Dict[str, str]] = None) -> MatchResult:
    """ONE scoring pass over the full move superset + the legal-move overlay.

    The static superset (phonetics.superset_index) carries lean fully-qualified
    phrases for every possible move; the per-turn phrases_cache carries the
    full piece-aware phrase sets for the CURRENT legal moves. Legal moves are
    scored only through the overlay (their superset entries are skipped --
    the overlay is a superset of those forms, and this keeps e1g1-as-a-rook-
    slide from matching castle phrases). The single ranked result is then
    split by legality: best move in legal_moves -> legal, else -> illegal.
    """
    if _reject_transcript(text):
        return MatchResult("unrecognized")

    legal_set = frozenset(legal_moves)

    # Skip the superset entries the overlay already covers: the legal UCIs
    # themselves, plus every piece-destination pseudo-move that corresponds
    # to a legal move ("knight to c3" must score through the overlay -> legal
    # when Nc3 is playable, and through the pseudo-move -> illegal when not).
    # Without a pieces map the overlay accepts every piece word for every
    # legal move, so all pieces are skipped for each legal destination.
    skip = set(legal_set)
    for move in legal_moves:
        words = ([pieces[move]] if pieces and move in pieces
                 else phonetics.PIECES)
        for word in words:
            skip.add(phonetics.piece_dest_key(word, move[2:4]))

    sup_phrases, sup_moves = phonetics.superset_index()
    move_scores = _best_score_per_move(text, sup_phrases, sup_moves,
                                       skip=frozenset(skip))

    o_phrases: List[str] = []
    o_moves: List[str] = []
    for move, move_phrases in phrases_cache.items():
        for phrase in move_phrases:
            o_phrases.append(phrase)
            o_moves.append(move)
    for move, score in _best_score_per_move(text, o_phrases, o_moves).items():
        if score > move_scores.get(move, -1.0):
            move_scores[move] = score

    _merge_castle_twins(move_scores, legal_set, pieces)

    best_move = _rank_and_guard(move_scores)
    if best_move is None:
        return MatchResult("unrecognized")
    if best_move in legal_set:
        return MatchResult("legal", best_move)
    return MatchResult("illegal", best_move)


# Spoken when a bare "castle" is heard while both castles are legal: the
# matcher rejects the tie (the player must pick a side), and the generic
# "say it again" retry would leave a blind player no clue why it failed.
# Shared by the live engine and main.py's TextVoice.
CASTLE_AMBIGUITY_HINT = "Both castles are possible. Say kingside or queenside."

_KINGSIDE_UCIS = ("e1g1", "e8g8")
_QUEENSIDE_UCIS = ("e1c1", "e8c8")


def is_ambiguous_castle(transcript: str, legal_moves: List[str],
                        pieces: Optional[Dict[str, str]] = None) -> bool:
    """True when the utterance sounds like a castle request while BOTH castles
    are legal — the one rejection the player cannot diagnose from a generic
    retry prompt. The caller should speak CASTLE_AMBIGUITY_HINT.
    """
    text = (transcript or "").strip().lower()
    if "castle" not in text.split():
        return False

    def _is_castle(uci: str) -> bool:
        # With a pieces map, e1g1 only counts as castling if the KING moves
        # (a rook/queen sliding e1->g1 is a normal move).
        return uci in legal_moves and (pieces is None or pieces.get(uci) == "king")

    return (any(_is_castle(u) for u in _KINGSIDE_UCIS)
            and any(_is_castle(u) for u in _QUEENSIDE_UCIS))


def match_text(transcript: str, legal_moves: List[str],
               pieces: Optional[Dict[str, str]] = None) -> MatchResult:
    """Classify an already-transcribed utterance: legal / illegal / unrecognized.

    Pure function — no audio, no hardware. This is the matching half of
    listen_for_move(), split out so the pipeline can run text-only.

    Args:
        transcript: The heard/typed utterance, e.g. "pawn to e four".
        legal_moves: Current legal moves in UCI format (e.g. ['e2e4', 'g1f3']).
        pieces: Optional map of UCI move -> spoken piece word ("pawn".."king"),
            from the rules authority. With it, a spoken piece name only matches
            moves of that piece ("knight to g3" can no longer hit the pawn
            move g2g3). Without it, any piece word matches any move.

    Returns:
        MatchResult. status "legal" or "illegal" carries the identified UCI
        move; "unrecognized" means no confident, unambiguous match to anything
        move-shaped (noise, silence, unrelated speech, or a near-tie).
    """
    if not legal_moves:
        return MatchResult("unrecognized")

    text = (transcript or "").strip().lower()
    phrases_cache = {move: phonetics.generate_phrases(move, (pieces or {}).get(move))
                     for move in legal_moves}
    return _classify(text, legal_moves, phrases_cache, pieces)


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

    def listen_for_move(
        self,
        legal_moves: List[str],
        pieces: Optional[Dict[str, str]] = None,
        require_confirmation: Optional[bool] = None,
        on_prompt: Optional[Callable[[str], None]] = None,
    ) -> MatchResult:
        """Captures audio from the microphone and classifies the utterance.

        This method is blocking. It configures a dynamic grammar engine with
        only the relevant vocabulary words to improve accuracy and speed.

        When confirmation is enabled (config.REQUIRE_CONFIRMATION, overridable per
        call), an understood LEGAL move is read back to the player, who must
        answer 'yes' before it is returned. A 'no' discards the move and
        re-listens (up to config.MAX_MOVE_ATTEMPTS total attempts); an unclear
        answer after config.MAX_CONFIRMATION_ATTEMPTS prompts fails safe to
        unrecognized. An ILLEGAL classification returns immediately without
        confirmation -- announcing it is the Hub's job.

        Args:
            legal_moves: A list of current legal moves in standard UCI format
                         (e.g., ['e2e4', 'g1f3', 'e1g1']).
            pieces: Optional map of UCI move -> spoken piece word ("pawn"..
                    "king") from the rules authority; restricts piece-name
                    phrases (and the Vosk grammar) to the piece that actually
                    moves. See match_text.
            require_confirmation: Overrides config.REQUIRE_CONFIRMATION when set.
            on_prompt: Callback invoked with read-back/prompt text so the Hub can
                       route it to a speaker or display. Defaults to printing.

        Returns:
            MatchResult: "legal" (confirmed when enabled) or "illegal" with the
            identified move, else "unrecognized".
        """
        # If no legal moves are provided, there is nothing to match.
        if not legal_moves:
            return MatchResult("unrecognized")

        if require_confirmation is None:
            require_confirmation = config.REQUIRE_CONFIRMATION
        prompt = on_prompt if on_prompt is not None else print

        # Precompute all phonetic phrases and flatten vocabulary
        phrases_cache = {move: phonetics.generate_phrases(move, (pieces or {}).get(move))
                         for move in legal_moves}
        unique_words = sorted(set(
            word.lower().strip()
            for phrases in phrases_cache.values()
            for phrase in phrases
            for word in phrase.split()
            if word.lower().strip()
        ))

        # We ensure at least some valid grammar words exist.
        if not unique_words:
            return "unrecognized"

        for attempt in range(config.MAX_MOVE_ATTEMPTS):
            text = self._transcribe(unique_words, config.LISTEN_TIMEOUT_SECONDS)
            result = _classify(text, legal_moves, phrases_cache, pieces)

            if result.status == "unrecognized":
                # A bare "castle" with both castles legal is rejected as a
                # tie; tell the player WHY and re-listen instead of leaving
                # them with the generic retry prompt.
                if is_ambiguous_castle(text, legal_moves, pieces):
                    prompt(CASTLE_AMBIGUITY_HINT)
                    continue
                return result

            if result.status == "illegal":
                # No yes/no round for illegal moves: the Hub announces which
                # move was heard and why it can't be played, then re-listens.
                return result

            if not require_confirmation:
                return result

            decision = self._confirm_move(result.move, prompt)
            if decision == "yes":
                return result
            if decision == "no":
                if attempt + 1 < config.MAX_MOVE_ATTEMPTS:
                    prompt("Move discarded. Please repeat your move.")
                continue
            # Unclear confirmation after all prompts: fail safe.
            return MatchResult("unrecognized")

        return MatchResult("unrecognized")

    def _transcribe(self, grammar_words: List[str], timeout_seconds: float) -> str:
        """Records one utterance restricted to the given grammar and returns its transcript.

        Opens the microphone, streams PCM chunks into a KaldiRecognizer configured
        with a dynamic grammar (plus '[unk]'), and returns the lowercase
        transcript ('' for silence).

        Vosk fires an endpoint (AcceptWaveform -> True) on ANY pause, including
        the leading silence before the player has spoken. Those empty endpoints
        must NOT end the capture -- breaking on them made every turn after a
        TTS announcement fail instantly ("I didn't catch that") while the
        player was still drawing breath. Only an endpoint that contains actual
        words ends the loop; once speech is detected, the deadline is extended
        by config.UTTERANCE_TIMEOUT_SECONDS so the utterance can finish.
        """
        grammar_json = json.dumps(grammar_words + ["[unk]"])
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
        deadline = time.time() + timeout_seconds

        # Stream and capture audio, pipe into Vosk.
        # Managed with RawInputStream context manager to prevent stream leaks and double-exceptions.
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
                                           time.time() + config.UTTERANCE_TIMEOUT_SECONDS)
                if not transcript_json:
                    # Deadline passed (or stream died): salvage anything heard.
                    transcript_json = rec.FinalResult()
        except Exception as e:
            # Propagate error with clean context
            raise RuntimeError(f"Error occurred during sound capture or decoding: {e}") from e

        if not transcript_json:
            return ""

        result = json.loads(transcript_json)
        text = result.get("text", "").strip().lower()
        print(f"[VOICE] heard: {text!r}")
        return text

    def _match_move(self, text: str, phrases_cache: Dict[str, List[str]]) -> str:
        """Maps a transcript to a single legal move, or 'unrecognized' when unsure.

        Thin wrapper over the module-level matcher so the text-only pipeline
        (match_text) and the live pipeline share identical strictness guards.
        """
        return _match_from_phrases(text, phrases_cache)

    def _confirm_move(self, move: str, prompt: Callable[[str], None]) -> str:
        """Reads the understood move back and listens for a spoken yes/no.

        Returns 'yes', 'no', or 'unclear' (no clear answer after
        config.MAX_CONFIRMATION_ATTEMPTS prompts).
        """
        grammar_words = sorted(set(phonetics.YES_WORDS + phonetics.NO_WORDS))

        for _ in range(config.MAX_CONFIRMATION_ATTEMPTS):
            prompt(
                f"I understood '{phonetics.describe_move(move)}'. "
                f"Say 'yes' to confirm or 'no' to try again."
            )
            text = self._transcribe(grammar_words, config.CONFIRMATION_TIMEOUT_SECONDS)
            decision = self._parse_confirmation(text)
            if decision != "unclear":
                return decision

        return "unclear"

    @staticmethod
    def _parse_confirmation(text: str) -> str:
        """Classifies a confirmation transcript as 'yes', 'no', or 'unclear'.

        The answer counts only if it is unambiguous: a transcript containing
        both affirmative and negative words (or neither) is 'unclear'.
        """
        tokens = set(text.split())
        has_yes = bool(tokens & set(phonetics.YES_WORDS))
        has_no = bool(tokens & set(phonetics.NO_WORDS))

        if has_yes and not has_no:
            return "yes"
        if has_no and not has_yes:
            return "no"
        return "unclear"
