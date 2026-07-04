"""A stand-in for Role 1 (Voice & Phonetic Matching).

It implements the *exact same interface* your friend's VoiceMatchEngine exposes:

    listen_for_move(legal_moves: list[str], pieces: dict[str, str] | None = None)
        -> str   # a UCI move, or "unrecognized"

(`pieces` maps each UCI move to the spoken word of the piece that moves, so the
real matcher can reject utterances naming the wrong piece; the mock ignores it.)

...but with no microphone, no Vosk model and no audio -- it just picks from the
legal moves. That lets the full game loop run and be tested anywhere (this
sandbox, CI, your laptop), and documents precisely what Role 1 must deliver.

Modes:
    MockVoice()                       -> random legal move each turn
    MockVoice(script=["e2e4","g1f3"]) -> plays the scripted moves in order
    MockVoice(unrecognized_every=3)   -> returns "unrecognized" on every Nth call
                                         (to exercise the retry path)
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional


class MockVoice:
    def __init__(
        self,
        script: Optional[List[str]] = None,
        unrecognized_every: int = 0,
        seed: Optional[int] = None,
    ) -> None:
        self.script = list(script) if script else None
        self.unrecognized_every = unrecognized_every
        self._calls = 0
        self._rng = random.Random(seed)

    def listen_for_move(self, legal_moves: List[str],
                        pieces: Optional[Dict[str, str]] = None) -> str:
        """Same signature/return contract as VoiceMatchEngine.listen_for_move."""
        self._calls += 1

        if self.unrecognized_every and self._calls % self.unrecognized_every == 0:
            return "unrecognized"

        if not legal_moves:
            return "unrecognized"

        if self.script:
            move = self.script.pop(0)
            # Stay honest: if a scripted move is not actually legal, report
            # unrecognized rather than smuggling an illegal move downstream.
            return move if move in legal_moves else "unrecognized"

        return self._rng.choice(legal_moves)
