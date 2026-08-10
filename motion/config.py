"""Role 3 - the machine's physical constants. THE calibration file.

Every number the gantry cares about lives here, so tuning the real build never
means editing planner logic. After the first powered run you should only ever
need to touch the four values marked ``[MEASURE]``.

Coordinate system (matches FluidNC after ``$H``):

    X  0 -> 715 mm   left to right   (a-file .. h-file, then the off-board zones)
    Y  0 -> 715 mm   near to far     (rank 1 .. rank 8, then graveyard/reserve)
    Z  0 -> -170 mm  DOWN IS NEGATIVE -- Z homes to the top, so the whole work
                     envelope is negative and Z0 is "claw fully retracted".
    A                the claw servo, in degrees (an axis, not a spindle)

Board geometry is derived from the measured spans of the real board rather than
the nominal square size: 456 mm across a-h is 57.0 mm/square, 459 mm across 1-8
is 57.375 mm/square. Using the nominal 58 mm instead would drift ~8 mm by the
h-file -- a guaranteed miss given the claw's 45 mm inner opening.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Machine envelope
# --------------------------------------------------------------------------- #

# Rail lengths are 720 mm; soft limits sit just inside so a rapid can never
# slam the ends. Z is 170 mm of rack-and-pinion travel.
X_MAX = 715.0
Y_MAX = 715.0
Z_AXIS_LENGTH = 170.0

# Where the gantry parks between moves: out of the player's line of sight.
PARK_X = 0.0
PARK_Y = 0.0


# --------------------------------------------------------------------------- #
# Board geometry
# --------------------------------------------------------------------------- #

SQUARE_X = 57.0        # 456 mm across a-h / 8
SQUARE_Y = 57.375      # 459 mm across 1-8 / 8

# [MEASURE] Machine coordinates of the board's a1 CORNER (not the square center).
# Jog the claw to the outside corner of a1 after homing and read the DRO.
BOARD_ORIGIN_X = 0.0
BOARD_ORIGIN_Y = 0.0

BOARD_SPAN_X = 8 * SQUARE_X     # 456.0
BOARD_SPAN_Y = 8 * SQUARE_Y     # 459.0


# --------------------------------------------------------------------------- #
# Z heights
# --------------------------------------------------------------------------- #

Z_TOP = 0.0            # homed position, claw fully retracted

# [MEASURE] Z at which the claw tips touch the board surface. Jog down slowly
# with a sheet of paper under the tips and stop when it just binds.
Z_BOARD = -150.0

# [MEASURE-ish] How far up the piece the claw grips. Wants to be on the base,
# below the widest part of the body, and above any felt pad.
GRIP_OFFSET = 18.0

# Loaded carry along a path chess rules guarantee is empty. Only has to clear
# the board surface itself.
LIFT_LOW = 15.0

# Loaded carry across occupied squares. Must clear the tallest piece: the king
# is 95 mm, plus 15 mm of margin.
LIFT_HIGH = 110.0

TALLEST_PIECE = 95.0   # king; for the assertion below and for documentation

Z_GRIP = Z_BOARD + GRIP_OFFSET            # -132.0  claw closed around a piece
Z_CARRY_LOW = Z_GRIP + LIFT_LOW           # -117.0  loaded, empty path
Z_CARRY_HIGH = Z_GRIP + LIFT_HIGH         #  -22.0  loaded, over other pieces
Z_SAFE = Z_CARRY_HIGH                     #  -22.0  empty claw always travels here


# --------------------------------------------------------------------------- #
# Claw (RC micro-servo on the A axis, gpio.19, 1000-2000 us @ 50 Hz)
# --------------------------------------------------------------------------- #

# Degrees, mapped by FluidNC's rc_servo to the pulse range. Because the servo is
# declared as an AXIS and not a spindle, these commands sit in the motion queue
# and are ordered against the XY/Z moves for free -- no spindle-sync guesswork.
CLAW_OPEN_A = 0.0      # 60 mm outer opening
CLAW_CLOSED_A = 45.0   # gripping a piece; 45 mm inner opening at full close

# Seconds. GRBL/FluidNC read G4 P as SECONDS -- P500 would dwell for 8 minutes.
CLAW_DWELL_S = 0.5


# --------------------------------------------------------------------------- #
# Feed rates (mm/min)
# --------------------------------------------------------------------------- #

# Only G1 carries a feed word: G0 rapids take their speed from the YAML's
# max_rate_mm_per_min, and an F on a G0 line just mutates the modal feed.
F_CARRY = 1200.0       # loaded -- a piece is standing in the claw
F_EMPTY = 3000.0       # unloaded G1 moves (currently unused; rapids handle these)


# --------------------------------------------------------------------------- #
# Off-board zones
# --------------------------------------------------------------------------- #

# GRAVEYARD: a 4 x 8 grid, not a single point. Dropping all 30 capturable
# pieces on one spot piles them up until they topple into the claw's path.
# X spacing (62) exceeds the claw's 60 mm open outer width so the jaws never
# graze a neighbour on the way down. NOTE: assumes the jaws open along X --
# if they open along Y, swap GRAVEYARD_DX and GRAVEYARD_DY.
GRAVEYARD_X0 = 500.0
GRAVEYARD_Y0 = 40.0
GRAVEYARD_DX = 62.0
GRAVEYARD_DY = 55.0
GRAVEYARD_COLS = 4
GRAVEYARD_ROWS = 8     # 32 slots >= the 30 pieces that can ever be captured

# QUEEN RESERVE: one row per colour. The old single-point reserve handed a
# white queen to a black promotion.
QUEEN_RESERVE_X0 = 500.0
QUEEN_RESERVE_DX = 62.0
QUEEN_RESERVE_SLOTS = 4
QUEEN_RESERVE_Y_WHITE = 520.0
QUEEN_RESERVE_Y_BLACK = 590.0


# --------------------------------------------------------------------------- #
# Self-check -- fails at import, not mid-game
# --------------------------------------------------------------------------- #

def _check() -> None:
    """Validate that the measured build can actually reach every position."""
    # The single most likely build failure: not enough Z drop to lift a piece
    # over the king. Needs GRIP_OFFSET + LIFT_HIGH = 128 mm of usable travel.
    if Z_CARRY_HIGH > Z_TOP:
        raise ValueError(
            f"Z envelope too short: gripping at {Z_GRIP} and lifting "
            f"{LIFT_HIGH} mm reaches Z{Z_CARRY_HIGH:+.1f}, above the Z_TOP "
            f"limit of {Z_TOP}. Need at least "
            f"{GRIP_OFFSET + LIFT_HIGH:.0f} mm of drop below the homed "
            f"position; lower Z_BOARD or shorten LIFT_HIGH."
        )
    if abs(Z_BOARD) > Z_AXIS_LENGTH:
        raise ValueError(
            f"Z_BOARD ({Z_BOARD}) is deeper than the {Z_AXIS_LENGTH} mm Z axis."
        )
    if LIFT_HIGH < TALLEST_PIECE:
        raise ValueError(
            f"LIFT_HIGH ({LIFT_HIGH}) does not clear the tallest piece "
            f"({TALLEST_PIECE} mm king) -- carried pieces will sweep the board."
        )

    # Every off-board zone must be reachable and clear of the board itself.
    board_right = BOARD_ORIGIN_X + BOARD_SPAN_X
    zones = {
        "graveyard": (
            GRAVEYARD_X0 + (GRAVEYARD_COLS - 1) * GRAVEYARD_DX,
            GRAVEYARD_Y0 + (GRAVEYARD_ROWS - 1) * GRAVEYARD_DY,
            GRAVEYARD_X0,
        ),
        "queen reserve": (
            QUEEN_RESERVE_X0 + (QUEEN_RESERVE_SLOTS - 1) * QUEEN_RESERVE_DX,
            QUEEN_RESERVE_Y_BLACK,
            QUEEN_RESERVE_X0,
        ),
    }
    for name, (max_x, max_y, min_x) in zones.items():
        if max_x > X_MAX or max_y > Y_MAX:
            raise ValueError(
                f"{name} reaches X{max_x} Y{max_y}, outside the "
                f"{X_MAX} x {Y_MAX} envelope."
            )
        if min_x < board_right:
            raise ValueError(
                f"{name} starts at X{min_x}, overlapping the board "
                f"(which ends at X{board_right})."
            )

    if GRAVEYARD_COLS * GRAVEYARD_ROWS < 30:
        raise ValueError("Graveyard has fewer than 30 slots; captures would collide.")


_check()
