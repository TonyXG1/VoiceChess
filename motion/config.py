"""Role 3 - the machine's physical constants. THE calibration file.

Every number the gantry cares about lives here, so tuning the real build never
means editing planner logic.

Coordinate system (matches the frame declared in ``fluidnc/config.yaml``):

    X  0 -> 535 mm   from a1's centre along the a-file toward a8
    Y  0 -> 545 mm   from a1's centre along rank 1 toward h1
    Z  0 -> 170 mm   DOWN IS POSITIVE -- Z0 is the top of travel, "claw fully
                     retracted", with 115 mm clearance above the board.
    A                the claw servo, in degrees (an axis, not a spindle)

NOTHING IS HOMED right now: X and Y have no limit switches and Z's is not wired,
so all four axes are ``cycle: 0`` and ``$H`` has nothing to home (``--no-home``
is mandatory). Every coordinate below is therefore relative to wherever the
machine happened to sit at power-on. Park the Z carriage at the TOP of its
travel before switching on or resetting so power-on Z0 matches the frame below.
The active work coordinates must also read Z0 there; clear any old Z offset
at this position as documented in fluidnc/ESP32_README.md.

Confirmed board geometry: 58 mm squares, a 464 x 464 mm playing area, and a
20 mm border on all four sides (504 x 504 mm overall). The centre of a1 is
the origin; square centres span 0..406 mm and outer board edges -49..455 mm.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Machine envelope
# --------------------------------------------------------------------------- #

# Measured usable positive travel from the starting point (a1's centre).
# These are coordinate limits, not total rail lengths. FluidNC soft limits
# remain disabled until the axes can be homed.
X_MAX = 535.0
Y_MAX = 545.0
Z_AXIS_LENGTH = 170.0

# Where the gantry parks between moves: above the centre of a1 at Z_SAFE.
PARK_X = 0.0
PARK_Y = 0.0


# --------------------------------------------------------------------------- #
# Board geometry
# --------------------------------------------------------------------------- #

SQUARE_X = 58.0
SQUARE_Y = 58.0
BOARD_BORDER = 20.0   # frame outside the playing area, on each of four sides

# Work coordinates of the CENTRE of a1. Start directly above that centre
# and set G54 X0 Y0 there. All other square centres are whole-square offsets.
BOARD_ORIGIN_X = 0.0
BOARD_ORIGIN_Y = 0.0

BOARD_SPAN_X = 8 * SQUARE_X     # 464.0, playing area
BOARD_SPAN_Y = 8 * SQUARE_Y     # 464.0, playing area
BOARD_OUTER_MIN_X = BOARD_ORIGIN_X - 0.5 * SQUARE_X - BOARD_BORDER
BOARD_OUTER_MIN_Y = BOARD_ORIGIN_Y - 0.5 * SQUARE_Y - BOARD_BORDER
BOARD_OUTER_MAX_X = BOARD_OUTER_MIN_X + BOARD_SPAN_X + 2 * BOARD_BORDER
BOARD_OUTER_MAX_Y = BOARD_OUTER_MIN_Y + BOARD_SPAN_Y + 2 * BOARD_BORDER


# --------------------------------------------------------------------------- #
# Z heights
# --------------------------------------------------------------------------- #

Z_TOP = 0.0                       # power-on position, claw fully retracted
Z_BOTTOM = Z_TOP + Z_AXIS_LENGTH  # 170.0, full mechanical travel (below board)

# Requested top clearance: physically set the gripper's lowest point 115 mm
# above the playing surface at Z0. Changing this value does not move the top.
# Clearance is not the same as the 170 mm axis travel.
Z_TOP_CLEARANCE = 115.0
Z_BOARD = Z_TOP + Z_TOP_CLEARANCE  # 115.0

# Loaded carry along a path chess rules guarantee is empty. This lifts each
# piece 15 mm from its own measured pickup depth.
LIFT_LOW = 15.0

# Measured piece heights in mm.
PIECE_HEIGHTS = {
    "king": 76.0,
    "queen": 75.0,
    "bishop": 65.0,
    "knight": 58.0,
    "rook": 47.0,
    "pawn": 45.0,
}

# Measured pickup depth and claw angle for each physical piece. Z is absolute
# and positive-down: the planner descends from Z0 to this value to grip.
# A is the SG90 axis angle used while that piece is held.
PIECE_GRIP_PROFILES = {
    "pawn": {"z": 85.0, "a": 62.0},
    "knight": {"z": 75.0, "a": 76.0},
    "bishop": {"z": 80.0, "a": 43.0},
    "rook": {"z": 90.0, "a": 43.0},
    "queen": {"z": 67.0, "a": 35.0},
    "king": {"z": 67.0, "a": 35.0},
}

# A high transfer travels at the physical top. At Z0, the piece base is the
# pickup-Z distance above the board. King and queen squares are routed around,
# so the tallest piece a high path may cross is the 65 mm bishop. The shallowest
# measured grip is Z67, leaving 2 mm of clearance.
PROTECTED_PIECE_TYPES = frozenset({"king", "queen"})
TALLEST_UNPROTECTED_PIECE = max(
    height for piece, height in PIECE_HEIGHTS.items()
    if piece not in PROTECTED_PIECE_TYPES
)
Z_HIGH_CARRY = Z_TOP
Z_SAFE = Z_TOP


# --------------------------------------------------------------------------- #
# Claw (SG90 9g micro-servo on the A axis, gpio.19, 1000-2000 us @ 50 Hz)
# --------------------------------------------------------------------------- #

# Physical jaw geometry. The inner width is what a piece base has to fit inside;
# the outer width is what has to clear a neighbouring capture position, which
# sets GRAVEYARD_SPACING below.
CLAW_OPEN_MM = 60.0    # outer width, jaws open
CLAW_GRIP_MM = 45.0    # inner width, jaws closed on a piece
CLAW_INTERNAL_DEPTH_MM = 30.0  # reported internal depth; not a pickup offset

# Degrees, mapped by FluidNC's rc_servo across the A axis travel to the pulse
# range (A0 -> 1000 us, A90 -> 2000 us). Because the servo is declared as an
# AXIS and not a spindle, these commands sit in the motion queue and are ordered
# against the XY/Z moves for free -- no spindle-sync guesswork.
CLAW_OPEN_A = 0.0      # 1000 us; CLAW_OPEN_MM outer

# Seconds. GRBL/FluidNC read G4 P as SECONDS -- P500 would dwell for 8 minutes.
CLAW_DWELL_S = 0.5


# --------------------------------------------------------------------------- #
# Feed rates (mm/min)
# --------------------------------------------------------------------------- #

# Only G1 carries a feed word: G0 rapids take their speed from the YAML's
# max_rate_mm_per_min, and an F on a G0 line just mutates the modal feed.
F_CARRY = 3000.0       # loaded -- a piece is standing in the claw
F_EMPTY = 3000.0       # unloaded G1 moves (currently unused; rapids handle these)


# --------------------------------------------------------------------------- #
# Off-board zones
# --------------------------------------------------------------------------- #

# GRAVEYARD: temporary flat L-shaped capture area. Eight positions form a
# column parallel to the h-file at Y485; another eight form a row beyond rank 8
# at X485. The 62 mm spacing clears the claw's 60 mm open outer width.
# Clear the area and reset the planner after 16 captures.
GRAVEYARD_SPACING = 62.0
GRAVEYARD_COLUMN_Y = 485.0
GRAVEYARD_ROW_X = 485.0
GRAVEYARD_POSITIONS = (
    tuple((i * GRAVEYARD_SPACING, GRAVEYARD_COLUMN_Y) for i in range(8))
    + tuple((GRAVEYARD_ROW_X, i * GRAVEYARD_SPACING) for i in range(8))
)

# Captures arrive at the high carry height Z0. Descend to the measured Z40,
# open the claw, and let the piece fall onto the temporary flat area.
# This deliberately differs from normal placement, which uses the captured
# piece's own pickup depth.
GRAVEYARD_RELEASE_Z = 40.0
GRAVEYARD_DROP_MM = GRAVEYARD_RELEASE_Z - Z_HIGH_CARRY

# QUEEN RESERVE: one spare per colour in the unused positive-XY corner. Both
# points clear every capture position and each other by more than the 60 mm
# open-claw width. Add more coordinates here if more physical queens are added.
QUEEN_RESERVE_POSITIONS = {
    "white": ((403.0, 540.0),),
    "black": ((530.0, 540.0),),
}


# --------------------------------------------------------------------------- #
# Self-check -- fails at import, not mid-game
# --------------------------------------------------------------------------- #

def _check() -> None:
    """Validate that the measured build can actually reach every position."""
    if not (Z_TOP <= Z_BOARD <= Z_BOTTOM):
        raise ValueError(
            f"Z_BOARD ({Z_BOARD}) is outside the Z{Z_TOP}..Z{Z_BOTTOM} envelope."
        )
    if set(PIECE_GRIP_PROFILES) != set(PIECE_HEIGHTS):
        raise ValueError(
            "PIECE_GRIP_PROFILES must contain exactly the six measured piece types."
        )
    for piece, profile in PIECE_GRIP_PROFILES.items():
        grip_z = profile["z"]
        grip_a = profile["a"]
        if not (Z_TOP <= grip_z <= Z_BOARD):
            raise ValueError(
                f"{piece} pickup Z{grip_z} is outside Z{Z_TOP}..Z{Z_BOARD}."
            )
        if not (0.0 <= grip_a <= 90.0):
            raise ValueError(f"{piece} grip A{grip_a} is outside A0..A90.")
        if grip_z - LIFT_LOW < Z_TOP:
            raise ValueError(
                f"{piece} cannot lift {LIFT_LOW} mm from pickup Z{grip_z}."
            )

    minimum_high_clearance = min(
        profile["z"] for profile in PIECE_GRIP_PROFILES.values()
    )
    if minimum_high_clearance <= TALLEST_UNPROTECTED_PIECE:
        raise ValueError(
            f"High carry clears only {minimum_high_clearance:.0f} mm, but an "
            f"unprotected piece can be {TALLEST_UNPROTECTED_PIECE:.0f} mm tall."
        )

    # Every capture position must be reachable and outside the board border.
    for i, (x, y) in enumerate(GRAVEYARD_POSITIONS):
        if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
            raise ValueError(
                f"graveyard slot {i} at X{x} Y{y} is outside the "
                f"{X_MAX} x {Y_MAX} envelope."
            )
        inside_board = (
            BOARD_OUTER_MIN_X <= x <= BOARD_OUTER_MAX_X and
            BOARD_OUTER_MIN_Y <= y <= BOARD_OUTER_MAX_Y
        )
        if inside_board:
            raise ValueError(
                f"graveyard slot {i} at X{x} Y{y} overlaps the board."
            )
        outside_clearance = max(
            x - BOARD_OUTER_MAX_X,
            y - BOARD_OUTER_MAX_Y,
            BOARD_OUTER_MIN_X - x,
            BOARD_OUTER_MIN_Y - y,
        )
        if outside_clearance < CLAW_OPEN_MM / 2:
            raise ValueError(
                f"graveyard slot {i} leaves only {outside_clearance:.1f} mm "
                f"between its centre and the board; the open claw needs "
                f"{CLAW_OPEN_MM / 2:.1f} mm."
            )

    if not GRAVEYARD_POSITIONS:
        raise ValueError("Graveyard must have at least one capture position.")

    # Check every pair because the L's two arms also approach each other at
    # their far ends; adjacent positions must clear the open claw everywhere.
    for i, (x1, y1) in enumerate(GRAVEYARD_POSITIONS):
        for x2, y2 in GRAVEYARD_POSITIONS[i + 1:]:
            separation = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if separation <= CLAW_OPEN_MM:
                raise ValueError(
                    f"Graveyard positions are only {separation:.1f} mm apart; "
                    f"the open claw is {CLAW_OPEN_MM} mm wide."
                )

    shallowest_grip = min(
        profile["z"] for profile in PIECE_GRIP_PROFILES.values()
    )
    if not (Z_HIGH_CARRY <= GRAVEYARD_RELEASE_Z <= shallowest_grip):
        raise ValueError(
            f"GRAVEYARD_RELEASE_Z ({GRAVEYARD_RELEASE_Z}) must be between the "
            f"high carry Z{Z_HIGH_CARRY} and shallowest pickup Z{shallowest_grip}."
        )

    # Reserve queens must also be reachable, off-board, and separated from all
    # capture and reserve positions by more than the open claw width.
    occupied = [(f"graveyard slot {i}", point)
                for i, point in enumerate(GRAVEYARD_POSITIONS)]
    for colour, positions in QUEEN_RESERVE_POSITIONS.items():
        if not positions:
            raise ValueError(f"No {colour} queen reserve position is configured.")
        for i, (x, y) in enumerate(positions):
            label = f"{colour} queen {i}"
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise ValueError(
                    f"{label} at X{x} Y{y} is outside the "
                    f"{X_MAX} x {Y_MAX} envelope."
                )
            if (BOARD_OUTER_MIN_X <= x <= BOARD_OUTER_MAX_X and
                    BOARD_OUTER_MIN_Y <= y <= BOARD_OUTER_MAX_Y):
                raise ValueError(f"{label} at X{x} Y{y} overlaps the board.")
            for other_label, (ox, oy) in occupied:
                separation = ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5
                if separation <= CLAW_OPEN_MM:
                    raise ValueError(
                        f"{label} is only {separation:.1f} mm from {other_label}; "
                        f"the open claw is {CLAW_OPEN_MM} mm wide."
                    )
            occupied.append((label, (x, y)))


_check()
