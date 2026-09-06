"""Tests for Role 3's MotionPlanner and the orchestrator's move classification.

Coordinate expectations follow motion/config.py: squares are 58 x 58 mm
(464 x 464 mm playing area, plus a 20 mm border on each side), and a square's
coordinate is its CENTER, with a1's centre as the work origin (0, 0).

Several tests here are regression guards for bugs that would only have shown up
with the motors live -- the millisecond/second dwell mix-up in particular.
"""

import re

import pytest

from chess_ai import ChessEngine, MockVoice
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
    assert planner.square_to_coords("a1") == (0.0, 0.0)
    assert planner.square_to_coords("h8") == (406.0, 406.0)
    assert planner.square_to_coords("e2") == (58.0, 232.0)
    assert planner.square_to_coords("b1") == (0.0, 58.0)
    assert planner.square_to_coords("a2") == (58.0, 0.0)

def test_squares_span_the_measured_board(planner):
    # Eight 58 mm squares; the first and last centres are seven squares apart.
    a1x, a1y = planner.square_to_coords("a1")
    h8x, h8y = planner.square_to_coords("h8")
    assert h8x - a1x == 406.0
    assert h8y - a1y == 406.0

def test_board_border_is_outside_square_coordinates(planner):
    assert cfg.BOARD_OUTER_MIN_X == cfg.BOARD_OUTER_MIN_Y == -49.0
    assert cfg.BOARD_OUTER_MAX_X == cfg.BOARD_OUTER_MAX_Y == 455.0
    assert planner.square_to_coords("a1") == (0.0, 0.0)

def test_capture_arms_clear_the_larger_board_by_half_the_open_claw():
    assert cfg.GRAVEYARD_COLUMN_Y - cfg.BOARD_OUTER_MAX_Y \
        == cfg.CLAW_OPEN_MM / 2
    assert cfg.GRAVEYARD_ROW_X - cfg.BOARD_OUTER_MAX_X \
        == cfg.CLAW_OPEN_MM / 2

def test_out_of_bounds_square_is_blocked(planner):
    with pytest.raises(ValueError):
        planner.square_to_coords("i9")
    with pytest.raises(ValueError):
        planner.plan("e9e4")  # bad origin square must never reach the motors

@pytest.mark.parametrize("x,y", [(535.01, 0), (0, 545.01), (-0.01, 0), (0, -0.01)])
def test_positions_beyond_measured_travel_are_blocked(planner, x, y):
    with pytest.raises(ValueError, match="outside the machine envelope"):
        planner._checked(x, y, "travel limit test")

def test_measured_travel_boundary_is_reachable(planner):
    assert planner._checked(535.0, 545.0, "travel limit test") == (535.0, 545.0)


# ------------------------------- scenarios -------------------------------- #

def test_standard_move(planner):
    gcode = planner.plan("e2e4", moving_piece="pawn")
    assert isinstance(gcode, str)
    assert "G0 X58.00 Y232.00" in gcode         # above e2
    assert "G1 X174.00 Y232.00 F3000" in gcode  # carry to e4
    assert "G0 Z85.00" in gcode
    assert "G0 A62.00" in gcode
    assert f"G0 A{cfg.CLAW_OPEN_A:.2f}" in gcode
    assert "G0 X0.00 Y0.00" in gcode            # parks above a1

def test_capture_removes_target_to_graveyard_first(planner):
    gcode = planner.plan("d4e5", is_capture=True)
    x, y = cfg.GRAVEYARD_POSITIONS[0]
    graveyard = gcode.index(f"X{x:.2f} Y{y:.2f}")
    place = gcode.index("; --- PLAYER MOVE: d4 -> e5 ---")
    assert graveyard < place                    # clear the square before moving in

def test_castling_moves_king_then_rook(planner):
    gcode = planner.plan("e1g1", move_type="castling")
    king = gcode.index("X0.00 Y232.00")        # king from e1
    rook = gcode.index("X0.00 Y406.00")        # rook from h1
    assert king < rook
    assert "X0.00 Y290.00" in gcode            # rook to f1

def test_en_passant_clears_the_passed_pawn(planner):
    gcode = planner.plan("d5e6", move_type="en_passant", is_capture=True)
    # Captured pawn sits on e5 (destination file, origin rank), not e6
    assert "above e5" in gcode
    x, y = cfg.GRAVEYARD_POSITIONS[0]
    assert f"X{x:.2f} Y{y:.2f}" in gcode

def test_promotion_swaps_pawn_for_reserve_queen(planner):
    gcode = planner.plan("a7a8", move_type="promotion")
    assert "remove promoting pawn to graveyard" in gcode
    x, y = cfg.QUEEN_RESERVE_POSITIONS["white"][0]
    assert f"X{x:.2f} Y{y:.2f}" in gcode
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
    assert "A62.00" in gcode and "A0.00" in gcode

