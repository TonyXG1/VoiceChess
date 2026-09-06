"""Role 3 - motion planning: chess square -> physical XY mm -> G-code.

``plan()`` returns ONE newline-joined G-code string; the orchestrator splits it
into lines for the serial link. Every physical constant lives in
:mod:`motion.config` -- nothing here should need editing to calibrate the build.

The G-code dialect is GRBL/FluidNC, which differs from Marlin in ways that bite:

  * ``G4 P`` is in **seconds**, not milliseconds.
  * ``G0`` rapids ignore the ``F`` word; feed comes from the YAML's max rate.
    Only ``G1`` carries a feed, and setting F on a G0 just mutates the modal
    feed for the next G1.
  * The claw is the **A axis** (an rc_servo), not a spindle. Each piece has a
    measured closing angle. Because it is an axis, it queues in move order --
    an ``M3``-style spindle command would need explicit synchronisation.

Safety invariants every emitted sequence upholds:

  1. An empty claw crosses the board only at ``Z_SAFE``.
  2. Each piece uses its measured pickup Z and grip A.
  3. High loaded paths travel at Z0 and route around every current king and
     queen square. Low paths are used only where chess rules guarantee the
     straight path is empty.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, List, Tuple

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

        # The origin is already the centre of a1; no half-square offset.
        # On the built gantry, +X follows a-file ranks (a1 -> a8) and +Y
        # follows rank-1 files (a1 -> h1).
        x = cfg.BOARD_ORIGIN_X + row_idx * cfg.SQUARE_X
        y = cfg.BOARD_ORIGIN_Y + col_idx * cfg.SQUARE_Y
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
        """Claim the next free position in the temporary flat capture area."""
        i = self._graveyard_slot
        if i >= len(cfg.GRAVEYARD_POSITIONS):
            raise ValueError(
                f"Capture area is full ({i} pieces). Clear it and call "
                f"planner.reset() before continuing."
            )
        self._graveyard_slot += 1
        x, y = cfg.GRAVEYARD_POSITIONS[i]
        return self._checked(round(x, 2), round(y, 2), f"graveyard slot {i}")

    def _queen_reserve_coords(self, colour: str) -> Tuple[float, float]:
        """Claim the next spare queen of the given colour ('white' | 'black')."""
        i = self._queen_slot[colour]
        positions = cfg.QUEEN_RESERVE_POSITIONS[colour]
        if i >= len(positions):
            raise ValueError(
                f"Out of spare {colour} queens ({len(positions)} configured). "
                f"Restock the reserve or reset the planner."
            )
        self._queen_slot[colour] += 1
        x, y = positions[i]
        return self._checked(round(x, 2), round(y, 2), f"{colour} queen {i}")

    @staticmethod
    def _is_square(label: str) -> bool:
        return (isinstance(label, str) and len(label) == 2 and
                label[0].lower() in _FILES and label[1] in _RANKS)

    @staticmethod
    def _profile(piece_type: str) -> dict[str, float]:
        key = piece_type.lower()
        try:
            return cfg.PIECE_GRIP_PROFILES[key]
        except (AttributeError, KeyError):
            valid = ", ".join(sorted(cfg.PIECE_GRIP_PROFILES))
            raise ValueError(
                f"Unknown piece type '{piece_type}'. Expected one of: {valid}."
            ) from None

    @staticmethod
    def _square_neighbours(square: str) -> Iterable[str]:
        file_i = _FILES.index(square[0])
        rank_i = _RANKS.index(square[1])
        for df, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nf, nr = file_i + df, rank_i + dr
            if 0 <= nf < 8 and 0 <= nr < 8:
                yield _FILES[nf] + _RANKS[nr]

    def _square_route(self, start: str, end: str,
                      protected_squares: Iterable[str]) -> List[str]:
        """Shortest orthogonal board route that never enters a protected square."""
        blocked = {sq.lower() for sq in protected_squares if self._is_square(sq)}
        blocked.discard(start.lower())
        blocked.discard(end.lower())
        start, end = start.lower(), end.lower()
        queue = deque([start])
        previous: dict[str, str | None] = {start: None}
        while queue:
            current = queue.popleft()
            if current == end:
                break
            for neighbour in self._square_neighbours(current):
                if neighbour not in blocked and neighbour not in previous:
                    previous[neighbour] = current
                    queue.append(neighbour)
        if end not in previous:
            raise ValueError(
                f"No loaded route from {start} to {end} around protected "
                f"king/queen squares: {sorted(blocked)}"
            )
        route = []
        cursor = end
        while cursor != start:
            route.append(cursor)
            cursor = previous[cursor]  # type: ignore[assignment]
        route.reverse()
        return route

    @staticmethod
    def _segment_intersects_box(
            start: Tuple[float, float], end: Tuple[float, float],
            minimum: Tuple[float, float], maximum: Tuple[float, float]) -> bool:
        """Return whether a line segment enters or touches an XY rectangle."""
        t_min, t_max = 0.0, 1.0
        for origin, target, lower, upper in zip(start, end, minimum, maximum):
            delta = target - origin
            if abs(delta) < 1e-9:
                if origin < lower or origin > upper:
                    return False
                continue
            enter = (lower - origin) / delta
            leave = (upper - origin) / delta
            if enter > leave:
                enter, leave = leave, enter
            t_min = max(t_min, enter)
            t_max = min(t_max, leave)
            if t_min > t_max:
                return False
        return True

    def _segment_clear_of_protected(
            self, start: Tuple[float, float], end: Tuple[float, float],
            protected_squares: Iterable[str], start_label: str,
            end_label: str) -> bool:
        """Check the whole segment against protected king/queen square areas."""
        allowed = {start_label.lower(), end_label.lower()}
        for square in protected_squares:
            square = square.lower()
            if not self._is_square(square) or square in allowed:
                continue
            cx, cy = self.square_to_coords(square)
            minimum = (cx - cfg.SQUARE_X / 2, cy - cfg.SQUARE_Y / 2)
            maximum = (cx + cfg.SQUARE_X / 2, cy + cfg.SQUARE_Y / 2)
            if self._segment_intersects_box(start, end, minimum, maximum):
                return False
        return True

    def _safe_board_route(self, start: str, end: str,
                          protected_squares: Iterable[str]) \
            -> List[Tuple[float, float]]:
        """Prefer coordinated XY motion; add waypoints only around obstacles."""
        start_xy = self.square_to_coords(start)
        end_xy = self.square_to_coords(end)
        protected = tuple(protected_squares)
        if self._segment_clear_of_protected(
                start_xy, end_xy, protected, start, end):
            return [end_xy]

        # BFS supplies a guaranteed-safe orthogonal route. Then remove every
        # intermediate waypoint that a clear diagonal segment can bypass.
        squares = [start, *self._square_route(start, end, protected)]
        points = [self.square_to_coords(square) for square in squares]
        simplified: List[Tuple[float, float]] = []
        current = 0
        while current < len(points) - 1:
            candidate = len(points) - 1
            while candidate > current + 1:
                if self._segment_clear_of_protected(
                        points[current], points[candidate], protected,
                        start, end):
                    break
                candidate -= 1
            simplified.append(points[candidate])
            current = candidate
        return simplified

    def _board_to_outside_route(
            self, board_square: str, outside: Tuple[float, float],
            protected_squares: Iterable[str]) -> List[Tuple[float, float]]:
        """Reach an off-board point through the closest usable top/right gate."""
        ox, oy = outside
        candidates: List[Tuple[float, str, Tuple[float, float]]] = []
        blocked = {sq.lower() for sq in protected_squares if self._is_square(sq)}
        start_file = _FILES.index(board_square[0])
        start_rank = _RANKS.index(board_square[1])

        # +Y corridor, beyond the h-file side of the board.
        if oy > cfg.BOARD_OUTER_MAX_Y:
            for rank_i in range(8):
                gate = "h" + _RANKS[rank_i]
                if gate in blocked and gate != board_square:
                    continue
                gx, gy = self.square_to_coords(gate)
                corridor = (gx, cfg.GRAVEYARD_COLUMN_Y)
                board_distance = ((7 - start_file) +
                                  abs(rank_i - start_rank)) * cfg.SQUARE_X
                score = (board_distance + abs(ox - gx) +
                         abs(oy - corridor[1]))
                candidates.append((score, gate, corridor))

        # +X corridor, beyond the rank-8 side of the board.
        if ox > cfg.BOARD_OUTER_MAX_X:
            for file_i in range(8):
                gate = _FILES[file_i] + "8"
                if gate in blocked and gate != board_square:
                    continue
                gx, gy = self.square_to_coords(gate)
                corridor = (cfg.GRAVEYARD_ROW_X, gy)
                board_distance = ((7 - start_rank) +
                                  abs(file_i - start_file)) * cfg.SQUARE_Y
                score = (board_distance + abs(ox - corridor[0]) +
                         abs(oy - gy))
                candidates.append((score, gate, corridor))

        if not candidates:
            raise ValueError(
                f"Off-board point X{ox} Y{oy} has no configured +X/+Y corridor."
            )

        errors = []
        for _, gate, corridor in sorted(candidates):
            try:
                points = self._safe_board_route(
                    board_square, gate, protected_squares
                )
            except ValueError as exc:
                errors.append(str(exc))
                continue
            points.extend([corridor, outside])
            return self._dedupe_points(points)
        raise ValueError("; ".join(errors))

    @staticmethod
    def _dedupe_points(points: Iterable[Tuple[float, float]]) \
            -> List[Tuple[float, float]]:
        out: List[Tuple[float, float]] = []
        for point in points:
            rounded = (round(point[0], 2), round(point[1], 2))
            if not out or rounded != out[-1]:
                out.append(rounded)
        return out

    def _loaded_route(self, start: Tuple[float, float], end: Tuple[float, float],
                      start_label: str, end_label: str, high_lift: bool,
                      protected_squares: Iterable[str]) \
            -> List[Tuple[float, float]]:
        """Return loaded XY targets, excluding ``start`` and including ``end``."""
        if not high_lift:
            return [end]
        start_on_board = self._is_square(start_label)
        end_on_board = self._is_square(end_label)
        if start_on_board and end_on_board:
            return self._safe_board_route(
                start_label, end_label, protected_squares
            )
        if start_on_board:
            return self._board_to_outside_route(
                start_label, end, protected_squares
            )
        if end_on_board:
            board_out = self._board_to_outside_route(
                end_label, start, protected_squares
            )
            full_path = [self.square_to_coords(end_label), *board_out]
            return list(reversed(full_path))[1:]
        return [end]

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
            "G54 ; calibrated board work coordinates",
            f"G0 A{_f(cfg.CLAW_OPEN_A)} ; open claw",
            f"G0 Z{_f(cfg.Z_TOP)} ; top Z0 (already here at power-on)",
        ])

    def _transfer(self, start: Tuple[float, float], end: Tuple[float, float],
                  start_label: str, end_label: str,
                  high_lift: bool, piece_type: str, reason: str = "",
                  release_z: float | None = None,
                  protected_squares: Iterable[str] = ()) -> List[str]:
        """Pick a piece up at ``start`` and set it down at ``end``.

        The one primitive every operation is built from. Pickup Z, grip A, and
        low carry Z come from ``piece_type``. High paths travel at Z0 and take
        square-centre waypoints around protected king/queen squares.
        """
        profile = self._profile(piece_type)
        grip_z = profile["z"]
        carry_z = cfg.Z_HIGH_CARRY if high_lift else grip_z - cfg.LIFT_LOW
        place_z = grip_z if release_z is None else release_z
        note = f" ({reason})" if reason else ""
        lines = [
            f"G0 Z{_f(cfg.Z_SAFE)} ; raise to safe height",
            f"G0 X{_f(start[0])} Y{_f(start[1])} ; above {start_label}",
            f"G0 Z{_f(grip_z)} ; descend to grip {piece_type}",
            f"G0 A{_f(profile['a'])} ; close claw for {piece_type}",
            f"G4 P{cfg.CLAW_DWELL_S} ; let the servo settle",
            f"G0 Z{_f(carry_z)} ; {'high' if high_lift else 'low'} carry{note}",
        ]
        route = self._loaded_route(
            start, end, start_label, end_label, high_lift, protected_squares
        )
        for i, (x, y) in enumerate(route):
            destination = end_label if i == len(route) - 1 else "safe waypoint"
            lines.append(
                f"G1 X{_f(x)} Y{_f(y)} F{cfg.F_CARRY:.0f} ; carry to {destination}"
            )
        lines.extend([
            f"G0 Z{_f(place_z)} ; descend to "
            f"{'drop' if release_z is not None else 'place'}",
            f"G0 A{_f(cfg.CLAW_OPEN_A)} ; open claw",
            f"G4 P{cfg.CLAW_DWELL_S} ; let the servo settle",
            f"G0 Z{_f(cfg.Z_SAFE)} ; retract",
        ])
        return lines

    def _to_graveyard(self, square: str, piece_type: str,
                      protected_squares: Iterable[str]) -> List[str]:
        """Clear a captured piece off ``square``. Always a high lift -- the trip
        to the off-board grid crosses occupied territory."""
        grave = self._graveyard_coords()
        return self._transfer(
            self.square_to_coords(square), grave,
            square, f"graveyard slot {self._graveyard_slot - 1}",
            high_lift=True, piece_type=piece_type, reason="crossing the board",
            release_z=cfg.GRAVEYARD_RELEASE_Z,
            protected_squares=protected_squares,
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
             is_capture: bool = False, high_lift: bool = False,
             moving_piece: str = "pawn", captured_piece: str | None = None,
             protected_squares: Iterable[str] = ()) -> str:
        """Plan one applied move. Returns a newline-joined G-code string.

        Piece types and protected king/queen squares are derived by the
        orchestrator from Role 2's board BEFORE the move is applied.

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
        protected = tuple(protected_squares)
        out: List[str] = []

        # 1. CASTLING
        if move_type == "castling":
            out.append("; --- CASTLING ---")
            # King first. It slides along the back rank, which castling rules
            # require to be empty, so a low carry is safe.
            out.extend(self._transfer(
                self.square_to_coords(move_from), self.square_to_coords(move_to),
                move_from, move_to, high_lift=False, piece_type="king",
                protected_squares=protected,
            ))

            rook_squares = {
                "g1": ("h1", "f1"), "c1": ("a1", "d1"),
                "g8": ("h8", "f8"), "c8": ("a8", "d8"),
            }
            if move_to not in rook_squares:
                raise ValueError(f"Invalid castling target '{move_to}'!")
            rook_from, rook_to = rook_squares[move_to]

            # The rook moves second. Avoid the king at its new square rather
            # than its now-empty pre-castling square.
            rook_protected = set(protected)
            rook_protected.discard(move_from)
            rook_protected.add(move_to)

            out.extend(self._transfer(
                self.square_to_coords(rook_from), self.square_to_coords(rook_to),
                rook_from, rook_to, high_lift=True, piece_type="rook",
                reason="rook passes the king",
                protected_squares=rook_protected,
            ))
            out.extend(self._park())
            return "\n".join(self._dedupe(out))

        # 2. EN PASSANT
        if move_type == "en_passant":
            out.append("; --- EN PASSANT CAPTURE ---")
            # The captured pawn sits on the destination's FILE but the origin's RANK.
            captured_sq = move_to[0] + move_from[1]
            out.append(f"; captured pawn is on {captured_sq}, not {move_to}")
            out.extend(self._to_graveyard(captured_sq, "pawn", protected))
            out.append(f"; --- PLAYER MOVE: {move_from} -> {move_to} ---")
            out.extend(self._transfer(
                self.square_to_coords(move_from), self.square_to_coords(move_to),
                move_from, move_to, high_lift=high_lift,
                piece_type=moving_piece, protected_squares=protected,
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
                out.extend(self._to_graveyard(
                    move_to, captured_piece or "pawn", protected
                ))

            out.append("; remove promoting pawn to graveyard")
            out.extend(self._to_graveyard(move_from, "pawn", protected))

            # Rank 8 can only be a white promotion, rank 1 only a black one --
            # pure geometry, no chess library needed. The old code always
            # fetched from one point and handed a white queen to Black.
            colour = "white" if move_to[1] == "8" else "black"
            out.append(f"; place a {colour} queen from the reserve onto {move_to}")
            out.extend(self._transfer(
                self._queen_reserve_coords(colour), self.square_to_coords(move_to),
                f"{colour} queen reserve", move_to, high_lift=True,
                piece_type="queen",
                reason="crossing the board",
                protected_squares=protected,
            ))
            out.extend(self._park())
            return "\n".join(self._dedupe(out))

        # 4. STANDARD CAPTURE - clear the destination before moving into it
        if is_capture:
            out.append(f"; --- CAPTURE OPPONENT PIECE AT {move_to} ---")
            out.extend(self._to_graveyard(
                move_to, captured_piece or "pawn", protected
            ))

        # 5. STANDARD MOVE
        out.append(f"; --- PLAYER MOVE: {move_from} -> {move_to} ---")
        out.extend(self._transfer(
            self.square_to_coords(move_from), self.square_to_coords(move_to),
            move_from, move_to, high_lift=high_lift,
            piece_type=moving_piece,
            reason="knight jumps" if high_lift else "",
            protected_squares=protected,
        ))
        out.extend(self._park())
        return "\n".join(self._dedupe(out))
