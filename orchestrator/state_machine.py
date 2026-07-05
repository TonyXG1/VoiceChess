"""Role 5 - the turn state machine (ported from the old orchestrator_demo.py).

    LISTEN -> PARSE -> CONFIRM -> MOVE(human) -> THINK(AI) -> MOVE(AI) -> LISTEN

Rules this module enforces:
  * The orchestrator is the ONLY thing that calls other modules. Role 1
    (voice) and Role 2 (chess_ai) never call each other; every result comes
    back here before the next call goes out.
  * Audio capture happens only in the LISTEN state -- never during MOVE/THINK
    (voice.listen_for_move is only ever called at the top of the human turn).
  * The UCI string is the only representation crossing module boundaries.

The voice source needs one method:
    listen_for_move(legal_moves, pieces, on_prompt) -> MatchResult | None
so VoiceMatchEngine, MockVoice, and main.py's TextVoice are interchangeable.
MatchResult.status is "legal" | "illegal" | "unrecognized"; None means the
voice source is exhausted (scripted input ran out / user exit) and ends the
game loop cleanly.
"""

from __future__ import annotations

from typing import List, Tuple

import chess

from chess_ai import ChessEngine
from motion import MotionPlanner
from voice_matching.phonetics import describe_move
from .serial_link import SerialLink


class Orchestrator:
    def __init__(self, engine: ChessEngine, voice, planner: MotionPlanner,
                 serial: SerialLink) -> None:
        self.engine = engine
        self.voice = voice
        self.planner = planner
        self.serial = serial

    # ------------------------------ MOVE state ------------------------------ #

    def _classify_move(self, uci: str) -> Tuple[str, bool]:
        """(move_type, is_capture) for Role 3's planner, in its vocabulary.

        Must run BEFORE the move is applied -- castling/en-passant/capture are
        properties of the move in the CURRENT position. Read-only query
        against Role 2's board (the documented source of truth); en passant
        in particular cannot be derived from the post-move SAN/UCI alone.
        """
        try:
            move = chess.Move.from_uci(uci)
            board = self.engine.board
            if board.is_castling(move):
                return "castling", False
            if board.is_en_passant(move):
                return "en_passant", True
            if move.promotion:
                return "promotion", board.is_capture(move)
            return "standard", board.is_capture(move)
        except ValueError:
            # Malformed UCI: apply() will reject it; flags are never used.
            return "standard", False

    def _execute_motion(self, uci: str, move_type: str, is_capture: bool) -> None:
        """Physically execute an applied move: plan G-code, stream it, wait.

        Used for BOTH the human and the AI move -- the gantry moves the
        pieces for both sides.
        """
        gcode = self.planner.plan(uci, move_type=move_type, is_capture=is_capture)
        # Role 3 returns one newline-joined G-code string; the serial link
        # streams line by line.
        self.serial.send(gcode.splitlines())  # blocks until motion done (stub: prints)

    # ------------------------------ game loop ------------------------------- #

    def run(self, max_turns: int = 40) -> str:
        """Play a full game (human = White, AI = Black). Returns the result text."""
        eng = self.engine
        eng.speak("New game. You are White. Your move.")

        turn = 0
        while turn < max_turns:
            turn += 1

            if eng.status().is_game_over:
                break

            # ============================ HUMAN TURN ============================ #
            # LISTEN: hand the live legal-move list to Role 1 and wait for speech.
            # The piece map lets Role 1 reject utterances naming the wrong piece
            # ("knight to g3" must not match the pawn move g2g3).
            legal: List[str] = eng.legal_moves()
            pieces = eng.legal_moves_pieces()
            print(f"\n--- Turn {turn} (White) --- {len(legal)} legal moves")
            # Role 1's confirmation prompts ("I understood ... say yes/no")
            # are routed through Role 2's speaker so they are spoken aloud.
            result = self.voice.listen_for_move(legal, pieces, on_prompt=eng.speak)

            if result is None:
                print("[orchestrator] voice source exhausted / quit - ending game.")
                break

            # PARSE: Role 1 classified the utterance -- react per status.
            if result.status == "unrecognized":
                eng.speak("I didn't catch that. Please say your move again.")
                turn -= 1                     # stay in LISTEN; don't burn a turn
                continue

            if result.status == "illegal":
                # Heard clearly, but the move can't be played right now. Name
                # it (square-based wording -- SAN doesn't exist for illegal
                # moves) so the player knows they were understood.
                eng.speak(f"That move, {describe_move(result.move)}, "
                          f"isn't legal right now. Try again.")
                turn -= 1                     # stay in LISTEN
                continue

            uci = result.move

            # Classify for motion BEFORE applying (needs the pre-move board).
            move_type, is_capture = self._classify_move(uci)

            # Apply via Role 2 (rules authority; nothing illegal passes).
            res = eng.apply(uci)
            if not res.ok:
                eng.speak(f"That move isn't legal. {res.error}")
                turn -= 1
                continue

            # CONFIRM: read the move back to the player.
            eng.speak(f"You said {res.readback}")

            # MOVE(human): Role 3 plans, Role 4 executes. No listening here.
            self._execute_motion(res.uci, move_type, is_capture)

            if res.status.is_game_over:
                break

            # ============================= AI TURN ============================== #
            # THINK: ask Stockfish (does not apply yet).
            ai_uci = eng.ai_move()
            ai_move_type, ai_is_capture = self._classify_move(ai_uci)
            ai_res = eng.apply(ai_uci)
            eng.speak(f"A I plays {ai_res.readback}")

            # MOVE(AI): same plan -> stream path as the human move.
            self._execute_motion(ai_res.uci, ai_move_type, ai_is_capture)

            if ai_res.status.is_game_over:
                break

        # ----------------------------- announce ----------------------------- #
        st = eng.status()
        if st.is_game_over:
            if st.reason == "checkmate":
                winner = "White" if st.result == "1-0" else "Black"
                msg = f"Checkmate. {winner} wins."
            elif st.is_draw:
                msg = f"Draw by {st.reason.replace('_', ' ')}."
            else:
                msg = f"Game over. Result {st.result}."
        else:
            msg = f"Stopped after {turn} turns."
        print(f"\n=== {msg} ===")
        eng.speak(msg)
        eng.close()
        return msg
