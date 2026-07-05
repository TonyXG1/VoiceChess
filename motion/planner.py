"""Role 3 - motion planning: chess square -> physical XY mm -> G-code.

Real implementation merged from Role 3's repo (Role3-demo, 2026-07-05).
plan() returns one newline-joined G-code string; the orchestrator splits it
into lines for the serial link. Physical constants (50mm squares, graveyard,
queen reserve) are Role 3's — sanity-check them against the final build
before first powered run (see CLAUDE.md "Still open" notes).
"""


class MotionPlanner:
    def __init__(self):
        # Machine dimensions and settings
        self.SQUARE_SIZE = 50.0
        self.OFFSET_X = 25.0
        self.OFFSET_Y = 25.0

        # Z-axis heights
        self.Z_TRAVEL = 0.0
        self.Z_GRAB = -40.0

        # Speeds
        self.F_RAPID = 4000
        self.F_MOVE = 1000

        # Claw commands
        self.CLAW_CLOSE = "M3"
        self.CLAW_OPEN = "M5"

        # External Physical Zones
        self.GRAVEYARD_X = 420.0
        self.GRAVEYARD_Y = 200.0
        self.QUEEN_RESERVE_X = 480.0 # Location where physical extra Queens are kept
        self.QUEEN_RESERVE_Y = 200.0

    def square_to_coords(self, square):
        """Converts chess notation (e.g., e2) to physical X, Y coordinates."""
        # --- STRICT HARDWARE SAFETY VALIDATION ---
        if not isinstance(square, str) or len(square) != 2:
            raise ValueError(f"Square '{square}' is invalid!")

        col_char = square[0].lower()
        row_char = square[1]

        if col_char not in 'abcdefgh' or row_char not in '12345678':
            raise ValueError(f"Square '{square}' is physically out of bounds (a-h, 1-8)!")
        # -----------------------------------------

        col_idx = ord(col_char) - ord('a')
        row_idx = int(row_char) - 1

        x = self.OFFSET_X + (col_idx * self.SQUARE_SIZE)
        y = self.OFFSET_Y + (row_idx * self.SQUARE_SIZE)
        return round(x, 2), round(y, 2)

    def generate_pick_and_place(self, start_sq, end_sq):
        """Generates G-code to pick a piece and place it at the target."""
        start_x, start_y = self.square_to_coords(start_sq)
        end_x, end_y = self.square_to_coords(end_sq)

        seq = []
        seq.append(f"G0 X{start_x} Y{start_y} F{self.F_RAPID} ; Move above {start_sq}")
        seq.append(f"G0 Z{self.Z_GRAB} ; Lower claw")
        seq.append(f"{self.CLAW_CLOSE} ; Grab piece")
        seq.append("G4 P500 ; Dwell for 0.5s")
        seq.append(f"G0 Z{self.Z_TRAVEL} ; Raise claw")

        seq.append(f"G1 X{end_x} Y{end_y} F{self.F_MOVE} ; Move to {end_sq}")
        seq.append(f"G0 Z{self.Z_GRAB} ; Lower claw")
        seq.append(f"{self.CLAW_OPEN} ; Release piece")
        seq.append("G4 P500 ; Dwell for 0.5s")
        seq.append(f"G0 Z{self.Z_TRAVEL} ; Raise claw")
        return seq

    def remove_to_graveyard(self, target_sq):
        """Helper sequence to move a piece from a square to the graveyard."""
        tx, ty = self.square_to_coords(target_sq)
        seq = []
        seq.append(f"G0 X{tx} Y{ty} F{self.F_RAPID} ; Go to piece at {target_sq}")
        seq.append(f"G0 Z{self.Z_GRAB}")
        seq.append(f"{self.CLAW_CLOSE}")
        seq.append("G4 P500")
        seq.append(f"G0 Z{self.Z_TRAVEL}")
        seq.append(f"G0 X{self.GRAVEYARD_X} Y{self.GRAVEYARD_Y} F{self.F_RAPID} ; Move to graveyard")
        seq.append(f"G0 Z{self.Z_GRAB}")
        seq.append(f"{self.CLAW_OPEN}")
        seq.append("G4 P500")
        seq.append(f"G0 Z{self.Z_TRAVEL}")
        return seq

    def plan(self, uci_move, move_type="standard", is_capture=False):
        """Main API method called by the Orchestrator."""
        move_from = uci_move[:2]
        move_to = uci_move[2:4]
        final_gcode = []

        # 1. CASTLING (Rokada)
        if move_type == "castling":
            final_gcode.append("; --- CASTLING ---")
            final_gcode.extend(self.generate_pick_and_place(move_from, move_to)) # Move King

            # Mathematically determine Rook positions based on King's destination
            if move_to == 'g1': rook_from, rook_to = 'h1', 'f1'
            elif move_to == 'c1': rook_from, rook_to = 'a1', 'd1'
            elif move_to == 'g8': rook_from, rook_to = 'h8', 'f8'
            elif move_to == 'c8': rook_from, rook_to = 'a8', 'd8'
            else: raise ValueError(f"Invalid castling target '{move_to}'!")

            final_gcode.extend(self.generate_pick_and_place(rook_from, rook_to)) # Move Rook
            final_gcode.append(f"G0 X0 Y0 F{self.F_RAPID} ; Return to origin")
            return "\n".join(final_gcode)

        # 2. EN PASSANT (Ngrënia Kalimthi)
        if move_type == "en_passant":
            final_gcode.append("; --- EN PASSANT CAPTURE ---")
            # The captured pawn is on the destination's column, but origin's row
            captured_sq = move_to[0] + move_from[1]
            final_gcode.extend(self.remove_to_graveyard(captured_sq))
            final_gcode.append(f"; --- PLAYER MOVE: {move_from} -> {move_to} ---")
            final_gcode.extend(self.generate_pick_and_place(move_from, move_to))
            final_gcode.append(f"G0 X0 Y0 F{self.F_RAPID} ; Return to origin")
            return "\n".join(final_gcode)

        # 3. PAWN PROMOTION (Promovimi i Ushtarit)
        if move_type == "promotion":
            final_gcode.append("; --- PAWN PROMOTION ---")
            if is_capture:
                final_gcode.append("; First, remove opponent piece at target")
                final_gcode.extend(self.remove_to_graveyard(move_to))

            final_gcode.append("; Remove promoting pawn to graveyard")
            final_gcode.extend(self.remove_to_graveyard(move_from))

            final_gcode.append("; Place Queen from reserve onto the board")
            tx, ty = self.square_to_coords(move_to)
            final_gcode.append(f"G0 X{self.QUEEN_RESERVE_X} Y{self.QUEEN_RESERVE_Y} F{self.F_RAPID} ; Go to Queen reserve")
            final_gcode.append(f"G0 Z{self.Z_GRAB} ; Lower claw")
            final_gcode.append(f"{self.CLAW_CLOSE} ; Grab piece")
            final_gcode.append("G4 P500 ; Dwell for 0.5s")
            final_gcode.append(f"G0 Z{self.Z_TRAVEL} ; Raise claw")
            final_gcode.append(f"G0 X{tx} Y{ty} F{self.F_RAPID} ; Place Queen on {move_to}")
            final_gcode.append(f"G0 Z{self.Z_GRAB} ; Lower claw")
            final_gcode.append(f"{self.CLAW_OPEN} ; Release piece")
            final_gcode.append("G4 P500 ; Dwell for 0.5s")
            final_gcode.append(f"G0 Z{self.Z_TRAVEL} ; Raise claw")
            final_gcode.append(f"G0 X0 Y0 F{self.F_RAPID} ; Return to origin")
            return "\n".join(final_gcode)

        # 4. STANDARD CAPTURE (Ngrënie Normale)
        if is_capture:
            final_gcode.append(f"; --- CAPTURE OPPONENT PIECE AT {move_to} ---")
            final_gcode.extend(self.remove_to_graveyard(move_to))

        # 5. STANDARD MOVE (Lëvizje Normale)
        final_gcode.append(f"; --- PLAYER MOVE: {move_from} -> {move_to} ---")
        final_gcode.extend(self.generate_pick_and_place(move_from, move_to))

        # Clear the view for the human player
        final_gcode.append(f"G0 X0 Y0 F{self.F_RAPID} ; Return to origin")
        return "\n".join(final_gcode)
