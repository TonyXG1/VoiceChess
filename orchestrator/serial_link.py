"""Role 4 boundary - USB-serial link to the ESP32 running FluidNC.

Streams G-code to the ESP32 line by line. Two modes, chosen at construction:

  * No port (the default: ``SerialLink()``) -> DRY-RUN. Each line is printed as
    ``[SERIAL-STUB] would send: ...`` and nothing is opened. Tests, CI, and
    ``python main.py --text`` need no hardware and behave exactly as before.
  * A port (``SerialLink(port="/dev/ttyUSB0")``, wired from ``main.py --serial``)
    -> REAL pyserial link at FluidNC's baud. Each line is written and the reply
    is read back.

Dry-run stays deliberately TOLERANT so the whole pipeline can emit real serial
bytes before the ESP32 is flashed. A REAL port is now STRICT: an ``error:``
reply raises instead of streaming the rest of the move into a machine that has
already rejected a line, and an ``Alarm`` state is reported rather than
silently swallowed. Those two together were the difference between "the gantry
did nothing and the log looked fine" and an actionable failure.

FluidNC protocol notes that shape this file:

  * After power-on with homing enabled the controller sits in **Alarm** and
    answers every G-code line with ``error:9``. ``$H`` must run first. That is
    a ``$`` command, not G-code, so it lives here and not in the planner.
  * ``ok`` means "queued", not "executed" -- FluidNC has a planner buffer.
    Blocking until motion really finishes is what ``?`` -> ``<Idle>`` is for,
    which ``send()`` does once at the end.
  * Because FluidNC withholds ``ok`` when that buffer is full, send-one-
    wait-for-``ok`` is already flow-controlled. No character counting needed.
"""

from __future__ import annotations

import time
from typing import List, Optional

try:  # pyserial is only needed for a REAL port; dry-run mode must not require it.
    import serial  # type: ignore
except Exception:  # pragma: no cover - optional dep on dev boxes
    serial = None


class SerialError(RuntimeError):
    """FluidNC rejected a line, or the machine is in a state that can't move."""


# The GRBL/FluidNC error codes worth translating; the rest are passed through.
_ERROR_HINTS = {
    "9": "G-code locked out during alarm or jog state - the machine needs $H "
         "(homing) before it will accept motion.",
    "2": "Bad number format in the G-code line.",
    "3": "Unsupported '$' system command.",
    "8": "'$' command unavailable while running.",
    "15": "Jog target exceeds machine travel.",
    "20": "Unsupported or invalid G-code command.",
    "22": "Feed rate has not been set (missing F word).",
    "33": "Motion target is invalid.",
}


