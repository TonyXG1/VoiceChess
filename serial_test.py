#!/usr/bin/env python3
"""Standalone ESP32 serial-link check -- the serial analog of speak_test.py.

Opens the given port, sends a few harmless G-code lines, and prints whatever
the ESP32 (FluidNC) replies. Run this to prove the USB link in isolation before
running a whole game through main.py --serial.

    python serial_test.py /dev/ttyUSB0            # Raspberry Pi
    python serial_test.py COM5 --baud 115200      # Windows dev box

Needs pyserial (`pip install pyserial`, already in requirements.txt) and the
ESP32 plugged in over USB. Until FluidNC is flashed, nothing will answer -- the
lines still go out on the wire and each read simply times out (logged, not
fatal), which is enough to confirm the port opens and bytes flow.
"""

import argparse
import sys

from orchestrator import SerialLink

# Set the modal state, then a safe rapid to origin. No claw, no Z plunge --
# nothing that could slam hardware if motors happen to be live. Comments are
# stripped by the link and never reach the wire.
SAMPLE_GCODE = [
    "; VoiceChess serial-link test",
    "G21 ; millimetres",
    "G90 ; absolute positioning",
    "G0 X0.00 Y0.00 ; rapid to origin",
]


def main() -> None:
    p = argparse.ArgumentParser(description="Send test G-code to the ESP32 over USB serial.")
    p.add_argument("port", help="serial device (e.g. /dev/ttyUSB0, COM5)")
    p.add_argument("--baud", type=int, default=115200, help="baud rate (FluidNC default 115200)")
    args = p.parse_args()

    try:
        # strict=False: this script's whole point is to work BEFORE FluidNC is
        # flashed, when no line will ever be acked. main.py stays strict.
        link = SerialLink(port=args.port, baud=args.baud, strict=False)
    except Exception as e:
        sys.exit(f"ERROR: could not open {args.port} @ {args.baud}: "
                 f"{e.__class__.__name__}: {e}")

    print(f"Opened {args.port} @ {args.baud}. Streaming {len(SAMPLE_GCODE)} lines...\n")
    link.send(SAMPLE_GCODE)
    link.close()
    print("\nDone. How to read the output above:")
    print("  '< ok' on every line + '? Idle'   -> FluidNC is flashed and healthy.")
    print("  '< error:9' on every line          -> FluidNC is there but in Alarm;")
    print("                                        it needs $H (or $X to unlock).")
    print("  '(no reply within timeout)'        -> the port opens and bytes go out,")
    print("                                        but nothing is answering: FluidNC")
    print("                                        isn't flashed, or the baud is wrong.")


if __name__ == "__main__":
    main()
