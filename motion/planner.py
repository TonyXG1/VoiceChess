"""Role 3 - motion planning: chess square -> physical XY mm -> G-code.

STUB. Role 3's real implementation (square->mm mapping, Z/claw sequencing,
capture-to-graveyard routing, promotion reserve handling) replaces this file.
Only the plan() signature is the contract; the orchestrator depends on nothing
else here.
"""

from typing import List, Optional


class MotionPlanner:
    def plan(self, from_sq: str, to_sq: str, capture: bool = False,
             special: Optional[str] = None) -> List[str]:
        """Plan the physical piece movement for one chess move.

        Args:
            from_sq: origin square, e.g. "e2".
            to_sq: destination square, e.g. "e4".
            capture: True if a piece on to_sq must first go to the graveyard.
            special: None | "castle" | "promotion" | "en_passant".

        Returns:
            G-code lines ready to stream to the ESP32 (FluidNC) over serial.
        """
        # TODO: replace with Role 3's real square->mm + G-code generation.
        return [f"; STUB gcode for {from_sq}->{to_sq} capture={capture} special={special}"]