def test_rapids_carry_no_feed_word(planner):
    """G0 ignores F in GRBL; leaving it on only mutates the modal feed."""
    for code in _codes(planner.plan("d4e5", is_capture=True)):
        if code.startswith("G0"):
            assert "F" not in code, f"rapid should not set a feed: {code!r}"

def test_startup_sets_units_and_opens_the_claw(planner):
    codes = _codes(planner.startup())
    assert "G21" in codes and "G90" in codes and "G94" in codes
    assert f"G0 A{cfg.CLAW_OPEN_A:.2f}" in codes

def test_startup_at_physical_top_never_commands_upward_travel(planner):
    codes = _codes(planner.startup())
    assert codes.index("G54") < codes.index("G0 Z0.00")
    z_targets = [float(m.group(1)) for code in codes
                 if (m := re.search(r"Z(-?\d+\.?\d*)", code))]
    assert z_targets == [0.0]
    _, _, problems = simulate(planner.startup(), verbose=False)
    assert not problems

def test_top_zero_preserves_measured_board_clearance_and_pickup(planner):
    # Physical measurements, independent of the configured coordinate origin.
    assert cfg.Z_TOP == 0.0
    assert cfg.Z_BOARD - cfg.Z_TOP == 115.0
    assert cfg.Z_BOTTOM - cfg.Z_TOP == 170.0
    codes = _codes(planner.plan("e2e4"))
    assert "G0 Z85.00" in codes   # measured pawn pickup
    assert "G0 Z70.00" in codes   # lift the piece 15 mm
    assert cfg.Z_HIGH_CARRY == 0.0

def test_all_measured_piece_profiles_are_recorded():
    assert cfg.PIECE_GRIP_PROFILES == {
        "pawn": {"z": 85.0, "a": 62.0},
        "knight": {"z": 75.0, "a": 76.0},
        "bishop": {"z": 80.0, "a": 43.0},
        "rook": {"z": 90.0, "a": 43.0},
        "queen": {"z": 67.0, "a": 35.0},
        "king": {"z": 67.0, "a": 35.0},
    }

@pytest.mark.parametrize("target", [-1.0, 116.0])
def test_preview_rejects_travel_above_top_or_below_board(target):
    _, _, problems = simulate(f"G0 Z{target}", verbose=False)
    assert problems


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
    assert _carry_heights(
        planner.plan("e2e4", moving_piece="pawn")
    ) == [70.0]

def test_knight_carries_high(planner):
    heights = _carry_heights(planner.plan(
        "g1f3", high_lift=True, moving_piece="knight",
        protected_squares=("d1", "e1", "d8", "e8"),
    ))
    assert heights and set(heights) == {cfg.Z_HIGH_CARRY}

def test_graveyard_trip_carries_high(planner):
    # The trip to the off-board grid crosses occupied squares either way.
    heights = _carry_heights(planner.plan(
        "d4e5", is_capture=True, moving_piece="pawn", captured_piece="pawn",
        protected_squares=("d1", "e1", "d8", "e8"),
    ))
    assert heights[-1] == 70.0
    assert heights[:-1] and set(heights[:-1]) == {cfg.Z_HIGH_CARRY}

def test_graveyard_descends_to_z40_then_drops(planner):
    codes = _codes(planner.plan("d4e5", is_capture=True))
    drop = codes.index(f"G0 Z{cfg.GRAVEYARD_RELEASE_Z:.2f}")
    assert cfg.GRAVEYARD_RELEASE_Z == 40.0
    assert cfg.GRAVEYARD_DROP_MM == 40.0
    assert codes[drop + 1] == f"G0 A{cfg.CLAW_OPEN_A:.2f}"

def test_castling_rook_leg_carries_high(planner):
    """The rook passes through the square the king just landed on.

    Kingside: king e1->g1, then rook h1->f1 crosses g1. Moving the rook first
    does not help -- then the king's path crosses f1. One leg must lift.
    """
    heights = _carry_heights(planner.plan(
        "e1g1", move_type="castling", moving_piece="king",
        protected_squares=("e1", "e8"),
    ))
    assert heights[0] == 52.0  # king pickup Z67 minus the 15 mm low lift
    assert heights[1:] and set(heights[1:]) == {cfg.Z_HIGH_CARRY}

def test_high_lift_clears_every_unprotected_piece():
    minimum_clearance = min(
        profile["z"] for profile in cfg.PIECE_GRIP_PROFILES.values()
    )
    assert minimum_clearance > cfg.TALLEST_UNPROTECTED_PIECE

