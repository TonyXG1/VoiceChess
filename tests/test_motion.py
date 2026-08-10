"""Tests for Role 3's MotionPlanner and the orchestrator's move classification.

Coordinate expectations follow motion/config.py: squares are 57.0 x 57.375 mm
(derived from the board's measured 456 x 459 mm spans), and a square's
coordinate is its CENTER, so a1 = (28.5, 28.69).

Several tests here are regression guards for bugs that would only have shown up
with the motors live -- the millisecond/second dwell mix-up in particular.
"""

import re

import pytest

from chess_ai import ChessEngine
from motion import MotionPlanner
from motion import config as cfg
from orchestrator import Orchestrator, SerialLink
from tools.gcode_preview import _SCENARIOS, simulate


@pytest.fixture
def planner():
    return MotionPlanner()


def _codes(gcode):
    """Just the machine-readable part of each line, comments stripped."""
    return [l.split(";", 1)[0].strip() for l in gcode.splitlines()
            if l.split(";", 1)[0].strip()]


# ------------------------------ coordinates ------------------------------- #

def test_square_to_coords(planner):
    assert planner.square_to_coords("a1") == (28.5, 28.69)
    assert planner.square_to_coords("h8") == (427.5, 430.31)
    assert planner.square_to_coords("e2") == (256.5, 86.06)

def test_squares_span_the_measured_board(planner):
    # a-h spans 456 mm and 1-8 spans 459 mm; centers sit half a square inside.
    a1x, a1y = planner.square_to_coords("a1")
    h8x, h8y = planner.square_to_coords("h8")
    assert h8x - a1x == pytest.approx(456.0 - cfg.SQUARE_X, abs=0.01)
    assert h8y - a1y == pytest.approx(459.0 - cfg.SQUARE_Y, abs=0.01)

def test_out_of_bounds_square_is_blocked(planner):
    with pytest.raises(ValueError):
        planner.square_to_coords("i9")
    with pytest.raises(ValueError):
        planner.plan("e9e4")  # bad origin square must never reach the motors


# ------------------------------- scenarios -------------------------------- #

def test_standard_move(planner):
    gcode = planner.plan("e2e4")
    assert isinstance(gcode, str)
    assert "G0 X256.50 Y86.06" in gcode         # above e2
    assert "G1 X256.50 Y200.81 F1200" in gcode  # carry to e4
    assert f"G0 A{cfg.CLAW_CLOSED_A:.2f}" in gcode
    assert f"G0 A{cfg.CLAW_OPEN_A:.2f}" in gcode
    assert "G0 X0.00 Y0.00" in gcode            # parks out of the way

def test_capture_removes_target_to_graveyard_first(planner):
    gcode = planner.plan("d4e5", is_capture=True)
    graveyard = gcode.index(f"X{cfg.GRAVEYARD_X0:.2f} Y{cfg.GRAVEYARD_Y0:.2f}")
    place = gcode.index("; --- PLAYER MOVE: d4 -> e5 ---")
    assert graveyard < place                    # clear the square before moving in

def test_castling_moves_king_then_rook(planner):
    gcode = planner.plan("e1g1", move_type="castling")
    king = gcode.index("X256.50 Y28.69")        # king from e1
    rook = gcode.index("X427.50 Y28.69")        # rook from h1
    assert king < rook
    assert "X313.50 Y28.69" in gcode            # rook to f1

def test_en_passant_clears_the_passed_pawn(planner):
    gcode = planner.plan("d5e6", move_type="en_passant", is_capture=True)
    # Captured pawn sits on e5 (destination file, origin rank), not e6
    assert "above e5" in gcode
    assert f"X{cfg.GRAVEYARD_X0:.2f} Y{cfg.GRAVEYARD_Y0:.2f}" in gcode

def test_promotion_swaps_pawn_for_reserve_queen(planner):
    gcode = planner.plan("a7a8", move_type="promotion")
    assert "remove promoting pawn to graveyard" in gcode
    assert f"X{cfg.QUEEN_RESERVE_X0:.2f} Y{cfg.QUEEN_RESERVE_Y_WHITE:.2f}" in gcode
    assert "carry to a8" in gcode