class SerialLink:
    def __init__(self, port: Optional[str] = None, baud: int = 115200,
                 ack_timeout: float = 2.0, idle_timeout: float = 180.0,
                 home_timeout: float = 90.0, strict: Optional[bool] = None) -> None:
        self.port = port
        self.baud = baud
        # Strict by default whenever a real port is open. serial_test.py passes
        # strict=False on purpose: its whole job is to prove that bytes reach
        # the ESP32 *before* FluidNC is flashed, when nothing will ever answer.
        self.strict = bool(port) if strict is None else strict
        self.ack_timeout = ack_timeout
        # A capture is ~1.5 m of travel plus dwells. The old 30 s expired
        # mid-move, and because the timeout is non-fatal the orchestrator went
        # back to LISTEN while the gantry was still moving.
        self.idle_timeout = idle_timeout
        self.home_timeout = home_timeout
        self._ser = None
        self._homed = False
        self._warned_unhomed = False

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
        self._report_initial_state()

    # ------------------------------ public API ------------------------------ #

    @property
    def is_live(self) -> bool:
        """True when a real port is open (i.e. strict mode)."""
        return self._ser is not None

    def home(self) -> None:
        """Run FluidNC's homing cycle and block until it finishes.

        Must happen before any move: until the machine has homed it has no idea
        where a1 is, and it refuses G-code with ``error:9`` anyway.
        """
        if self._ser is None:
            print("[SERIAL-STUB] would home: $H")
            self._homed = True
            return

        print("[SERIAL] homing ($H) - this can take up to a minute...")
        self._ser.write(b"$H\n")
        self._ser.flush()
        # $H does not ack until the cycle completes, so it gets its own timeout.
        reply = self._read_reply(timeout=self.home_timeout)
        if reply is None:
            self._fail(
                f"No reply to $H within {self.home_timeout:.0f}s. Check that "
                "FluidNC is flashed, that homing is enabled in config.yaml, "
                "and that the limit switches are wired."
            )
        else:
            self._check_error(reply, "$H")
        self._wait_for_idle()
        self._homed = True
        print("[SERIAL] homed.")

    def send(self, gcode_lines: List[str]) -> None:
        """Stream G-code to the ESP32 and block until motion completes.

        The orchestrator calls this synchronously on purpose: the game must not
        listen for the next move while the gantry is still moving.
        """
        if self._ser is None:
            for line in gcode_lines:
                print(f"[SERIAL-STUB] would send: {line}")
            return

        if not self._homed and not self._warned_unhomed:
            print("[SERIAL] WARNING: streaming without homing first. Machine "
                  "coordinates are meaningless until $H has run.")
            self._warned_unhomed = True

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

    def _report_initial_state(self) -> None:
        """Say plainly whether the machine will accept motion yet."""
        state = self._query_state()
        if state is None:
            print("[SERIAL] no status reply to '?' - is FluidNC flashed and "
                  "at the right baud?")
        elif state.startswith("Alarm"):
            print("[SERIAL] machine is in ALARM. It will reject every G-code "
                  "line with error:9 until $H runs.")
        else:
            print(f"[SERIAL] machine state: {state}")

    def _query_state(self) -> Optional[str]:
        """One ``?`` round-trip -> the state word out of ``<Idle|MPos:...>``."""
        self._ser.write(b"?")  # FluidNC realtime status query (no newline)
        self._ser.flush()
        raw = self._ser.readline()
        text = raw.decode(errors="replace").strip() if raw else ""
        if not text.startswith("<"):
            return None
        return text[1:].split("|", 1)[0].rstrip(">").strip()

    @staticmethod
    def _strip_comment(line: str) -> str:
        """Drop the ``; ...`` tail. Comments are for the local trace, not the wire."""
        return line.split(";", 1)[0].strip()

    def _fail(self, message: str) -> None:
        """Raise in strict mode; log and carry on in tolerant mode."""
        if self.strict:
            raise SerialError(message)
        print(f"[SERIAL] (tolerant) {message}")

    def _check_error(self, reply: str, source: str) -> None:
        if not reply.lower().startswith("error"):
            return
        code = reply.split(":", 1)[1].strip() if ":" in reply else ""
        hint = _ERROR_HINTS.get(code, "")
        self._fail(
            f"FluidNC rejected {source!r} with {reply}."
            + (f" {hint}" if hint else "")
        )

    def _send_line(self, line: str) -> None:
        """Write one G-code line and wait for its ok/error ack.

        Comments never reach the wire: a comment-only line would still cost a
        full round-trip, and a 35-line promotion is about half comments.
        """
        payload = self._strip_comment(line)
        if not payload:
            if line.strip():
                print(f"[SERIAL] # {line.strip()}")  # local trace only
            return
        self._ser.write((payload + "\n").encode("ascii", errors="replace"))
        self._ser.flush()
        reply = self._read_reply()
        print(f"[SERIAL] > {payload}")
        print(f"[SERIAL] < {reply if reply else '(no reply within timeout)'}")
        if reply is None:
            self._fail(
                f"No ok/error ack for {payload!r} within {self.ack_timeout}s. "
                "The controller may be wedged or the baud rate may be wrong."
            )
        else:
            self._check_error(reply, payload)

    def _read_reply(self, timeout: Optional[float] = None) -> Optional[str]:
        """Read lines until an ok/error ack or the timeout elapses.

        Non-ack chatter (status pushes, messages) is logged and we keep waiting
        -- a real ack is what ends the wait.
        """
        deadline = time.time() + (self.ack_timeout if timeout is None else timeout)
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
        """Poll ``?`` until FluidNC reports ``Idle``.

        This is what makes ``send()`` block until the gantry has actually
        stopped -- ``ok`` only means the line was queued. An ``Alarm`` here
        means a limit switch tripped mid-move, which must not be waited out.
        """
        deadline = time.time() + self.idle_timeout
        # In tolerant mode nothing may be answering at all (FluidNC not flashed
        # yet). Don't sit through the full idle_timeout to discover that.
        quiet_deadline = time.time() + 3.0
        saw_status = False

        while time.time() < deadline:
            state = self._query_state()
            if not state and not saw_status and not self.strict \
                    and time.time() > quiet_deadline:
                print("[SERIAL] nothing is answering '?' - skipping the idle "
                      "wait (FluidNC probably isn't flashed yet).")
                return
            if state:
                saw_status = True
                print(f"[SERIAL] ? {state}")
                if state.startswith("Alarm"):
                    self._fail(
                        "Machine entered ALARM mid-move (usually a limit "
                        "switch or a soft-limit violation). Motion stopped."
                    )
                    return
                if state.startswith("Idle"):
                    return
            time.sleep(0.1)
        self._fail(
            f"Gantry still moving after {self.idle_timeout:.0f}s. Refusing to "
            "hand control back to the game loop while it may still be in "
            "motion - raise idle_timeout if a move legitimately takes longer."
        )
