"""Tests for Role 3's MotionPlanner and the orchestrator's move classification.

Planner scenarios ported from Role 3's test_all_scenarios.py (which printed
instead of asserting). Coordinate expectations follow the planner's own math:
x = 25 + file_index * 50, y = 25 + rank_index * 50.
"""

import pytest

from chess_ai import ChessEngine
from motion import MotionPlanner
from orchestrator import Orchestrator, SerialLink


@pytest.fixture
def planner():
    return MotionPlanner()


# ------------------------------ coordinates ------------------------------- #

def test_square_to_coords(planner):
    assert planner.square_to_coords("a1") == (25.0, 25.0)
    assert planner.square_to_coords("h8") == (375.0, 375.0)
    assert planner.square_to_coords("e2") == (225.0, 75.0)

def test_out_of_bounds_square_is_blocked(planner):
    with pytest.raises(ValueError):
        planner.square_to_coords("i9")
    with pytest.raises(ValueError):
        planner.plan("e9e4")  # bad origin square must never reach the motors


# ------------------------------- scenarios -------------------------------- #

def test_standard_move(planner):
    gcode = planner.plan("e2e4")
    assert isinstance(gcode, str)
    assert "G0 X225.0 Y75.0" in gcode          # above e2
    assert "G1 X225.0 Y175.0" in gcode         # place on e4
    assert "M3" in gcode and "M5" in gcode     # claw close/open
    assert "X0 Y0" in gcode                    # returns to origin

def test_capture_removes_target_to_graveyard_first(planner):
    gcode = planner.plan("d4e5", is_capture=True)
    graveyard = gcode.index("X420.0 Y200.0")
    place = gcode.index("; --- PLAYER MOVE: d4 -> e5 ---")
    assert graveyard < place                   # clear the square before moving in

def test_castling_moves_king_then_rook(planner):
    gcode = planner.plan("e1g1", move_type="castling")
    assert "X225.0 Y25.0" in gcode             # king from e1
    assert "X375.0 Y25.0" in gcode             # rook from h1
    assert "X275.0 Y25.0" in gcode             # rook to f1

def test_en_passant_clears_the_passed_pawn(planner):
    gcode = planner.plan("d5e6", move_type="en_passant", is_capture=True)
    # Captured pawn sits on e5 (destination file, origin rank), not e6
    assert "Go to piece at e5" in gcode
    assert "X420.0 Y200.0" in gcode            # pawn goes to the graveyard

def test_promotion_swaps_pawn_for_reserve_queen(planner):
    gcode = planner.plan("a7a8", move_type="promotion")
    assert "Remove promoting pawn to graveyard" in gcode
    assert "X480.0 Y200.0" in gcode            # queen reserve
    assert "Place Queen on a8" in gcode

def test_promotion_with_capture_clears_target_first(planner):
    gcode = planner.plan("b7a8", move_type="promotion", is_capture=True)
    assert "remove opponent piece at target" in gcode


# --------------------- orchestrator move classification -------------------- #

def _orch(fen=None):
    eng = ChessEngine(start_fen=fen)
    return Orchestrator(engine=eng, voice=None, planner=MotionPlanner(),
                        serial=SerialLink())

def test_classify_standard_and_capture():
    orch = _orch()
    assert orch._classify_move("e2e4") == ("standard", False)
    orch.engine.apply("e2e4")
    orch.engine.apply("d7d5")
    assert orch._classify_move("e4d5") == ("standard", True)

def test_classify_castling():
    orch = _orch("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert orch._classify_move("e1g1") == ("castling", False)
    assert orch._classify_move("e1c1") == ("castling", False)

def test_classify_en_passant():
    # White pawn on d5; black just played e7e5 -> ep square e6
    orch = _orch("4k3/8/8/3Pp3/8/8/8/4K3 w - e6 0 2")
    assert orch._classify_move("d5e6") == ("en_passant", True)

def test_classify_promotion():
    orch = _orch("1r2k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    assert orch._classify_move("a7a8q") == ("promotion", False)
    assert orch._classify_move("a7b8q") == ("promotion", True)
