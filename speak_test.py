#!/usr/bin/env python3
"""Standalone speech test - run this FIRST on the Pi to confirm audio works.

    python3 speak_test.py                # tries espeak, then falls back to print
    python3 speak_test.py piper          # tries Piper (needs PIPER_MODEL set)

If you hear the sentence, TTS is working and the full game will talk too.
If it only prints [SPEAK] ..., the backend isn't set up yet -- the message
tells you what's missing.
"""

import sys
from chess_ai import get_speaker

kind = sys.argv[1] if len(sys.argv) > 1 else "espeak"

print(f"Testing '{kind}' speech backend...")
speaker = get_speaker(kind)
for line in [
    "New game. You are White. Your move.",
    "You said pawn to e4.",
    "A I plays knight to f6.",
    "Checkmate. Black wins.",
]:
    print(f"  saying: {line}")
    speaker.say(line)

print("Done. If you heard those lines, audio is working.")
