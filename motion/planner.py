"""Role 3 - motion planning: chess square -> physical XY mm -> G-code.

``plan()`` returns ONE newline-joined G-code string; the orchestrator splits it
into lines for the serial link. Every physical constant lives in
:mod:`motion.config` -- nothing here should need editing to calibrate the build.

The G-code dialect is GRBL/FluidNC, which differs from Marlin in ways that bite:

  * ``G4 P`` is in **seconds**, not milliseconds.
  * ``G0`` rapids ignore the ``F`` word; feed comes from the YAML's max rate.
    Only ``G1`` carries a feed, and setting F on a G0 just mutates the modal
    feed for the next G1.
  * The claw is the **A axis** (an rc_servo), not a spindle. ``G0 A45`` closes
    it. Because it is an axis, it queues in move order with XY/Z for free --
    an ``M3``-style spindle command would need explicit synchronisation.

Safety invariants every emitted sequence upholds:

  1. The claw only ever crosses the board at ``Z_SAFE`` (clear of the 95 mm king).
  2. It only descends to ``Z_GRIP`` directly over the square it is acting on.
  3. A loaded carry uses ``Z_CARRY_LOW`` only when chess rules guarantee the
     path is empty; otherwise ``Z_CARRY_HIGH``. See ``plan``'s docstring.
"""

from __future__ import annotations

from typing import List, Tuple

from . import config as cfg

_FILES = "abcdefgh"
_RANKS = "12345678"


def _f(value: float) -> str:
    """Format a coordinate. Fixed 2dp keeps the output diffable and unambiguous."""
    return f"{value:.2f}"


