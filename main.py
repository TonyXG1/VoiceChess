#!/usr/bin/env python3
"""VoiceChess entry point (Role 5 wiring).

Builds the four modules and hands them to the Orchestrator. Nothing in here
contains game logic -- it only decides WHICH implementations to wire up:

    voice:   VoiceMatchEngine (live mic)  |  TextVoice (typed/scripted, no hardware)
    chess:   ChessEngine (python-chess + Stockfish)
    motion:  MotionPlanner (Role 3's real square->mm + G-code planner)
    serial:  SerialLink (stub until Role 4's ESP32 exists)

Run it:
    python main.py                                  # live voice if possible, else text mode
    python main.py --text                           # force text mode (type your moves)
    python main.py --text --script "e2e4,e7e5,g1f3" # scripted, fully hardware-free
    python main.py --tts pyttsx3                    # real spoken audio
    python main.py --text --serial /dev/ttyUSB0     # stream real G-code to the ESP32
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from typing import List, Optional

from chess_ai import ChessEngine, get_speaker
from motion import MotionPlanner
from orchestrator import Orchestrator, SerialLink
from voice_matching.engine import (CASTLE_AMBIGUITY_HINT, MatchResult,
                                   is_ambiguous_castle, match_text)


class TextVoice:
    """Text-driven stand-in for Role 1: same contract, no mic/model/audio.

    Feeds typed or scripted utterances through voice_matching.match_text, so
    the entire matching pipeline (phrase generation, Levenshtein scoring,
    confidence/ambiguity guards) is exercised exactly as with live audio --
    only the Vosk transcription step is skipped.
    """

    _UCI_SHORTHAND = re.compile(r"^([a-h][1-8])([a-h][1-8])([qrbn])?$")
    _PROMO_WORDS = {"q": "queen", "r": "rook", "b": "bishop", "n": "knight"}

    def __init__(self, script: Optional[List[str]] = None) -> None:
        self.script = list(script) if script else None

    @classmethod
    def _as_transcript(cls, typed: str) -> str:
        """Typed UCI shorthand -> the words a live transcript would contain.

        Vosk emits words ("e2 e4"), never glued squares ("e2e4"), so typed
        shorthand must be expanded here or it can't clear Role 1's confidence
        threshold. Anything that isn't UCI shorthand passes through verbatim.
        """
        m = cls._UCI_SHORTHAND.match(typed.strip().lower())
        if not m:
            return typed
        words = f"{m.group(1)} {m.group(2)}"
        if m.group(3):
            words += f" {cls._PROMO_WORDS[m.group(3)]}"
        return words

    def listen_for_move(self, legal_moves: List[str],
                        pieces: Optional[dict] = None,
                        on_prompt=None) -> Optional[MatchResult]:
        # Returns None when the input source is exhausted (script done, typed
        # quit/exit, EOF) -- the orchestrator ends the game loop on None.
        # on_prompt (Role 1's spoken-prompt hook) is used only for the
        # ambiguous-castle hint; typed input is otherwise its own confirmation.
        if self.script is not None:
            if not self.script:
                return None                         # script exhausted: end cleanly
            transcript = self.script.pop(0)
            print(f"[TEXT-VOICE] heard (scripted): {transcript!r}")
        else:
            try:
                transcript = input("[TEXT-VOICE] say your move > ")
            except EOFError:
                return None
            if transcript.strip().lower() in ("quit", "exit"):
                return None

        transcript = self._as_transcript(transcript)
        print(f"[TEXT-VOICE] transcript: {transcript!r}")
        result = match_text(transcript, legal_moves, pieces)
        if result.status == "unrecognized" and is_ambiguous_castle(transcript, legal_moves, pieces):
            (on_prompt or print)(CASTLE_AMBIGUITY_HINT)
        print(f"[TEXT-VOICE] matched: {result.status}"
              + (f" ({result.move})" if result.move else ""))
        return result


def find_stockfish() -> Optional[str]:
    """PATH first, then a stockfish.exe in the repo root, then the winget shim.

    The winget check covers terminals opened before `winget install` ran --
    their PATH is stale and shutil.which() misses the freshly installed shim.
    """
    found = shutil.which("stockfish")
    if found:
        return found
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Microsoft", "WinGet", "Links", "stockfish.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def get_voice_source(force_text: bool, script: Optional[str]):
    """Return (voice, kind). Falls back to TextVoice if live audio can't start.

    Mirrors chess_ai.get_speaker(): a missing Vosk model or missing
    vosk/sounddevice install must never crash the game -- log and degrade.
    """
    script_list = script.split(",") if script else None
    if force_text:
        return TextVoice(script=script_list), "text"

    try:
        from voice_matching import VoiceMatchEngine
        return VoiceMatchEngine(), "live"
    except Exception as e:
        print(f"[info] Live voice unavailable ({e.__class__.__name__}: {e})")
        print("[info] Falling back to text-input mode.\n")
        return TextVoice(script=script_list), "text"


def main() -> None:
    p = argparse.ArgumentParser(description="VoiceChess - voice-controlled chess robot brain.")
    p.add_argument("--text", action="store_true", help="force text mode (no mic/Vosk needed)")
    p.add_argument("--script", default=None, help="comma-separated utterances for text mode")
    p.add_argument("--turns", type=int, default=40, help="max turns (demo safety limit)")
    p.add_argument("--tts", default="print", choices=["print", "espeak", "pyttsx3", "piper"],
                   help="audio backend (default: print only)")
    p.add_argument("--skill", type=int, default=5, help="Stockfish skill level 0-20")
    p.add_argument("--think", type=float, default=0.5, help="Stockfish seconds per move")
    p.add_argument("--serial", default=None,
                   help="ESP32 serial port for REAL G-code output (e.g. /dev/ttyUSB0, "
                        "COM5). Omit for dry-run (prints the G-code instead of sending).")
    p.add_argument("--baud", type=int, default=115200,
                   help="serial baud rate (FluidNC default 115200)")
    args = p.parse_args()

    # Fail loudly NOW, not mid-game: the AI turn cannot work without Stockfish.
    stockfish = find_stockfish()
    if not stockfish:
        sys.exit(
            "ERROR: Stockfish binary not found.\n"
            "  Raspberry Pi / Debian:  sudo apt install stockfish\n"
            "  Windows dev box:        winget install --id Stockfish.Stockfish -e\n"
            "                          (or drop stockfish.exe in the repo root)\n"
        )

    engine = ChessEngine(stockfish_path=stockfish, skill_level=args.skill,
                         think_time=args.think)
    engine.speaker = get_speaker(args.tts)
    voice, kind = get_voice_source(args.text, args.script)

    serial = SerialLink(port=args.serial, baud=args.baud)
    wire = f"serial: {args.serial}@{args.baud}" if args.serial else "serial: dry-run (print)"
    print(f"=== VoiceChess ({kind} voice, stockfish: {stockfish}, {wire}) ===")
    print("Human = White, AI (Stockfish) = Black.")

    orch = Orchestrator(engine=engine, voice=voice,
                        planner=MotionPlanner(), serial=serial)
    try:
        orch.run(max_turns=args.turns)
    finally:
        serial.close()


if __name__ == "__main__":
    main()
