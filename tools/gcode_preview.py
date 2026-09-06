"""Dry-run a planned move: trace it, time it, and prove it stays in the envelope.

Run this before the first powered move, and again after changing anything in
motion/config.py. It needs no hardware, no serial port, and no Stockfish.

    python tools/gcode_preview.py --move e2e4
    python tools/gcode_preview.py --move g1f3 --piece knight --high-lift
    python tools/gcode_preview.py --move e1g1 --type castling
    python tools/gcode_preview.py --all          # every move type at once

Exits non-zero if any line would take the gantry outside the machine envelope
or drive the claw into the board, so it works as a CI check too.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motion import MotionPlanner            # noqa: E402
from motion import config as cfg            # noqa: E402

_WORD = re.compile(r"([XYZAFP])(-?\d+\.?\d*)")

# Rapids take their speed from the FluidNC YAML, not from the G-code, so the
# timing estimate needs those numbers here. Keep in sync with fluidnc/config.yaml.
MAX_RATE_X = 3000.0    # mm/min
MAX_RATE_Y = 4000.0    # mm/min
MAX_RATE_Z = 4000.0    # mm/min


class Violation(Exception):
    pass


def _check_point(x: float, y: float, z: float, a: float, line_no: int,
                 line: str) -> List[str]:
    """Every way a single machine position can be wrong."""
    problems = []
    if not (0.0 <= x <= cfg.X_MAX):
        problems.append(f"X{x:.2f} outside 0..{cfg.X_MAX}")
    if not (0.0 <= y <= cfg.Y_MAX):
        problems.append(f"Y{y:.2f} outside 0..{cfg.Y_MAX}")
    if z < cfg.Z_TOP:
        problems.append(f"Z{z:.2f} above the top of travel ({cfg.Z_TOP})")
    if z > cfg.Z_BOARD:
        problems.append(f"Z{z:.2f} BELOW the board surface ({cfg.Z_BOARD}) "
                        f"- this drives the claw into the board")
    if not (0.0 <= a <= 90.0):
        problems.append(f"A{a:.2f} outside the servo's 0..90 sweep")
    return [f"  line {line_no}: {p}\n    {line}" for p in problems]


def simulate(gcode: str, verbose: bool = True) -> Tuple[float, float, List[str]]:
    """Walk the G-code, printing a trace. Returns (mm travelled, seconds, problems)."""
    x = y = 0.0
    z = cfg.Z_TOP
    a = cfg.CLAW_OPEN_A
    feed = 0.0
    distance = 0.0
    seconds = 0.0
    problems: List[str] = []

    if verbose:
        print(f"{'#':>4}  {'X':>8} {'Y':>8} {'Z':>8} {'A':>6}  {'mm':>7} "
              f"{'s':>6}  code")
        print("-" * 78)

    for i, line in enumerate(gcode.splitlines(), start=1):
        code = line.split(";", 1)[0].strip()
        if not code:
            if verbose:
                print(f"{i:>4}  {'':>8} {'':>8} {'':>8} {'':>6}  {'':>7} "
                      f"{'':>6}  {line.strip()}")
            continue

        words = dict(_WORD.findall(code.upper()))
        nx = float(words.get("X", x))
        ny = float(words.get("Y", y))
        nz = float(words.get("Z", z))
        na = float(words.get("A", a))
        if "F" in words:
            feed = float(words["F"])

        # How far, and how long
        seg = ((nx - x) ** 2 + (ny - y) ** 2) ** 0.5
        dz = abs(nz - z)
        step_mm = seg + dz
        if code.startswith("G4"):
            step_s = float(words.get("P", 0.0))
        elif code.startswith("G1") and feed > 0:
            step_s = (seg + dz) / feed * 60.0
        elif code.startswith("G0"):
            # A coordinated rapid is limited by whichever participating axis
            # needs longest at its own configured maximum rate.
            xy_s = max(abs(nx - x) / MAX_RATE_X,
                       abs(ny - y) / MAX_RATE_Y) * 60.0
            step_s = xy_s + dz / MAX_RATE_Z * 60.0
        else:
            step_s = 0.0

        distance += step_mm
        seconds += step_s
        x, y, z, a = nx, ny, nz, na
        problems.extend(_check_point(x, y, z, a, i, line.strip()))

        if verbose:
            print(f"{i:>4}  {x:>8.2f} {y:>8.2f} {z:>8.2f} {a:>6.1f}  "
                  f"{step_mm:>7.1f} {step_s:>6.2f}  {line.strip()}")

    return distance, seconds, problems


def preview(planner: MotionPlanner, label: str, verbose: bool = True,
            **plan_kwargs) -> List[str]:
    gcode = planner.plan(**plan_kwargs)
    print(f"\n=== {label} ===")
    distance, seconds, problems = simulate(gcode, verbose=verbose)
    lines = len(gcode.splitlines())
    motion = len([l for l in gcode.splitlines() if l.split(";", 1)[0].strip()])
    print(f"\n  {lines} lines ({motion} sent to FluidNC, "
          f"{lines - motion} comments stripped)")
    print(f"  {distance:.0f} mm of travel, ~{seconds:.1f}s")
    if problems:
        print(f"  ENVELOPE VIOLATIONS ({len(problems)}):")
        for p in problems:
            print(p)
    else:
        print("  envelope: OK")
    return problems


_SCENARIOS = [
    ("standard pawn e2e4", dict(uci_move="e2e4", moving_piece="pawn")),
    ("knight g1f3 (high lift)", dict(
        uci_move="g1f3", moving_piece="knight", high_lift=True,
        protected_squares=("d1", "e1", "d8", "e8"))),
    ("capture d4e5", dict(uci_move="d4e5", is_capture=True,
                           moving_piece="pawn", captured_piece="pawn",
                           protected_squares=("d1", "e1", "d8", "e8"))),
    ("castling kingside e1g1", dict(
        uci_move="e1g1", move_type="castling",
        moving_piece="king", protected_squares=("e1", "e8"))),
    ("castling queenside e8c8", dict(
        uci_move="e8c8", move_type="castling",
        moving_piece="king", protected_squares=("e1", "e8"))),
    ("en passant d5e6", dict(uci_move="d5e6", move_type="en_passant",
                             is_capture=True, moving_piece="pawn",
                             captured_piece="pawn",
                             protected_squares=("e1", "e8"))),
    ("promotion a7a8q", dict(
        uci_move="a7a8q", move_type="promotion", moving_piece="pawn",
        protected_squares=("e1", "e8"))),
    ("promotion+capture b2a1q (black)", dict(uci_move="b2a1q",
                                             move_type="promotion",
                                             is_capture=True,
                                             moving_piece="pawn",
                                             captured_piece="rook",
                                             protected_squares=("e1", "e8"))),
    ("corner to corner a1h8", dict(
        uci_move="a1h8", moving_piece="bishop", high_lift=True,
        protected_squares=("d4", "e5"))),
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--move", help="UCI move, e.g. e2e4")
    p.add_argument("--type", default="standard",
                   choices=["standard", "castling", "en_passant", "promotion"])
    p.add_argument("--capture", action="store_true", help="the move captures")
    p.add_argument("--high-lift", action="store_true",
                   help="the mover is a knight (jumps over pieces)")
    p.add_argument("--piece", default="pawn",
                   choices=sorted(cfg.PIECE_GRIP_PROFILES),
                   help="physical type of the moving piece")
    p.add_argument("--captured-piece", choices=sorted(cfg.PIECE_GRIP_PROFILES),
                   help="physical type on the captured square")
    p.add_argument("--protect", nargs="*", default=(), metavar="SQUARE",
                   help="king/queen squares the loaded route must avoid")
    p.add_argument("--all", action="store_true",
                   help="run every move type; summary only")
    args = p.parse_args()

    print(f"board:  a1 = {MotionPlanner().square_to_coords('a1')}   "
          f"h8 = {MotionPlanner().square_to_coords('h8')}")
    print(f"        {cfg.SQUARE_X} x {cfg.SQUARE_Y} mm squares, origin "
          f"({cfg.BOARD_ORIGIN_X}, {cfg.BOARD_ORIGIN_Y})")
    print(f"Z:      high carry/safe {cfg.Z_SAFE}  board {cfg.Z_BOARD}")
    print(f"claw:   open A{cfg.CLAW_OPEN_A:.0f}")
    print("profiles:" + "".join(
        f"  {piece}=Z{profile['z']:.0f}/A{profile['a']:.0f}"
        for piece, profile in cfg.PIECE_GRIP_PROFILES.items()
    ))

    problems: List[str] = []
    if args.all:
        for label, kwargs in _SCENARIOS:
            # Fresh planner per scenario so graveyard slots start from 0.
            problems += preview(MotionPlanner(), label, verbose=False, **kwargs)
    elif args.move:
        problems += preview(MotionPlanner(), args.move, verbose=True,
                            uci_move=args.move, move_type=args.type,
                            is_capture=args.capture, high_lift=args.high_lift,
                            moving_piece=args.piece,
                            captured_piece=args.captured_piece,
                            protected_squares=args.protect)
    else:
        p.error("give --move UCI or --all")

    print()
    if problems:
        print(f"FAILED: {len(problems)} envelope violation(s). "
              f"Do NOT power the gantry with this config.")
        return 1
    print("PASSED: every planned position is inside the machine envelope.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
