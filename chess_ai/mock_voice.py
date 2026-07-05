"""A stand-in for Role 1 (Voice & Phonetic Matching).

It implements the *exact same interface* Role 1's VoiceMatchEngine exposes:

    listen_for_move(legal_moves, pieces=None, ...) -> MatchResult

...but with no microphone, no Vosk model and no audio -- it just picks from the
legal moves. That lets the full game loop run and be tested anywhere (this
sandbox, CI, your laptop), and documents precisely what Role 1 must deliver.

MatchResult (from voice_matching) is the three-way contract:
    status "legal"        -> move is in legal_moves
    status "illegal"      -> a well-formed move that is NOT legal right now
    status "unrecognized" -> nothing move-shaped was understood

(`pieces` maps each UCI move to the spoken word of the piece that moves, so the
real matcher can reject utterances naming the wrong piece; the mock ignores it.)

Modes:
    MockVoice()                       -> random legal move each turn
    MockVoice(script=["e2e4","g1f3"]) -> plays the scripted moves in order
                                         (a well-formed scripted move that is
                                         not legal is reported as "illegal")
    MockVoice(unrecognized_every=3)   -> returns "unrecognized" on every Nth call
                                         (to exercise the retry path)
"""

from __future__ import annotations

import random
import re
from typing import Dict, List, Optional

_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


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
                        pieces: Optional[Dict[str, str]] = None,
                        require_confirmation: Optional[bool] = None,
                        on_prompt=None):
        """Same signature/return contract as VoiceMatchEngine.listen_for_move."""
        # Imported lazily: a type-only dependency on Role 1's contract, kept
        # out of module import so `import chess_ai` stays standalone-fast.
        from voice_matching.engine import MatchResult

        self._calls += 1

        if self.unrecognized_every and self._calls % self.unrecognized_every == 0:
            return MatchResult("unrecognized")

        if not legal_moves:
            return MatchResult("unrecognized")

        if self.script:
            move = self.script.pop(0)
            if move in legal_moves:
                return MatchResult("legal", move)
            # Stay honest: a well-formed scripted move that isn't legal is
            # exactly the "illegal" case; garbage is "unrecognized".
            if _UCI_RE.match(move):
                return MatchResult("illegal", move)
            return MatchResult("unrecognized")

        return MatchResult("legal", self._rng.choice(legal_moves))
