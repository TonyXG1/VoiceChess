"""Tests for the ESP32 serial link's streaming and failure behaviour.

The interesting behaviour is what happens when FluidNC says something other
than "ok". Before this suite, an ``error:`` reply was logged and the rest of
the move was streamed into a controller that had already rejected a line --
the gantry did nothing and the log looked fine.

A fake port stands in for pyserial so none of this needs hardware.
"""

import pytest

from orchestrator import SerialLink, SerialError


class FakeSerial:
    """Minimal pyserial stand-in: replies from a scripted queue."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)
        return len(data)

    def flush(self):
        pass

    def readline(self):
        if not self.replies:
            return b""
        return (self.replies.pop(0) + "\n").encode()

    def close(self):
        self.closed = True


def _link(replies, strict=True):
    """A link in 'real port' mode without opening one."""
    link = SerialLink()          # dry-run construction: opens nothing
    link._ser = FakeSerial(replies)
    link.strict = strict
    link.ack_timeout = 0.2       # keep the no-reply tests fast
    link.idle_timeout = 0.5
    link._homed = True
    return link


def _sent(link):
    return [b.decode().strip() for b in link._ser.written if b != b"?"]


# ------------------------------- dry run ---------------------------------- #

def test_dry_run_prints_and_opens_nothing(capsys):
    link = SerialLink()
    link.send(["G0 X1.00", "; a comment"])
    out = capsys.readouterr().out
    assert "[SERIAL-STUB] would send: G0 X1.00" in out
    assert not link.is_live

def test_dry_run_is_tolerant_by_default():
    assert SerialLink().strict is False


# ---------------------------- comment stripping ---------------------------- #

def test_comments_never_reach_the_wire(capsys):
    link = _link(["ok", "ok", "<Idle|MPos:0,0,0>"])
    link.send(["; --- CASTLING ---", "G0 Z-22.00 ; raise to safe height",
               "G0 X0.00 Y0.00 ; park"])
    assert _sent(link) == ["G0 Z-22.00", "G0 X0.00 Y0.00"]
    # ...but they still show up in the local trace, which is the demo's readout.
    assert "--- CASTLING ---" in capsys.readouterr().out


# ------------------------------ error handling ----------------------------- #

def test_error_reply_stops_the_stream():
    link = _link(["error:9"])
    with pytest.raises(SerialError) as e:
        link.send(["G0 X1.00", "G0 X2.00"])
    # error:9 is the one that actually happens: G-code locked out until $H.
    assert "homing" in str(e.value).lower()
    assert _sent(link) == ["G0 X1.00"]     # the second line was never sent

def test_missing_ack_is_an_error_when_strict():
    link = _link([])                        # nothing ever answers
    with pytest.raises(SerialError):
        link.send(["G0 X1.00"])

def test_dwell_uses_motion_timeout_for_delayed_ack(monkeypatch):
    """G4 waits for queued motion before FluidNC sends its acknowledgement."""
    link = _link([])
    observed = []

    def reply(timeout=None):
        observed.append(timeout)
        return "ok"

    monkeypatch.setattr(link, "_read_reply", reply)
    link._send_line("G4 P0.5")
    assert observed == [link.idle_timeout]

def test_normal_command_keeps_short_ack_timeout(monkeypatch):
    link = _link([])
    observed = []

    def reply(timeout=None):
        observed.append(timeout)
        return "ok"

    monkeypatch.setattr(link, "_read_reply", reply)
    link._send_line("G0 Z85")
    assert observed == [link.ack_timeout]

def test_tolerant_mode_keeps_streaming_through_errors(capsys):
    """serial_test.py relies on this: prove bytes flow before FluidNC exists."""
    link = _link([], strict=False)
    link.send(["G0 X1.00", "G0 X2.00"])
    assert _sent(link) == ["G0 X1.00", "G0 X2.00"]
    assert "tolerant" in capsys.readouterr().out

def test_alarm_mid_move_is_not_waited_out():
    link = _link(["ok", "<Alarm|MPos:0,0,0>"])
    with pytest.raises(SerialError) as e:
        link.send(["G0 X1.00"])
    assert "ALARM" in str(e.value)


# --------------------------------- homing ---------------------------------- #

def test_home_sends_dollar_h_and_waits_for_idle():
    link = _link(["ok", "<Idle|MPos:0,0,0>"])
    link._homed = False
    link.home()
    assert _sent(link) == ["$H"]
    assert link._homed

def test_home_in_dry_run_needs_no_port():
    link = SerialLink()
    link.home()                             # must not raise
    assert link._homed

def test_streaming_unhomed_warns_once(capsys):
    link = _link(["ok", "<Idle|MPos:0,0,0>", "ok", "<Idle|MPos:0,0,0>"])
    link._homed = False
    link.send(["G0 X1.00"])
    link.send(["G0 X2.00"])
    assert capsys.readouterr().out.count("WARNING: streaming without homing") == 1


# --------------------------- idle / status parsing -------------------------- #

def test_send_blocks_until_idle():
    """'ok' only means queued -- '?' -> Idle is what proves motion finished."""
    link = _link(["ok", "<Run|MPos:1,2,3>", "<Run|MPos:2,3,4>",
                  "<Idle|MPos:3,4,5>"])
    link.send(["G1 X1.00 F1200"])           # returns only once Idle is seen
    assert b"?" in link._ser.written