class MotionPlanner:
    """Stateless per move, except for which graveyard/reserve slot is next."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Start a fresh game: rewind the graveyard and queen-reserve slots."""
        self._graveyard_slot = 0
        self._queen_slot = {"white": 0, "black": 0}

    # ---------------------------- coordinates ------------------------------ #

    def square_to_coords(self, square: str) -> Tuple[float, float]:
        """Chess notation (e.g. 'e2') -> the machine XY of that square's center."""
        # --- STRICT HARDWARE SAFETY VALIDATION --- #
        if not isinstance(square, str) or len(square) != 2:
            raise ValueError(f"Square '{square}' is invalid!")

        col_char = square[0].lower()
        row_char = square[1]

        if col_char not in _FILES or row_char not in _RANKS:
            raise ValueError(
                f"Square '{square}' is physically out of bounds (a-h, 1-8)!"
            )
        # ----------------------------------------- #

        col_idx = _FILES.index(col_char)
        row_idx = _RANKS.index(row_char)

        # +0.5 puts the claw on the square's CENTER, not its corner.
        x = cfg.BOARD_ORIGIN_X + (col_idx + 0.5) * cfg.SQUARE_X
        y = cfg.BOARD_ORIGIN_Y + (row_idx + 0.5) * cfg.SQUARE_Y
        return self._checked(round(x, 2), round(y, 2), square)

    def _checked(self, x: float, y: float, what: str) -> Tuple[float, float]:
        """Reject anything outside the soft-limit envelope before it is emitted."""
        if not (0.0 <= x <= cfg.X_MAX and 0.0 <= y <= cfg.Y_MAX):
            raise ValueError(
                f"'{what}' maps to X{x} Y{y}, outside the machine envelope "
                f"(0-{cfg.X_MAX} x 0-{cfg.Y_MAX}). Check BOARD_ORIGIN_X/Y."
            )
        return x, y

    def _graveyard_coords(self) -> Tuple[float, float]:
        """Claim the next free graveyard slot. Pieces land in a grid, not a pile."""
        i = self._graveyard_slot
        if i >= cfg.GRAVEYARD_COLS * cfg.GRAVEYARD_ROWS:
            raise ValueError(
                f"Graveyard is full ({i} pieces). Call planner.reset() between games."
            )
        self._graveyard_slot += 1
        x = cfg.GRAVEYARD_X0 + (i % cfg.GRAVEYARD_COLS) * cfg.GRAVEYARD_DX
        y = cfg.GRAVEYARD_Y0 + (i // cfg.GRAVEYARD_COLS) * cfg.GRAVEYARD_DY
        return self._checked(round(x, 2), round(y, 2), f"graveyard slot {i}")

    def _queen_reserve_coords(self, colour: str) -> Tuple[float, float]:
        """Claim the next spare queen of the given colour ('white' | 'black')."""
        i = self._queen_slot[colour]
        if i >= cfg.QUEEN_RESERVE_SLOTS:
            raise ValueError(
                f"Out of spare {colour} queens ({cfg.QUEEN_RESERVE_SLOTS} in "
                f"reserve). Restock the reserve row or reset the planner."
            )
        self._queen_slot[colour] += 1
        x = cfg.QUEEN_RESERVE_X0 + i * cfg.QUEEN_RESERVE_DX
        y = (cfg.QUEEN_RESERVE_Y_WHITE if colour == "white"
             else cfg.QUEEN_RESERVE_Y_BLACK)
        return self._checked(round(x, 2), round(y, 2), f"{colour} queen {i}")

    # ------------------------------ primitives ------------------------------ #

    def startup(self) -> str:
        """One-time preamble, sent before the first move of a game.

        Homing (``$H``) is deliberately NOT here: it is a ``$`` command, not
        G-code, so the serial link owns it.
        """
        return "\n".join([
            "; --- STARTUP ---",
            "G21 ; millimetres",
            "G90 ; absolute positioning",
            "G94 ; feed rate is mm/min",
            f"G0 A{_f(cfg.CLAW_OPEN_A)} ; open claw",
            f"G0 Z{_f(cfg.Z_TOP)} ; retract Z",
        ])

    def _transfer(self, start: Tuple[float, float], end: Tuple[float, float],
                  start_label: str, end_label: str,
                  high_lift: bool, reason: str = "") -> List[str]:
        """Pick a piece up at ``start`` and set it down at ``end``.

        The one primitive every operation is built from. ``high_lift`` selects
        the carry height: high clears the tallest piece, low only clears the
        board surface.
        """
        carry_z = cfg.Z_CARRY_HIGH if high_lift else cfg.Z_CARRY_LOW
        note = f" ({reason})" if reason else ""
        return [
            f"G0 Z{_f(cfg.Z_SAFE)} ; raise to safe height",
            f"G0 X{_f(start[0])} Y{_f(start[1])} ; above {start_label}",
            f"G0 Z{_f(cfg.Z_GRIP)} ; descend to grip",
            f"G0 A{_f(cfg.CLAW_CLOSED_A)} ; close claw",
            f"G4 P{cfg.CLAW_DWELL_S} ; let the servo settle",
            f"G0 Z{_f(carry_z)} ; {'high' if high_lift else 'low'} carry{note}",
            f"G1 X{_f(end[0])} Y{_f(end[1])} F{cfg.F_CARRY:.0f} ; carry to {end_label}",
            f"G0 Z{_f(cfg.Z_GRIP)} ; descend to place",
            f"G0 A{_f(cfg.CLAW_OPEN_A)} ; open claw",
            f"G4 P{cfg.CLAW_DWELL_S} ; let the servo settle",
            f"G0 Z{_f(cfg.Z_SAFE)} ; retract",
        ]

    def _to_graveyard(self, square: str) -> List[str]:
        """Clear a captured piece off ``square``. Always a high lift -- the trip
        to the off-board grid crosses occupied territory."""
        grave = self._graveyard_coords()
        return self._transfer(
            self.square_to_coords(square), grave,
            square, f"graveyard slot {self._graveyard_slot - 1}",
            high_lift=True, reason="crossing the board",
        )

    def _park(self) -> List[str]:
        """Clear the gantry out of the player's view. Z is already at Z_SAFE."""
        return [f"G0 X{_f(cfg.PARK_X)} Y{_f(cfg.PARK_Y)} ; park"]

    @staticmethod
    def _dedupe(lines: List[str]) -> List[str]:
        """Drop a command that repeats the one before it verbatim.

        Chaining two transfers emits "retract to Z_SAFE" then "raise to
        Z_SAFE" back to back. In absolute mode (G90) that second move is a
        no-op, but it still costs a serial round-trip and clutters the trace.
        Only exact repeats of the code part are removed, so nothing that
        actually moves the machine is ever dropped.
        """
        out: List[str] = []
        prev_code = None
        for line in lines:
            code = line.split(";", 1)[0].strip()
            if code and code == prev_code:
                continue
            out.append(line)
            if code:
                prev_code = code
        return out

    # -------------------------------- plan ---------------------------------- #

    def plan(self, uci_move: str, move_type: str = "standard",
             is_capture: bool = False, high_lift: bool = False) -> str:
        """Plan one applied move. Returns a newline-joined G-code string.

        ``move_type`` is "standard" | "castling" | "en_passant" | "promotion",
        and ``is_capture`` / ``high_lift`` are derived by the orchestrator from
        Role 2's board BEFORE the move is applied.

        ``high_lift`` means "this piece jumps over others" -- i.e. it is a
        knight. Every other piece slides, and chess rules guarantee its path is
        empty, so it can be carried low and fast. The planner adds a high lift
        of its own for the three cases that are unconditionally unsafe:
        graveyard trips, the queen-reserve trip, and castling's rook leg (the
        rook passes through the square the king has just landed on -- and
        moving the rook first only swaps which piece is in the other's way).
        """
        move_from = uci_move[:2]
        move_to = uci_move[2:4]
        promo_piece = uci_move[4:5].lower()
        out: List[str] = []

        # 1. CASTLING
        if move_type == "castling":
            out.append("; --- CASTLING ---")
            # King first. It slides along the back rank, which castling rules
            # require to be empty, so a low carry is safe.
            out.extend(self._transfer(
                self.square_to_coords(move_from), self.square_to_coords(move_to),
                move_from, move_to, high_lift=False,
            ))

            rook_squares = {
                "g1": ("h1", "f1"), "c1": ("a1", "d1"),
                "g8": ("h8", "f8"), "c8": ("a8", "d8"),
            }
            if move_to not in rook_squares:
                raise ValueError(f"Invalid castling target '{move_to}'!")
            rook_from, rook_to = rook_squares[move_to]

            out.extend(self._transfer(
                self.square_to_coords(rook_from), self.square_to_coords(rook_to),
                rook_from, rook_to, high_lift=True,
                reason="rook passes the king",
            ))
            out.extend(self._park())
            return "\n".join(self._dedupe(out))

        # 2. EN PASSANT
        if move_type == "en_passant":
            out.append("; --- EN PASSANT CAPTURE ---")
            # The captured pawn sits on the destination's FILE but the origin's RANK.
            captured_sq = move_to[0] + move_from[1]
            out.append(f"; captured pawn is on {captured_sq}, not {move_to}")
            out.extend(self._to_graveyard(captured_sq))
            out.append(f"; --- PLAYER MOVE: {move_from} -> {move_to} ---")
            out.extend(self._transfer(
                self.square_to_coords(move_from), self.square_to_coords(move_to),
                move_from, move_to, high_lift=high_lift,
            ))
            out.extend(self._park())
            return "\n".join(self._dedupe(out))

        # 3. PAWN PROMOTION
        if move_type == "promotion":
            out.append("; --- PAWN PROMOTION ---")
            if promo_piece and promo_piece != "q":
                # Documented simplification: the reserve only holds queens.
                out.append(f"; WARNING: underpromotion to '{promo_piece}' requested "
                           f"- placing a QUEEN instead")
            if is_capture:
                out.append("; first, remove opponent piece at target")
                out.extend(self._to_graveyard(move_to))

            out.append("; remove promoting pawn to graveyard")
            out.extend(self._to_graveyard(move_from))

            # Rank 8 can only be a white promotion, rank 1 only a black one --
            # pure geometry, no chess library needed. The old code always
            # fetched from one point and handed a white queen to Black.
            colour = "white" if move_to[1] == "8" else "black"
            out.append(f"; place a {colour} queen from the reserve onto {move_to}")
            out.extend(self._transfer(
                self._queen_reserve_coords(colour), self.square_to_coords(move_to),
                f"{colour} queen reserve", move_to, high_lift=True,
                reason="crossing the board",
            ))
            out.extend(self._park())
            return "\n".join(self._dedupe(out))

        # 4. STANDARD CAPTURE - clear the destination before moving into it
        if is_capture:
            out.append(f"; --- CAPTURE OPPONENT PIECE AT {move_to} ---")
            out.extend(self._to_graveyard(move_to))

        # 5. STANDARD MOVE
        out.append(f"; --- PLAYER MOVE: {move_from} -> {move_to} ---")
        out.extend(self._transfer(
            self.square_to_coords(move_from), self.square_to_coords(move_to),
            move_from, move_to, high_lift=high_lift,
            reason="knight jumps" if high_lift else "",
        ))
        out.extend(self._park())
        return "\n".join(self._dedupe(out))