def test_promotion_with_capture_clears_target_first(planner):
    gcode = planner.plan("b7a8", move_type="promotion", is_capture=True)
    assert "remove opponent piece at target" in gcode


# --------------------------- G-code dialect -------------------------------- #
# GRBL/FluidNC, not Marlin. These are the details that only bite once the
# machine is powered, so they get explicit guards.

def test_dwell_is_seconds_not_milliseconds(planner):
    """G4 P is SECONDS in GRBL/FluidNC. 'G4 P500' would stall for 8 minutes."""
    dwells = [c for c in _codes(planner.plan("e2e4")) if c.startswith("G4")]
    assert dwells, "a pick-and-place must dwell for the servo"
    for d in dwells:
        seconds = float(d.split("P")[1])
        assert 0 < seconds <= 5, f"{d!r} is not a plausible dwell in seconds"

def test_claw_is_an_axis_not_a_spindle(planner):
    """The claw is an rc_servo on A. Bare M3 would be a no-op (S defaults to 0)."""
    gcode = planner.plan("e2e4")
    assert "M3" not in gcode and "M5" not in gcode
    assert "A45.00" in gcode and "A0.00" in gcode

def test_rapids_carry_no_feed_word(planner):
    """G0 ignores F in GRBL; leaving it on only mutates the modal feed."""
    for code in _codes(planner.plan("d4e5", is_capture=True)):
        if code.startswith("G0"):
            assert "F" not in code, f"rapid should not set a feed: {code!r}"

def test_startup_sets_units_and_opens_the_claw(planner):
    codes = _codes(planner.startup())
    assert "G21" in codes and "G90" in codes and "G94" in codes
    assert f"G0 A{cfg.CLAW_OPEN_A:.2f}" in codes


# ----------------------------- lift policy --------------------------------- #

def _carry_heights(gcode):
    """The Z the machine is at during each loaded G1 carry."""
    z = cfg.Z_TOP
    heights = []
    for code in _codes(gcode):
        m = re.search(r"Z(-?\d+\.?\d*)", code)
        if m:
            z = float(m.group(1))
        if code.startswith("G1"):
            heights.append(z)
    return heights

def test_sliding_piece_carries_low(planner):
    # Chess rules guarantee a sliding piece's path is empty, so it stays low.
    assert _carry_heights(planner.plan("e2e4")) == [cfg.Z_CARRY_LOW]

def test_knight_carries_high(planner):
    # A knight jumps over pieces; it must clear the 95mm king.
    assert _carry_heights(planner.plan("g1f3", high_lift=True)) == [cfg.Z_CARRY_HIGH]

def test_graveyard_trip_carries_high(planner):
    # The trip to the off-board grid crosses occupied squares either way.
    heights = _carry_heights(planner.plan("d4e5", is_capture=True))
    assert heights == [cfg.Z_CARRY_HIGH, cfg.Z_CARRY_LOW]

def test_castling_rook_leg_carries_high(planner):
    """The rook passes through the square the king just landed on.

    Kingside: king e1->g1, then rook h1->f1 crosses g1. Moving the rook first
    does not help -- then the king's path crosses f1. One leg must lift.
    """
    assert _carry_heights(planner.plan("e1g1", move_type="castling")) == [
        cfg.Z_CARRY_LOW,    # king, along a rank castling requires to be empty
        cfg.Z_CARRY_HIGH,   # rook, over the king
    ]

def test_lift_high_actually_clears_the_tallest_piece():
    assert cfg.LIFT_HIGH >= cfg.TALLEST_PIECE


# -------------------------- off-board bookkeeping -------------------------- #

def test_graveyard_slots_advance(planner):
    """Captured pieces land in a grid. Piling them on one point topples them."""
    first = planner.plan("d4e5", is_capture=True)
    second = planner.plan("a1a2", is_capture=True)
    assert f"X{cfg.GRAVEYARD_X0:.2f} Y{cfg.GRAVEYARD_Y0:.2f}" in first
    assert f"X{cfg.GRAVEYARD_X0 + cfg.GRAVEYARD_DX:.2f} " \
           f"Y{cfg.GRAVEYARD_Y0:.2f}" in second