def test_high_route_never_enters_protected_king_or_queen_squares(planner):
    protected = ("d4", "e5")
    gcode = planner.plan(
        "a1h8", moving_piece="bishop", high_lift=True,
        protected_squares=protected,
    )
    protected_xy = {planner.square_to_coords(square) for square in protected}
    loaded_xy = []
    for code in _codes(gcode):
        if code.startswith("G1"):
            mx = re.search(r"X(-?\d+\.?\d*)", code)
            my = re.search(r"Y(-?\d+\.?\d*)", code)
            loaded_xy.append((float(mx.group(1)), float(my.group(1))))
    assert loaded_xy
    assert protected_xy.isdisjoint(loaded_xy)
    assert loaded_xy[-1] == planner.square_to_coords("h8")

def test_graveyard_route_uses_another_gate_when_edge_square_is_protected(planner):
    gcode = planner.plan(
        "d4e5", is_capture=True, moving_piece="pawn", captured_piece="pawn",
        protected_squares=("h1",),
    )
    h1x, h1y = planner.square_to_coords("h1")
    assert f"G1 X{h1x:.2f} Y{h1y:.2f}" not in gcode


# -------------------------- off-board bookkeeping -------------------------- #

def test_graveyard_slots_advance(planner):
    """Captured pieces are distributed across the temporary flat area."""
    first = planner.plan("d4e5", is_capture=True)
    second = planner.plan("a1a2", is_capture=True)
    x0, y0 = cfg.GRAVEYARD_POSITIONS[0]
    x1, y1 = cfg.GRAVEYARD_POSITIONS[1]
    assert f"X{x0:.2f} Y{y0:.2f}" in first
    assert f"X{x1:.2f} Y{y1:.2f}" in second

def test_reset_rewinds_the_graveyard(planner):
    planner.plan("d4e5", is_capture=True)
    planner.reset()
    x, y = cfg.GRAVEYARD_POSITIONS[0]
    assert f"X{x:.2f} Y{y:.2f}" in planner.plan("d4e5", is_capture=True)

def test_promotion_fetches_a_queen_of_the_right_colour(planner):
    """Rank 8 is a white promotion, rank 1 a black one -- pure geometry."""
    white = planner.plan("a7a8q", move_type="promotion")
    black = planner.plan("a2a1q", move_type="promotion")
    wx, wy = cfg.QUEEN_RESERVE_POSITIONS["white"][0]
    bx, by = cfg.QUEEN_RESERVE_POSITIONS["black"][0]
    assert f"X{wx:.2f} Y{wy:.2f}" in white
    assert "white queen" in white
    assert f"X{bx:.2f} Y{by:.2f}" in black
    assert "black queen" in black

def test_only_one_spare_queen_per_colour_is_configured(planner):
    planner._queen_reserve_coords("white")
    with pytest.raises(ValueError, match="Out of spare white queens"):
        planner._queen_reserve_coords("white")

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
    height. Descending to a piece's grip Z is only allowed straight down over the
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
            allowed_carry_z = {cfg.Z_SAFE} | {
                profile["z"] - cfg.LIFT_LOW
                for profile in cfg.PIECE_GRIP_PROFILES.values()
            }
            assert z in allowed_carry_z, (
                f"{label}: XY moves to ({nx}, {ny}) at unsafe Z{z}; "
                f"allowed carry heights are {sorted(allowed_carry_z)}"
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

def test_motion_context_reports_profiles_and_protected_squares():
    orch = _orch()
    moving, captured, protected = orch._motion_context("g1f3")
    assert moving == "knight"
    assert captured is None
    assert set(protected) == {"d1", "e1", "d8", "e8"}

def test_motion_context_reports_captured_piece_before_apply():
    orch = _orch("4k3/8/8/4r3/3B4/8/8/4K3 w - - 0 1")
    moving, captured, protected = orch._motion_context("d4e5")
    assert moving == "bishop"
    assert captured == "rook"
    assert set(protected) == {"e1", "e8"}

def test_orchestrator_announces_every_human_turn(monkeypatch):
    class RecordingSpeaker:
        def __init__(self):
            self.messages = []

        def say(self, text):
            self.messages.append(text)

    eng = ChessEngine()
    speaker = RecordingSpeaker()
    eng.speaker = speaker
    monkeypatch.setattr(eng, "ai_move", lambda: "e7e5")
    orch = Orchestrator(
        engine=eng,
        voice=MockVoice(script=["e2e4"]),
        planner=MotionPlanner(),
        serial=SerialLink(),
    )

    orch.run(max_turns=1)

    assert speaker.messages[:2] == ["New game. You are White.", "Your turn."]
    ai_announcement = speaker.messages.index("A I plays Pawn to e5")
    assert speaker.messages[ai_announcement + 1] == "Your turn."
    assert speaker.messages.count("Your turn.") == 2
