"""Role 5 - the turn state machine (ported from the old orchestrator_demo.py).

    LISTEN -> PARSE -> CONFIRM -> MOVE(human) -> THINK(AI) -> MOVE(AI) -> LISTEN

Rules this module enforces:
  * The orchestrator is the ONLY thing that calls other modules. Role 1
    (voice) and Role 2 (chess_ai) never call each other; every result comes
    back here before the next call goes out.
  * Audio capture happens only in the LISTEN state -- never during MOVE/THINK
    (voice.listen_for_move is only ever called at the top of the human turn).
  * The UCI string is the only representation crossing module boundaries.

The voice source needs one method:  listen_for_move(legal_moves) -> uci | "unrecognized"
so VoiceMatchEngine, MockVoice, and main.py's TextVoice are interchangeable.
A voice source may also return "quit" (scripted input exhausted / user exit)
to end the game loop cleanly.
"""

from __future__ import annotations

from typing import List

from chess_ai import ChessEngine, MoveResult
from motion import MotionPlanner
from .serial_link import SerialLink


class Orchestrator:
    def __init__(self, engine: ChessEngine, voice, planner: MotionPlanner,
                 serial: SerialLink) -> None:
        self.engine = engine
        self.voice = voice
        self.planner = planner
        self.serial = serial

    # ------------------------------ MOVE state ------------------------------ #

    def _execute_motion(self, res: MoveResult) -> None:
        """Physically execute an applied move: plan G-code, stream it, wait.

        Used for BOTH the human and the AI move -- the gantry moves the
        pieces for both sides.
        """
        uci = res.uci
        special = None
        if res.san.startswith("O-O"):
            special = "castle"
        elif len(uci) == 5:
            special = "promotion"
        # NOTE: en passant currently reads as a plain capture ("exd6"); when
        # Role 3's real planner lands, chess_ai needs to expose an en-passant
        # flag so the planner clears the right square.
        capture = "x" in res.san

        gcode = self.planner.plan(uci[:2], uci[2:4], capture=capture, special=special)
        self.serial.send(gcode)  # blocks until motion done (stub: prints)

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
            uci = self.voice.listen_for_move(legal, pieces)

            if uci == "quit":
                print("[orchestrator] voice source exhausted / quit - ending game.")
                break

            # PARSE: Role 1 already matched to a legal move, or gave up.
            if uci == "unrecognized":
                eng.speak("I didn't catch that. Please say your move again.")
                turn -= 1                     # stay in LISTEN; don't burn a turn
                continue

            # Apply via Role 2 (rules authority; nothing illegal passes).
            res = eng.apply(uci)
            if not res.ok:
                eng.speak(f"That move isn't legal. {res.error}")
                turn -= 1
                continue

            # CONFIRM: read the move back to the player.
            eng.speak(f"You said {res.readback}")

            # MOVE(human): Role 3 plans, Role 4 executes. No listening here.
            self._execute_motion(res)

            if res.status.is_game_over:
                break

            # ============================= AI TURN ============================== #
            # THINK: ask Stockfish (does not apply yet).
            ai_uci = eng.ai_move()
            ai_res = eng.apply(ai_uci)
            eng.speak(f"A I plays {ai_res.readback}")

            # MOVE(AI): same plan -> stream path as the human move.
            self._execute_motion(ai_res)

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
