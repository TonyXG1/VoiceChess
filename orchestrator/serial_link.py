"""Role 4 boundary - USB-serial link to the ESP32 running FluidNC.

Streams G-code to the ESP32 line by line. Two modes, chosen at construction:

  * No port (the default: ``SerialLink()``) -> DRY-RUN. Each line is printed as
    ``[SERIAL-STUB] would send: ...`` and nothing is opened. Tests, CI, and
    ``python main.py --text`` need no hardware and behave exactly as before.
  * A port (``SerialLink(port="/dev/ttyUSB0")``, wired from ``main.py --serial``)
    -> REAL pyserial link at FluidNC's baud. Each line is written and the reply
    is read back.

The FluidNC handshake (an ``ok`` per line, then ``?`` -> ``<Idle>`` polling before
returning) is implemented but deliberately TOLERANT: every read has a timeout and
a miss is logged, never fatal. That lets the whole pipeline emit real serial bytes
*before* the ESP32 is even flashed with FluidNC -- you see exactly what goes on the
wire. Tighten the handshake (make a missing ``ok`` an error, block harder on
``<Idle>``) once FluidNC answers reliably.
"""

from __future__ import annotations

import time
from typing import List, Optional

try:  # pyserial is only needed for a REAL port; dry-run mode must not require it.
    import serial  # type: ignore
except Exception:  # pragma: no cover - optional dep on dev boxes
    serial = None


class SerialLink:
    def __init__(self, port: Optional[str] = None, baud: int = 115200,
                 ack_timeout: float = 2.0, idle_timeout: float = 30.0) -> None:
        self.port = port
        self.baud = baud
        self.ack_timeout = ack_timeout
        self.idle_timeout = idle_timeout
        self._ser = None

        if not port:
            return  # dry-run mode: send() just prints.

        if serial is None:
            raise RuntimeError(
                "pyserial is not installed but a serial port was requested "
                f"({port!r}). Run `pip install pyserial`, or drop --serial to "
                "use dry-run (print-only) mode."
            )
        # Opening the port toggles DTR/RTS, which resets most ESP32 dev boards;
        # give FluidNC a moment to boot, then swallow its startup banner.
        self._ser = serial.Serial(port, baud, timeout=ack_timeout)
        time.sleep(2.0)
        self._drain_banner()

    # ------------------------------ public API ------------------------------ #

    def send(self, gcode_lines: List[str]) -> None:
        """Stream G-code to the ESP32 and block until motion completes.

        The orchestrator calls this synchronously on purpose: the game must not
        listen for the next move while the gantry is still moving.
        """
        if self._ser is None:
            for line in gcode_lines:
                print(f"[SERIAL-STUB] would send: {line}")
            return

        for line in gcode_lines:
            self._send_line(line)
        self._wait_for_idle()

    def close(self) -> None:
        """Close the port. No-op in dry-run mode; safe to call more than once."""
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    # ------------------------------ internals ------------------------------- #

    def _drain_banner(self) -> None:
        """Print (and discard) FluidNC's post-reset startup lines."""
        deadline = time.time() + 2.0
        while time.time() < deadline:
            raw = self._ser.readline()
            if not raw:
                break
            print(f"[SERIAL] ~ {raw.decode(errors='replace').strip()}")

    def _send_line(self, line: str) -> None:
        """Write one G-code line and wait (with timeout) for its ok/error ack."""
        payload = line.strip()
        if not payload:
            return  # blank lines carry no motion; skip the round-trip
        self._ser.write((payload + "\n").encode("ascii", errors="replace"))
        self._ser.flush()
        reply = self._read_reply()
        print(f"[SERIAL] > {payload}")
        print(f"[SERIAL] < {reply if reply else '(no reply within timeout)'}")

    def _read_reply(self) -> Optional[str]:
        """Read lines until an ok/error ack or the ack timeout elapses.

        Non-ack chatter (status pushes, comments echoed back) is logged and we
        keep waiting -- a real ack is what ends the wait.
        """
        deadline = time.time() + self.ack_timeout
        while time.time() < deadline:
            raw = self._ser.readline()  # bounded by the port's own timeout
            if not raw:
                continue
            text = raw.decode(errors="replace").strip()
            if not text:
                continue
            if text.lower().startswith(("ok", "error")):
                return text
            print(f"[SERIAL] . {text}")  # push/status line - informational
        return None

    def _wait_for_idle(self) -> None:
        """Poll ``?`` until FluidNC reports ``<Idle>`` or the idle timeout hits.

        Tolerant by design: a timeout logs and returns rather than hanging the
        game, so the pipeline stays runnable before FluidNC is on the ESP32.
        """
        deadline = time.time() + self.idle_timeout
        while time.time() < deadline:
            self._ser.write(b"?")  # FluidNC realtime status query (no newline)
            self._ser.flush()
            raw = self._ser.readline()
            text = raw.decode(errors="replace").strip() if raw else ""
            if text:
                print(f"[SERIAL] ? {text}")
                if "Idle" in text:
                    return
            time.sleep(0.1)
        print("[SERIAL] (idle poll timed out - continuing; "
              "tighten the handshake once FluidNC responds)")
