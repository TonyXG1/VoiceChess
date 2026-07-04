"""Role 4 boundary - USB-serial link to the ESP32 running FluidNC.

STUB. The ESP32/FluidNC side (Role 4) isn't in this repo yet. When it lands,
this becomes a pyserial connection that streams G-code line by line, waits for
"ok" per line, and polls "?" until "<Idle>" before reporting the move done.
`pyserial` is already in requirements.txt for that moment.
"""

from typing import List


class SerialLink:
    def send(self, gcode_lines: List[str]) -> None:
        """Stream G-code to the ESP32 and block until motion completes.

        The orchestrator calls this synchronously on purpose: the game must
        not listen for the next move while the gantry is still moving.
        """
        # TODO: replace with real pyserial connection + ok/<Idle> polling to ESP32.
        for line in gcode_lines:
            print(f"[SERIAL-STUB] would send: {line}")