def test_reset_rewinds_the_graveyard(planner):
    planner.plan("d4e5", is_capture=True)
    planner.reset()
    assert f"X{cfg.GRAVEYARD_X0:.2f} Y{cfg.GRAVEYARD_Y0:.2f}" in \
        planner.plan("d4e5", is_capture=True)

def test_promotion_fetches_a_queen_of_the_right_colour(planner):
    """Rank 8 is a white promotion, rank 1 a black one -- pure geometry."""
    white = planner.plan("a7a8q", move_type="promotion")
    black = planner.plan("a2a1q", move_type="promotion")
    assert f"Y{cfg.QUEEN_RESERVE_Y_WHITE:.2f}" in white
    assert "white queen" in white
    assert f"Y{cfg.QUEEN_RESERVE_Y_BLACK:.2f}" in black
    assert "black queen" in black

def test_underpromotion_is_flagged_not_silently_swapped(planner):
    gcode = planner.plan("a7a8n", move_type="promotion")
    assert "WARNING" in gcode and "underpromotion" in gcode


# ------------------------- machine-wide invariants ------------------------- #

@pytest.mark.parametrize("label,kwargs", _SCENARIOS, ids=[s[0] for s in _SCENARIOS])
def test_every_scenario_stays_inside_the_envelope(label, kwargs, capsys):
    _, _, problems = simulate(MotionPlanner().plan(**kwargs), verbose=False)
    assert not problems, "\n".join(problems)

@pytest.mark.parametrize("label,kwargs", _SCENARIOS, ids=[s[0] for s in _SCENARIOS])
def test_xy_never_moves_while_a_piece_is_on_the_board(label, kwargs):
    """The claw must never drag across the board at grip height.

    Any XY motion has to happen either empty at Z_SAFE or loaded at a carry
    height. Descending to Z_GRIP is only ever allowed straight down over the
    square being acted on.
    """
    gcode = MotionPlanner().plan(**kwargs)
    x = y = 0.0
    z = cfg.Z_TOP
    for code in _codes(gcode):
        m = re.search(r"Z(-?\d+\.?\d*)", code)
        if m:
            z = float(m.group(1))
        mx = re.search(r"X(-?\d+\.?\d*)", code)
        my = re.search(r"Y(-?\d+\.?\d*)", code)
        nx = float(mx.group(1)) if mx else x
        ny = float(my.group(1)) if my else y
        if (nx, ny) != (x, y):
            assert z >= cfg.Z_CARRY_LOW, (
                f"{label}: XY moves to ({nx}, {ny}) at Z{z}, below the "
                f"carry height {cfg.Z_CARRY_LOW} -- this drags pieces"
            )
        x, y = nx, ny


# --------------------- orchestrator move classification -------------------- #

def _orch(fen=None):
    eng = ChessEngine(start_fen=fen)
    return Orchestrator(engine=eng, voice=None, planner=MotionPlanner(),
                        serial=SerialLink())

def test_classify_standard_and_capture():
    orch = _orch()
    assert orch._classify_move("e2e4") == ("standard", False, False)
    orch.engine.apply("e2e4")
    orch.engine.apply("d7d5")
    assert orch._classify_move("e4d5") == ("standard", True, False)

def test_classify_flags_knights_for_a_high_lift():
    orch = _orch()
    assert orch._classify_move("g1f3") == ("standard", False, True)
    assert orch._classify_move("e2e4") == ("standard", False, False)

def test_classify_castling():
    orch = _orch("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert orch._classify_move("e1g1") == ("castling", False, False)
    assert orch._classify_move("e1c1") == ("castling", False, False)

def test_classify_en_passant():
    # White pawn on d5; black just played e7e5 -> ep square e6
    orch = _orch("4k3/8/8/3Pp3/8/8/8/4K3 w - e6 0 2")
    assert orch._classify_move("d5e6") == ("en_passant", True, False)

def test_classify_promotion():
    orch = _orch("1r2k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    assert orch._classify_move("a7a8q") == ("promotion", False, False)
    assert orch._classify_move("a7b8q") == ("promotion", True, False)
