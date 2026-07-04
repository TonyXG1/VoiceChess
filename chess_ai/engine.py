"""Role 2 - Chess Engine, AI Opponent & (audio hook).

This is the rules authority and AI opponent for the VoiceChess robot.

It owns the single source of truth for the game (the board), produces the list
of legal moves that Role 1 (voice matching) needs every turn, validates and
applies moves, asks Stockfish for the AI reply, and reports game state
(check / checkmate / stalemate / draw).

Design contract (see interfaces.md):
  * Every move in and out is a UCI string, e.g. "e2e4", "e7e8q", "e1g1".
  * legal_moves() is recomputed live from the board -- call it after every
    change and hand the result to Role 1.
  * This module never talks to other roles directly. The Orchestrator (Role 5)
    calls it. It is a plain in-process Python object.

Nothing here needs a screen, a mic, or a network. It is pure and unit-testable.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import List, Optional, Dict

import chess
import chess.engine


# Plain dataclasses so any role can read a result without importing python-chess.

@dataclass
class GameStatus:
    """Snapshot of the game after the most recent move."""
    turn: str                       # "white" or "black" -- whose move it is now
    in_check: bool                  # side to move is in check
    is_checkmate: bool
    is_stalemate: bool
    is_draw: bool                   # any drawing condition below is true
    is_game_over: bool
    result: Optional[str]           # "1-0", "0-1", "1/2-1/2", or None if ongoing
    reason: Optional[str]           # "checkmate", "stalemate", "insufficient_material",
                                    # "seventyfive_moves", "fivefold_repetition", or None
    fullmove_number: int

    def as_dict(self) -> Dict:
        return self.__dict__.copy()


@dataclass
class MoveResult:
    """Result of trying to apply a move."""
    ok: bool                        # False means the move was rejected (illegal)
    uci: str                        # the move as given
    san: str = ""                   # human-readable, e.g. "e4", "Nf3", "O-O"
    readback: str = ""              # spoken phrase, e.g. "Pawn to e4, check"
    error: Optional[str] = None     # why it was rejected, if ok is False
    status: Optional[GameStatus] = None

    def as_dict(self) -> Dict:
        d = self.__dict__.copy()
        d["status"] = self.status.as_dict() if self.status else None
        return d


# Map python-chess piece letters to spoken words for the read-back.
_PIECE_WORDS = {
    "P": "Pawn", "N": "Knight", "B": "Bishop",
    "R": "Rook", "Q": "Queen", "K": "King",
}
_PROMO_WORDS = {"q": "queen", "r": "rook", "b": "bishop", "n": "knight"}


class ChessEngine:
    """The rules authority and AI opponent (Role 2).

    Typical use by the Orchestrator (Role 5):

        eng = ChessEngine()                    # human is White by default
        legal = eng.legal_moves()              # -> ["e2e4", "g1f3", ...]  (give to Role 1)
        res = eng.apply("e2e4")                # validate + apply the human move
        if res.ok and not res.status.is_game_over:
            ai = eng.ai_move()                 # -> "e7e5"   (Stockfish, does NOT apply)
            eng.apply(ai)                      # apply the AI reply

    The Stockfish subprocess is opened lazily on the first ai_move() call and
    reused. Call close() (or use the object as a context manager) to shut it
    down cleanly.
    """

    def __init__(
        self,
        stockfish_path: Optional[str] = None,
        skill_level: int = 5,
        think_time: float = 0.5,
        start_fen: Optional[str] = None,
    ) -> None:
        """
        Args:
            stockfish_path: Path to the Stockfish binary. If None, common
                locations are auto-detected (PATH, /usr/games/stockfish, ...).
            skill_level: Stockfish "Skill Level" 0-20. Lower = weaker/faster,
                good for a friendly demo. Default 5.
            think_time: Seconds Stockfish is allowed to think per move.
            start_fen: Optional FEN to start from (otherwise the normal setup).
        """
        self.board = chess.Board(start_fen) if start_fen else chess.Board()
        self.skill_level = skill_level
        self.think_time = think_time
        self._stockfish_path = stockfish_path or self._find_stockfish()
        self._engine: Optional[chess.engine.SimpleEngine] = None  # opened lazily
        # Audio output hook. Defaults to a no-audio print speaker; the
        # Orchestrator can swap in real TTS with:  eng.speaker = get_speaker("pyttsx3")
        from .speech import get_speaker
        self.speaker = get_speaker("print")

    # --- rules / board (pure python-chess, no subprocess) ---

    def legal_moves(self) -> List[str]:
        """Every legal move at the current position, as UCI strings.

        THIS is the list you hand to Role 1 every turn. Recompute it after
        each applied move -- the board is the single source of truth.
        """
        return [m.uci() for m in self.board.legal_moves]

    def legal_moves_pieces(self) -> Dict[str, str]:
        """Map of each legal UCI move -> spoken word for the piece that moves.

        e.g. {"e2e4": "pawn", "g1f3": "knight", ...}. Hand this to Role 1
        alongside legal_moves() so phrase matching only accepts the piece name
        that is actually moving (saying "knight to g3" must never match the
        pawn move g2g3).
        """
        out: Dict[str, str] = {}
        for m in self.board.legal_moves:
            piece = self.board.piece_at(m.from_square)
            word = _PIECE_WORDS.get(piece.symbol().upper(), "piece") if piece else "piece"
            out[m.uci()] = word.lower()
        return out

    def legal_moves_san(self) -> List[Dict[str, str]]:
        """Same list, but each item is {"uci": ..., "san": ...}.

        Useful when a role wants the human-readable form too (read-backs,
        logging, a debug UI).
        """
        out = []
        for m in self.board.legal_moves:
            out.append({"uci": m.uci(), "san": self.board.san(m)})
        return out

    def is_legal(self, uci: str) -> bool:
        """True if `uci` is a legal move right now. Never raises."""
        try:
            return chess.Move.from_uci(uci) in self.board.legal_moves
        except (ValueError, chess.InvalidMoveError):
            return False

    def apply(self, uci: str) -> MoveResult:
        """Validate and apply a move. This is how BOTH sides move.

        Returns a MoveResult. If the move is illegal or malformed, ok is False,
        error explains why, and the board is left unchanged. This is the "when
        something is impossible" guard -- nothing bad reaches the robot arm.
        """
        # Parse guard: is the string even a well-formed UCI move?
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError, chess.InvalidMoveError):
            return MoveResult(ok=False, uci=uci,
                              error=f"'{uci}' is not a valid UCI move string.",
                              status=self.status())

        # Legality guard: is it legal in THIS position?
        if move not in self.board.legal_moves:
            return MoveResult(ok=False, uci=uci,
                              error=f"'{uci}' is not legal in the current position.",
                              status=self.status())

        # Build the human-readable forms BEFORE pushing (need pre-move board).
        san = self.board.san(move)
        readback = self._readback(move)

        self.board.push(move)
        status = self.status()

        # Append check / checkmate to the spoken read-back after the move.
        if status.is_checkmate:
            readback += ", checkmate"
        elif status.in_check:
            readback += ", check"

        return MoveResult(ok=True, uci=uci, san=san, readback=readback, status=status)

    def status(self) -> GameStatus:
        """Current game state: whose turn, check/mate/draw, result if over."""
        b = self.board
        result = None
        reason = None
        if b.is_checkmate():
            reason = "checkmate"
            result = b.result()
        elif b.is_stalemate():
            reason = "stalemate"
            result = "1/2-1/2"
        elif b.is_insufficient_material():
            reason = "insufficient_material"
            result = "1/2-1/2"
        elif b.is_seventyfive_moves():
            reason = "seventyfive_moves"
            result = "1/2-1/2"
        elif b.is_fivefold_repetition():
            reason = "fivefold_repetition"
            result = "1/2-1/2"

        is_draw = reason in {
            "stalemate", "insufficient_material",
            "seventyfive_moves", "fivefold_repetition",
        }
        return GameStatus(
            turn="white" if b.turn == chess.WHITE else "black",
            in_check=b.is_check(),
            is_checkmate=b.is_checkmate(),
            is_stalemate=b.is_stalemate(),
            is_draw=is_draw,
            is_game_over=b.is_game_over(),
            result=result,
            reason=reason,
            fullmove_number=b.fullmove_number,
        )

    def fen(self) -> str:
        """Current position as a FEN string (portable board snapshot)."""
        return self.board.fen()

    def set_fen(self, fen: str) -> None:
        self.board.set_fen(fen)

    def reset(self, start_fen: Optional[str] = None) -> None:
        """Start a new game."""
        self.board = chess.Board(start_fen) if start_fen else chess.Board()

    def undo(self) -> Optional[str]:
        """Take back the last move. Returns its UCI, or None if none to undo."""
        if not self.board.move_stack:
            return None
        return self.board.pop().uci()

    def describe(self, uci: str) -> str:
        """Spoken read-back for a move WITHOUT applying it.

        e.g. "Pawn to e4", "Knight takes f3", "Kingside castle",
             "Pawn to e8, promote to queen".
        Feed this straight to speak() / TTS.
        """
        try:
            move = chess.Move.from_uci(uci)
        except (ValueError, chess.InvalidMoveError):
            return "unrecognized move"
        return self._readback(move)

    # --- AI opponent (Stockfish) ---

    def ai_move(self) -> str:
        """Stockfish's chosen reply at the current position, as UCI.

        Does NOT apply the move -- the Orchestrator applies it in a later step
        (so the human read-back / robot motion for the human move can finish
        first). Call apply() with the returned string when ready.

        Raises RuntimeError if no Stockfish binary is available.
        """
        if self.board.is_game_over():
            raise RuntimeError("Game is already over; no AI move to make.")
        eng = self._ensure_engine()
        result = eng.play(self.board, chess.engine.Limit(time=self.think_time))
        if result.move is None:
            raise RuntimeError("Stockfish returned no move.")
        return result.move.uci()

    # --- audio output ---

    def speak(self, text: str) -> None:
        """Speak a string through the attached Speaker (Role 2's voice).

        The Orchestrator decides WHEN this is called; Role 2 just does it.
        Default speaker only prints; swap in real TTS via `self.speaker`.
        """
        self.speaker.say(text)

    def close(self) -> None:
        """Shut down the Stockfish subprocess if it is running."""
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
            self._engine = None

    def __enter__(self) -> "ChessEngine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- internals ---

    @staticmethod
    def _find_stockfish() -> Optional[str]:
        found = shutil.which("stockfish")
        if found:
            return found
        for path in ("/usr/games/stockfish", "/usr/bin/stockfish",
                     "/usr/local/bin/stockfish", "/opt/homebrew/bin/stockfish"):
            if os.path.isfile(path):
                return path
        return None

    def _ensure_engine(self) -> chess.engine.SimpleEngine:
        if self._engine is not None:
            return self._engine
        if not self._stockfish_path:
            raise RuntimeError(
                "No Stockfish binary found. Install it (e.g. 'apt install stockfish' "
                "or 'brew install stockfish') or pass stockfish_path=... to ChessEngine."
            )
        self._engine = chess.engine.SimpleEngine.popen_uci(self._stockfish_path)
        # Clamp strength for a friendly demo. Skill Level is 0..20.
        try:
            self._engine.configure({"Skill Level": int(self.skill_level)})
        except Exception:
            pass  # some builds name it differently; ignore if unsupported
        return self._engine

    def _readback(self, move: chess.Move) -> str:
        """Build a natural spoken phrase for a move using the PRE-move board."""
        b = self.board
        if b.is_kingside_castling(move):
            return "Kingside castle"
        if b.is_queenside_castling(move):
            return "Queenside castle"

        piece = b.piece_at(move.from_square)
        piece_word = _PIECE_WORDS.get(piece.symbol().upper(), "Piece") if piece else "Piece"
        dest = chess.square_name(move.to_square)
        verb = "takes" if b.is_capture(move) else "to"

        phrase = f"{piece_word} {verb} {dest}"
        if move.promotion:
            promo = chess.piece_symbol(move.promotion)
            phrase += f", promote to {_PROMO_WORDS.get(promo, promo)}"
        return phrase
