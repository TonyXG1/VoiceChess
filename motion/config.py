"""Role 3 - the machine's physical constants. THE calibration file.

Every number the gantry cares about lives here, so tuning the real build never
means editing planner logic. After the first powered run you should only ever
need to touch the four values marked ``[MEASURE]``.

Coordinate system (matches the frame declared in ``fluidnc/config.yaml``):

    X  0 -> 540 mm   from a1's centre toward the h-file
    Y  0 -> 550 mm   from a1's centre toward rank 8
    Z -170 -> 0 mm   UP IS POSITIVE -- Z0 is the top of travel, "claw fully
                     retracted", with 115 mm clearance above the board.
    A                the claw servo, in degrees (an axis, not a spindle)

NOTHING IS HOMED right now: X and Y have no limit switches and Z's is not wired,
so all four axes are ``cycle: 0`` and ``$H`` has nothing to home (``--no-home``
is mandatory). Every coordinate below is therefore relative to wherever the
machine happened to sit at power-on. Park the Z carriage at the TOP of its
travel before switching on or resetting so power-on Z0 matches the frame below.
The active work coordinates must also read Z0 there; clear any old Z offset
at this position as documented in fluidnc/ESP32_README.md.

Confirmed board geometry: 60 mm squares, a 480 x 480 mm playing area, and a
20 mm border on all four sides (520 x 520 mm overall). The centre of a1 is
the origin; square centres span 0..420 mm and outer board edges -50..470 mm.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Machine envelope
# --------------------------------------------------------------------------- #

# Measured usable positive travel from the starting point (a1's centre).
# These are coordinate limits, not total rail lengths. FluidNC soft limits
# remain disabled until the axes can be homed.
X_MAX = 540.0
Y_MAX = 550.0
Z_AXIS_LENGTH = 170.0

# Where the gantry parks between moves: above the centre of a1 at Z_SAFE.
PARK_X = 0.0
PARK_Y = 0.0


# --------------------------------------------------------------------------- #
# Board geometry
# --------------------------------------------------------------------------- #

SQUARE_X = 60.0
SQUARE_Y = 60.0
BOARD_BORDER = 20.0   # frame outside the playing area, on each of four sides

# Work coordinates of the CENTRE of a1. Start directly above that centre
# and set G54 X0 Y0 there. All other square centres are whole-square offsets.
BOARD_ORIGIN_X = 0.0
BOARD_ORIGIN_Y = 0.0

BOARD_SPAN_X = 8 * SQUARE_X     # 480.0, playing area
BOARD_SPAN_Y = 8 * SQUARE_Y     # 480.0, playing area
BOARD_OUTER_MIN_X = BOARD_ORIGIN_X - 0.5 * SQUARE_X - BOARD_BORDER
BOARD_OUTER_MIN_Y = BOARD_ORIGIN_Y - 0.5 * SQUARE_Y - BOARD_BORDER
BOARD_OUTER_MAX_X = BOARD_OUTER_MIN_X + BOARD_SPAN_X + 2 * BOARD_BORDER
BOARD_OUTER_MAX_Y = BOARD_OUTER_MIN_Y + BOARD_SPAN_Y + 2 * BOARD_BORDER


# --------------------------------------------------------------------------- #
# Z heights
# --------------------------------------------------------------------------- #

Z_TOP = 0.0                       # power-on position, claw fully retracted
Z_BOTTOM = Z_TOP - Z_AXIS_LENGTH  # -170.0, full mechanical travel (below board)

# Requested top clearance: physically set the gripper's lowest point 115 mm
# above the playing surface at Z0. Changing this value does not move the top.
# Clearance is not the same as the 170 mm axis travel.
Z_TOP_CLEARANCE = 115.0
Z_BOARD = Z_TOP - Z_TOP_CLEARANCE  # -115.0

# [MEASURE] Height of the gripper's lowest point above the board at pickup.
# 18 mm remains an assumption, not established by the 30 mm internal depth.
# Record the working pickup Z; GRIP_OFFSET = pickup Z - Z_BOARD.
GRIP_OFFSET = 18.0

# Loaded carry along a path chess rules guarantee is empty. Only has to clear
# the board surface itself.
LIFT_LOW = 15.0

# Measured piece heights in mm. Only the tallest one actually drives motion --
# LIFT_HIGH has to clear it -- but the whole set is recorded here so that number
# is derived and auditable instead of a magic constant nobody can re-check.
PIECE_HEIGHTS = {
    "king": 95.0,
    "queen": 75.0,
    "bishop": 65.0,
    "knight": 58.0,
    "rook": 46.0,
    "pawn": 45.0,
}

TALLEST_PIECE = max(PIECE_HEIGHTS.values())    # 95.0 -- the king

# Loaded carry across occupied squares: clear the tallest piece plus 7 mm.
# With 115 mm top clearance and an 18 mm grip offset, only 97 mm of lift
# is available. This required 102 mm lift DOES NOT FIT: _check() blocks
# automatic play until the measured geometry supports it. Do not clamp
# the target or reduce the clearance requirement to hide the collision.
LIFT_MARGIN = 7.0
LIFT_HIGH = TALLEST_PIECE + LIFT_MARGIN        # 102.0

Z_GRIP = Z_BOARD + GRIP_OFFSET            # -97.0  claw closed around a piece
Z_CARRY_LOW = Z_GRIP + LIFT_LOW           # -82.0  loaded, empty path
Z_CARRY_HIGH = Z_GRIP + LIFT_HIGH         #  +5.0  UNREACHABLE; rejected below
Z_SAFE = Z_TOP                           #   0.0  empty claw travels at the top


# --------------------------------------------------------------------------- #
# Claw (SG90 9g micro-servo on the A axis, gpio.19, 1000-2000 us @ 50 Hz)
# --------------------------------------------------------------------------- #

# Physical jaw geometry. The inner width is what a piece base has to fit inside;
# the outer width is what has to clear a neighbouring piece, which is where
# GRAVEYARD_DX below comes from.
CLAW_OPEN_MM = 60.0    # outer width, jaws open
CLAW_GRIP_MM = 45.0    # inner width, jaws closed on a piece
CLAW_INTERNAL_DEPTH_MM = 30.0  # reported internal depth; not a pickup offset

# Degrees, mapped by FluidNC's rc_servo across the A axis travel to the pulse
# range (A0 -> 1000 us, A90 -> 2000 us). Because the servo is declared as an
# AXIS and not a spindle, these commands sit in the motion queue and are ordered
# against the XY/Z moves for free -- no spindle-sync guesswork.
CLAW_OPEN_A = 0.0      # 1000 us; CLAW_OPEN_MM outer
CLAW_CLOSED_A = 45.0   # 1500 us; gripping a piece at CLAW_GRIP_MM inner

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

# NOT YET RECALIBRATED for the measured X540/Y550 envelope. The existing
# grid reaches X686, and the black queen reserve reaches Y590. Keep the
# envelope checks active until a reachable storage layout is measured.

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
    # over the king. Needs GRIP_OFFSET + LIFT_HIGH = 120 mm of top clearance.
    if Z_CARRY_HIGH > Z_TOP:
        raise ValueError(
            f"Z envelope too short: top clearance is {Z_TOP_CLEARANCE:.0f} mm; "
            f"gripping at {Z_GRIP} and lifting "
            f"{LIFT_HIGH} mm reaches Z{Z_CARRY_HIGH:+.1f}, above the Z_TOP "
            f"limit of {Z_TOP}. Need at least "
            f"{GRIP_OFFSET + LIFT_HIGH:.0f} mm above the playing surface with "
            f"the current grip offset and piece heights. Re-measure or change "
            f"the physical geometry before automatic play."
        )
    if not (Z_BOTTOM <= Z_BOARD <= Z_TOP):
        raise ValueError(
            f"Z_BOARD ({Z_BOARD}) is outside the Z{Z_BOTTOM}..Z{Z_TOP} envelope."
        )
    if LIFT_HIGH < TALLEST_PIECE:
        raise ValueError(
            f"LIFT_HIGH ({LIFT_HIGH}) does not clear the tallest piece "
            f"({TALLEST_PIECE} mm king) -- carried pieces will sweep the board."
        )

    # Every off-board zone must be reachable and clear of the board's border.
    board_right = BOARD_OUTER_MAX_X
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

    # Slots must be spaced wider than the claw's open outer width, or the jaws
    # clip the piece in the next slot on the way down.
    if GRAVEYARD_DX <= CLAW_OPEN_MM:
        raise ValueError(
            f"GRAVEYARD_DX ({GRAVEYARD_DX}) is not wider than the claw's open "
            f"outer width ({CLAW_OPEN_MM}) -- the jaws would clip the piece in "
            f"the neighbouring slot. NOTE: this assumes the jaws open along X; "
            f"if they open along Y, swap GRAVEYARD_DX and GRAVEYARD_DY."
        )


_check()
