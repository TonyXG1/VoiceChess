"""Role 2 - audio output (Text-to-Speech).

Every role speaks through the same tiny `Speaker` interface, so changing HOW
the robot talks is one line and nothing else moves:

    speaker = get_speaker("print")     # no audio, just prints  (default / testing)
    speaker = get_speaker("espeak")    # offline robotic speech (easiest on the Pi)
    speaker = get_speaker("piper")     # offline natural-sounding speech (nicer, more setup)

    speaker.say("You said pawn to e4")

If a backend can't start (missing package, no audio device), get_speaker never
crashes the game -- it prints a note and falls back to the print speaker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional, Protocol


class Speaker(Protocol):
    """Anything that can turn a string into audio (or a stand-in for it)."""
    def say(self, text: str) -> None: ...


class PrintSpeaker:
    """No audio -- just prints. Safe default; never fails, never needs hardware."""
    def say(self, text: str) -> None:
        print(f"[SPEAK] {text}")


class EspeakSpeaker:
    """Offline speech by calling the espeak-ng binary directly (blocking).

    The most reliable TTS on the Pi: no Python audio layer at all, just the
    system binary through ALSA. Needs:  sudo apt install espeak-ng
    """
    def __init__(self, rate: int = 150, voice: str = "en") -> None:
        self._bin = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self._bin:
            raise RuntimeError("espeak-ng binary not found on PATH")
        self._rate = rate
        self._voice = voice

    def say(self, text: str) -> None:
        print(f"[SPEAK] {text}")
        subprocess.run(
            [self._bin, "-s", str(self._rate), "-v", self._voice, text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )


class Pyttsx3Speaker:
    """Offline speech via pyttsx3 (SAPI5 on Windows, espeak-ng on the Pi).

    Robotic but fully offline. Needs:  pyttsx3 (+ espeak-ng on Linux).

    A fresh engine is built for every utterance: pyttsx3's event loop goes
    silent after the first runAndWait() on many platforms (pyttsx3 issue
    #193), which made every announcement after "New game" inaudible.
    """
    def __init__(self, rate: int = 150, volume: float = 1.0,
                 voice_hint: str = "english") -> None:
        import pyttsx3  # imported here so the module never hard-requires it
        self._pyttsx3 = pyttsx3
        self._rate = rate
        self._volume = volume
        self._voice_hint = voice_hint
        self._make_engine().stop()  # fail here, at startup, if TTS can't init

    def _make_engine(self):
        engine = self._pyttsx3.init()
        engine.setProperty("rate", self._rate)      # slower = clearer for move calls
        engine.setProperty("volume", self._volume)
        # Try to pick a clear English voice if several are installed.
        try:
            for v in engine.getProperty("voices"):
                if self._voice_hint.lower() in (v.name or "").lower() or \
                   self._voice_hint.lower() in (v.id or "").lower():
                    engine.setProperty("voice", v.id)
                    break
        except Exception:
            pass
        return engine

    def say(self, text: str) -> None:
        print(f"[SPEAK] {text}")
        engine = self._make_engine()
        engine.say(text)
        engine.runAndWait()
        engine.stop()


class PiperSpeaker:
    """Offline, natural-sounding speech via Piper (great on the Pi).

    Needs the `piper` command on PATH and a downloaded voice model (.onnx).
    Point it at the model with the PIPER_MODEL env var or the model_path arg.
    Audio is played through `aplay` (part of alsa-utils, already on Pi OS).
    """
    def __init__(self, model_path: Optional[str] = None,
                 player: str = "aplay") -> None:
        self._piper = shutil.which("piper")
        if not self._piper:
            raise RuntimeError("piper binary not found on PATH")
        self._model = model_path or os.environ.get("PIPER_MODEL")
        if not self._model or not os.path.isfile(self._model):
            raise RuntimeError("Piper voice model not found; set PIPER_MODEL=/path/voice.onnx")
        self._player = shutil.which(player)
        if not self._player:
            raise RuntimeError(f"audio player '{player}' not found (install alsa-utils)")

    def say(self, text: str) -> None:
        print(f"[SPEAK] {text}")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav = tmp.name
        try:
            subprocess.run(
                [self._piper, "--model", self._model, "--output_file", wav],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
            subprocess.run([self._player, wav],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass


def get_speaker(kind: str = "print", **kwargs) -> Speaker:
    """Factory. kind = "print" | "espeak"/"pyttsx3" | "piper".

    Any failure to start a real backend falls back to PrintSpeaker so the game
    loop can never be killed by an audio problem.
    """
    kind = (kind or "print").lower()
    try:
        if kind == "espeak":
            # The direct binary is the reliable path on the Pi; Windows dev
            # boxes have no espeak-ng, so fall through to pyttsx3 there.
            try:
                return EspeakSpeaker(**kwargs)
            except Exception:
                return Pyttsx3Speaker()
        if kind == "pyttsx3":
            return Pyttsx3Speaker(**kwargs)
        if kind == "piper":
            return PiperSpeaker(**kwargs)
    except Exception as e:
        print(f"[SPEAK] ({kind} unavailable: {e}; falling back to print)")
        return PrintSpeaker()
    return PrintSpeaker()
