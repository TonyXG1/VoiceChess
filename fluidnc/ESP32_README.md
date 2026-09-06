# ESP32 / FluidNC — Setup, Debug and Handover

**Status as of 2026-08-10:** the full software path is verified end to end.
The Pi sends G-code over USB serial, FluidNC parses it, and the motion
planner's coordinates arrive intact. Everything remaining is hardware.

Last verified command sequence:

```
$X                  -> ok
G91
G0 X10              -> ok
?                   -> <Idle|MPos:10.000,0.000,187.002,0.000|FS:0,0>
G90
```

X advanced by exactly 10.000 mm as commanded. Nothing is wired to the
drivers yet, so no motor moved — this is the controller's internal
position counter, which is exactly what we wanted to prove.

---

## 1. The architecture in one line

```
Pi (Python)  ->  USB serial /dev/ttyUSB0 @115200  ->  ESP32 (FluidNC)  ->  4x TB6600  ->  motors
```

The Pi owns everything intelligent: speech recognition, chess rules,
Stockfish, motion planning. The ESP32 owns only real-time step pulse
generation. The interface between them is plain G-code text, one line at a
time, with an `ok` acknowledgement per line.

---

## 2. What is on the ESP32 right now

| Item | Value |
|---|---|
| Chip | ESP32-D0WD-V3 rev 3.1, 40 MHz crystal |
| MAC | 8c:94:df:90:d2:90 |
| Firmware | FluidNC v4.0.3, `esp32-wifi` build |
| Console | 115200 8N1 on `/dev/ttyUSB0` (CP2102) |
| Filesystem | LittleFS, holds `config.yaml` |
| WiFi | AP mode, SSID `FluidNC`, password `12345678`, `http://192.168.0.1` |
| Axes | 4 — X (2 ganged motors), Y, Z, A (claw) |

---

## 3. The new folder: `~/fluidnc-v4.0.3/fluidnc-v4.0.3-posix`

This is the official FluidNC v4.0.3 release bundle, unzipped on the Pi. It
is **not** part of the git repo — it is a downloaded toolkit. It contains
the firmware binaries and the flashing/terminal scripts.

```
cd ~/fluidnc-v4.0.3/fluidnc-v4.0.3-posix
ls
```

```
bt/                 common/             wifi/               wifi_s3/
install-wifi.sh     install-fs.sh       install-bt.sh       erase.sh
fluidterm.sh        tools.sh            checksecurity.sh    HOWTO-INSTALL.txt
```

What matters:

- **`fluidterm.sh`** — the serial terminal. This is your daily driver.
- **`wifi/`** — the firmware binaries for our build (`bootloader.bin`,
  `firmware.bin`, `partitions.bin`, `littlefs.bin`).
- **`install-wifi.sh` / `install-fs.sh`** — flashing scripts. **They do not
  work as-is on this Pi** (see section 8). Flash manually instead.

---

## 4. Daily commands — the ones you actually need

### Power-on order

1. Keep the main system PSU off, including the motor drivers and servo buck
   converter. Connect all wiring and common grounds before applying power.
   Place the gripper directly above the CENTRE of a1, with Z fully up:
   this is X0 Y0 Z0, with the gripper 115 mm above the board. Keep it there
   through ESP32 boot/reset and serial connection.
2. **Power the ESP32 first via USB** and wait for FluidNC to finish booting.
3. **Then power the rest of the system**: the main PSU, motor drivers, and
   servo supply (buck converter set to 4.9 V).

The servo can move to its commanded position as soon as it receives power.
This order does not prevent a jam if that position exceeds the gripper's
mechanical travel; keep the linkage detached until its usable range is calibrated.
Do not also feed the USB-powered ESP32 from the buck converter.

### Open a terminal to the ESP32

```bash
cd ~/fluidnc-v4.0.3/fluidnc-v4.0.3-posix
./fluidterm.sh
```

Choose port `2` (`/dev/ttyUSB0`).

| Key | Action |
|---|---|
| `Ctrl-]` or `Ctrl-Q` | quit |
| `Ctrl-U` | upload a file to the ESP32 |
| `Ctrl-R` | reset the board |
| `Ctrl-W` | clear screen |

Alternative terminal: `screen /dev/ttyUSB0 115200`, exit with `Ctrl-A k y`.

### Free the serial port

**Only one process can hold `/dev/ttyUSB0` at a time.** This is the single
most common source of confusion. If a script says the port is busy, or
fluidterm shows nothing, run:

```bash
fuser -v /dev/ttyUSB0     # who has it
fuser -k /dev/ttyUSB0     # kill them
screen -ls                # check for stale screens
```

Do **not** run two terminals on the port, and do **not** use
`cat /dev/ttyUSB0` — two readers split the byte stream and each gets half.

### Essential FluidNC commands

```
$X                      clear Alarm state (needed after every boot)
?                       status report (send as a single char, no Enter)
$$                      list all settings
$CD                     dump the ACTIVE config as YAML
$I                      firmware version
$Help                   list available commands for this build
$LocalFS/List           list files on the ESP32
$LocalFS/Run=test_capture_positions.gcode
                        visit all 16 temporary capture positions
$LocalFS/Run=test_queen_reserve.gcode
                        visit both promotion queen pickup positions
$LocalFS/Run=test_all_squares.gcode
                        visit and lower over all 64 board squares
$LocalFS/Delete=config.yaml    delete the config (recovery, see section 7)
$Bye                    reboot
$C                      toggle check mode (parse G-code, move nothing)
$H                      run homing cycle
$J=G91 X10 F500         jog 10mm relative — cancellable, safe
$Macros/Run=0           run the configured movement-test macro
```

### Test every temporary capture position

The job [test_capture_positions.gcode](test_capture_positions.gcode) visits all
16 positions in the L-shaped capture area: eight along Y485 parallel to the
h-file and eight along X485 beyond rank 8, spaced 62 mm apart. It keeps the
claw open, retracts to Z0 before every XY move, lowers to Z40, and waits four
seconds at each position with `G4 P4`. Every X/Y/Z move uses `G1 F1500`; `G0`
would use FluidNC's configured rapid rate and ignore the requested feed.

Before running it, clear the board and capture area, place Z fully up over the
centre of a1, and establish the G54 origin as documented below. Upload the file
with FluidTerm `Ctrl-U`, confirm it appears with `$LocalFS/List`, then run:

```
$X
$LocalFS/Run=test_capture_positions.gcode
```

Use feed hold (`!`) to pause or `Ctrl-X` to stop if any position looks wrong.
In the FluidNC WebUI, click **Pause / Feed Hold** first, then **Stop / Reset**
beside the job progress bar. Reset cancels the queued file and loses the
switchless machine's assumed position, so manually restore a1/Z0 and set G54
again before another test.

The companion [test_queen_reserve.gcode](test_queen_reserve.gcode) visits White
at X403 Y540 and Black at X530 Y540, lowers to the queen pickup Z67, and
waits four seconds at each. Its X/Y/Z moves also use `G1 F1500`. Run it first
with both reserve positions empty;
then place the queens and confirm the open claw is centered before testing A35.

```
$X
$LocalFS/Run=test_queen_reserve.gcode
```

### Test all 64 board squares

[test_all_squares.gcode](test_all_squares.gcode) follows a snake path starting
at a1 and visits all 64 square centres. At every square it uses `G1 F1500`,
lowers from Z0 to Z60, and immediately returns to Z0 before the next XY move.
It finishes at the a1 origin.

Run this test only with the entire board empty: Z60 leaves the gripper's lowest
point 55 mm above the playing surface and therefore does not clear the taller
pieces. After establishing the G54 origin and uploading the file, run:

```
$X
$LocalFS/Run=test_all_squares.gcode
```

The test takes roughly 8 minutes at F1500.

Realtime bytes — sent as raw characters, no Enter, no `ok` reply:

| Byte | Effect |
|---|---|
| `?` | status report |
| `!` | feed hold (decelerate and pause) |
| `~` | resume |
| `Ctrl-X` (0x18) | soft reset — **this is the software e-stop** |
| `0x85` | cancel jog |

### Run the software

```bash
cd ~/voicechess
source venv/bin/activate

# link test only
python serial_test.py /dev/ttyUSB0

# dry run — validates every planner scenario, moves nothing, needs no board
python tools/gcode_preview.py --all

# full app, text input
python main.py --text --script "e2e4,e7e5" --serial /dev/ttyUSB0 --no-home

# full app, voice + speech
python main.py --tts espeak --voice male --serial /dev/ttyUSB0 --no-home
```

**`--no-home` is mandatory.** No axis is configured for homing (all are
`cycle: 0`), so `$H` has nothing to home and errors out.

---

## 5. The config file

Lives in the repo at `~/voicechess/fluidnc/config.yaml` and is uploaded to
the ESP32's filesystem. Edit the repo copy, then upload.

### THE PARSER RULE — read this before editing

**FluidNC's YAML parser does not strip trailing comments.**

```yaml
pulse_us: 6           # this comment breaks the line
```

is read as the literal value `6           # this comment breaks the line`,
and fails with `Expected an integer value`. Comments must be on their own
line:

```yaml
# TB6600 optocouplers need >=5us
pulse_us: 6
```

This cost us roughly two hours. Before every upload, verify:

```bash
grep -nE ':.*#' ~/voicechess/fluidnc/config.yaml
```

Any output (other than lines that themselves start with `#`) will fail.

### Uploading

```bash
fuser -k /dev/ttyUSB0
cd ~/fluidnc-v4.0.3/fluidnc-v4.0.3-posix
./fluidterm.sh
```

Port `2`, wait for a clean prompt, then `Ctrl-U`:

```
Local file to send: /home/anton/voicechess/fluidnc/config.yaml
File on FluidNC: config.yaml
```

Full absolute path, no quotes, no `~`. The destination name must be exactly
`config.yaml`. Then `$Bye` and read the boot log.

If the XMODEM transfer fails with `expected NAK, CRC, EOT or CAN`, the
input buffer had leftover characters. Press `Ctrl-R`, let the boot output
finish completely, press Enter to confirm a clean `ok`, then retry `Ctrl-U`.

### Verifying it parsed

The boot log is the validation. A good boot shows:

```
[MSG:INFO: FluidNC v4.0.3 ...]
[MSG:INFO: Machine VoiceChess Gantry]
[MSG:INFO: Stepping:RMT Pulse:6us ... Idle Delay:255ms]
[MSG:INFO: Axis count 4]
[MSG:INFO:     stepstick Step:gpio.13 Dir:gpio.14 Disable:NO_PIN]
[MSG:INFO:     rc_servo Pin:gpio.19]
[MSG:INFO: AP started]
```

with **zero `[MSG:ERR:` lines**. The `rc_servo` line is the one to check
after the claw change — a `stepstick` line for the A axis means the old
stepper config is still on the board.

Two failure signatures to recognise:

- `Machine Default (Test Drive no I/O)` and `Axis count 3` — the config was
  rejected entirely and FluidNC fell back to its built-in default. It will
  still ack G-code perfectly while driving nothing.
- `Critical error in main_init` — boot aborted. **WiFi never starts**, so
  the AP disappears and serial is the only way back in.

---

## 6. Pin map

| Function | GPIO | Notes |
|---|---|---|
| X motor0 step | 13 | moved off gpio.12 — see warning below |
| X motor0 dir | 14 | |
| X motor1 step | 16 | second ganged NEMA 23 |
| X motor1 dir | 18 | may need `:low` to counter-rotate |
| Y step | 27 | |
| Y dir | 26 | |
| Z step | 25 | |
| Z dir | 33 | |
| Z limit (top) | 17 | **not wired — set to `NO_PIN`, homing disabled** |
| A (claw) servo signal | 19 | SG90, `rc_servo` — one wire, no driver |

`gpio.23` was the DIR pin of a stepper claw that does not exist. It is free.

### GPIO pins to never use on ESP32

| Pins | Why |
|---|---|
| 0, 2, 5, 12, 15 | strapping pins — sampled at reset, affect boot mode |
| 6–11 | wired to the SPI flash chip |
| 1, 3 | UART0 TX/RX — the USB console |
| 34–39 | input only, no output driver |

**GPIO12 specifically:** it selects flash voltage at reset. Our TB6600s are
wired common-anode (PUL+/DIR+/ENA+ to the 5V rail, ESP32 sinks through the
optocoupler), so a driver on GPIO12 pulls it high at boot and the ESP32
enters a **permanent boot loop** — silent at every baud, unrecoverable over
WiFi, only fixable by physically removing the wire. X step was moved to
gpio.13 for this reason. Do not move it back.

Free and safe if more pins are needed: 4, 21, 22, 32.

---

## 7. Recovery procedures

### The board is in Alarm

```
$X
```

Normal after every boot. Not a fault.

### `$H` errors, or homing fails with `ALARM:9 Homing Fail Approach`

Expected. No axis is configured for homing, so there is nothing for `$H` to
do. Use `--no-home`. (Once a switch is wired but the motor is not, you get
`ALARM:9` instead: the controller commanded the axis toward the switch,
nothing moved, and it searched past the end of travel.)

### The FluidNC WiFi network disappeared

The config crashed `main_init`, which runs **before** WiFi starts. Recover
over serial:

```
$LocalFS/Delete=config.yaml
$Bye
```

The board boots stock with `AP started`. Upload a corrected config through
`http://192.168.0.1`, or over serial with `Ctrl-U`.

**Note:** join the FluidNC AP from a laptop, not the Pi — joining it from
the Pi drops your SSH session.

### Garbage characters on the serial console

Work through in order:

1. Wrong baud — try 115200, then 74880 (ROM bootloader speed).
2. Something else holds the port — `fuser -k /dev/ttyUSB0`.
3. Boot loop — press RST while connected at 74880 and look for
   `flash read err`, which means a strapping pin is held high.
4. Chip health check, baud-independent:
   ```bash
   python -m esptool --port /dev/ttyUSB0 --chip esp32 chip-id
   ```
   If this prints chip type, MAC and crystal frequency, the hardware and
   USB path are fine and the problem is firmware or config.

### Full reflash (last resort)

Only if the firmware itself is corrupt. `config.yaml` lives in the repo, so
nothing is lost.

```bash
cd ~/fluidnc-v4.0.3/fluidnc-v4.0.3-posix
source ~/voicechess/venv/bin/activate

python -m esptool --port /dev/ttyUSB0 erase-flash

python -m esptool --chip esp32 --port /dev/ttyUSB0 --baud 230400 \
  --before default-reset --after hard-reset \
  write-flash -z --flash-mode dio --flash-freq 80m --flash-size detect \
  0x1000  wifi/bootloader.bin \
  0x8000  wifi/partitions.bin \
  0xe000  common/boot_app0.bin \
  0x10000 wifi/firmware.bin

python -m esptool --chip esp32 --port /dev/ttyUSB0 --baud 230400 \
  --before default-reset --after hard-reset \
  write-flash -z --flash-mode dio --flash-freq 80m --flash-size detect \
  0x3d0000 wifi/littlefs.bin
```

Then verify the stock firmware boots readably **before** uploading the
config. A fresh flash correctly complains that `config.yaml` is missing —
what matters is that the complaint is legible English.

---

## 8. Why the install scripts don't work

`install-wifi.sh` and `install-fs.sh` create their own virtualenv at
`~/.fluidnc_venv`, install only `xmodem` and `pyserial` into it, then call
`esptool.py` — which does not exist there, and which was renamed to
`esptool` in esptool v5.x anyway. The scripts fail with
`No module named esptool` **without flashing anything**.

The manual commands in section 7 use the exact offsets the scripts print,
and work. The scripts also end with `deactivate`, which kills your project
virtualenv — reactivate with `source ~/voicechess/venv/bin/activate`.

---

## 9. TOMORROW — hardware tasks

Ordered by what blocks what. Nothing here is software.

### A. Before any power is applied

**Set the TB6600 current DIP switches.** This is the only setting that can
physically destroy a motor. Set at or just **below** the motor's rated
current per phase.

| Motor | Rated | Set to | S4 | S5 | S6 |
|---|---|---|---|---|---|
| NEMA 23 (X ×2, Y) | 2.8 A | 2.5 A | OFF | ON | ON |
| NEMA 17 (Z) | 1.5 A | 1.5 A | ON | ON | OFF |

The claw is a servo and has no driver — there are no DIPs to set for it.

**VERIFY FIRST:** confirm the NEMA 23s are the 2.8 A part and not the 4.2 A
high-torque variant. The TB6600 caps at 3.5 A continuous either way.
Underpowering causes missed steps but no damage; overpowering cooks the
motor.

**Set microstepping to 1/16 on all four drivers** (S1 OFF, S2 OFF, S3 ON) =
3200 pulses/rev. Every `steps_per_mm` in the config assumes this. A
mismatch makes every distance wrong by the ratio of the two.

**Wire TB6600 logic common-anode.** The ESP32 outputs 3.3 V; the TB6600
expects 5 V logic. Tie all PUL+/DIR+/ENA+ to the 5 V rail and let the ESP32
sink the negative terminals. No level shifter needed. Leave ENA unwired —
`disable_pin` is `NO_PIN` in the config.

**Do not double-power the ESP32** from USB and the buck converter at the
same time. For bench testing, USB only.

### B. Safety hardware — currently absent

None of this exists yet and all of it should before a 24 V run:

- Fused, switched IEC inlet
- Blade fuse on the 24 V rail
- Normally-closed mushroom e-stop

Until the physical e-stop exists, `Ctrl-X` (0x18) over serial is the only
stop, and it only works if a terminal is open.

### C. Limit switches — the biggest remaining risk

**No axis is homed.** X and Y have no switches, and Z's (gpio.17) is not
wired yet, so all three are `cycle: 0` and `limit_*_pin: NO_PIN`. `$H` has
nothing to home and will error — **`--no-home` is mandatory**, not merely
recommended.

Without a datum the controller has no idea where square a1 is, nor whether the
claw was placed at its intended fully-up Z0, after any power cycle.

**Z is configured switchless on purpose** so the axis can be bench-tested
before its switch goes in. Two consequences that bite:

- **Park the Z carriage at the top before powering on or resetting.** That
  position is Z0; positive Z lowers the claw. Without this
  physical initialization, every absolute Z target is offset.
- `soft_limits` is `false` on Z because soft limits are measured against a
  homed datum. Nothing on the controller stops the claw being driven into the
  table — the only guards are the Pi-side envelope check in
  `tools/gcode_preview.py` and the planner's per-piece pickup envelope.

The requested clearance at the top is **115 mm (11.5 cm) above the
playing surface**. Physically set and verify this clearance at Z0; editing
the config does not raise the mechanism. The full
mechanical axis travel remains 170 mm; these are different measurements:

| Position | Z coordinate |
|---|---|
| Fully raised / startup / empty travel | 0 mm |
| High carry | 0 mm |
| Low carry | pickup Z minus 15 mm |
| Measured pickups | Z67 to Z90; see table below |
| Board surface | 115 mm |
| Mechanical bottom (not a board-move target) | 170 mm |

High loaded travel uses Z0 and adjacent square-centre waypoints. The planner
never routes a carried piece through a square occupied by a king or queen. The
shallowest pickup is Z67, which clears the tallest unprotected piece, the 65 mm
bishop, by 2 mm. Manual FluidNC bench tests remain available.

**Migrating from the old bottom-zero coordinates:** upload the updated
`fluidnc/config.yaml` and use the matching `motion/config.py` on the Pi.
With Z physically at the top, boot the ESP32 and send:

```gcode
G21
G54
G10 L20 P1 X0 Y0 Z0
```

Run these commands only while fully raised directly above the CENTRE of a1.
They assign work X0 Y0 Z0 to that starting position without moving any axis.
Verify work X/Y/Z all read 0 before moving. The board's outer corner is half
a square behind this origin; do not zero there. On the built gantry, +X follows
the a-file toward a8 and +Y follows rank 1 toward h1. Square centres are a1 =
(0, 0), b1 = (0, 58), a2 = (58, 0), and h8 = (406, 406) mm.
Squares are 58 mm on both axes. The playing area is 464 x 464 mm, with a
20 mm border on all four sides: 504 x 504 mm overall. Relative to a1's
centre, the playing edges run from -29 to 435 mm and the outer edges from
-49 to 455 mm on each axis. The border does not shift the square centres.
Measured usable travel from this origin is **X0 to X535 mm** and
**Y0 to Y545 mm**. All 64 square centres fit. These limits are recorded
in both the Pi motion config and FluidNC YAML; controller soft limits remain
disabled, so manual jogs are not automatically stopped at these boundaries.
The temporary flat capture area has eight positions along Y485 parallel to the
h-file and eight along X485 beyond rank 8, all spaced 62 mm apart. Captured
pieces arrive at high carry Z0, descend to Z40, and drop onto that surface. It
has 16 positions and must be cleared before the planner is reset. One promotion
queen per colour is reserved at X403 Y540 for White and X530 Y540 for Black;
both use the measured queen Z67/A35 pickup. Verify them physically before automatic
promotions.
Use G54 for the game. Opening the Python serial port can reset the ESP32,
so Z must be at the top before launching the app too. After a reset elsewhere,
restore the physical top position and its work Z0 before resuming.
Do not reuse old negative-Z-down move commands with this frame.

Wire the switches, then in `config.yaml`:

```yaml
    soft_limits: true
    homing:
      cycle: 2
    motor0:
      limit_neg_pin: gpio.<pin>:low:pu
```

Z goes back to `cycle: 1` with `limit_neg_pin: gpio.17:low:pu` and
`soft_limits: true` so it retracts first; X and Y share `cycle: 2`.

Then set `must_home: true` under `start:` and drop `--no-home` from the
`main.py` command line.

**Bench workaround until then** — after each power-up, once:

```
$J=G91 X10 F1000        (jog until the claw is centred over a1)
G10 L20 P1 X0 Y0        (declare that spot as work zero)
```

This drifts and is not a demo plan.

### D. Calibrate steps_per_mm

Current values are calculated, not measured. For each axis:

```
$J=G91 X100 F500
```

Measure the actual travel with calipers, then:

```
new_steps_per_mm = old * (commanded / measured)
```

**Z was calibrated on the built machine.** The original `50.930` value assumed
a module-1.0, 20-tooth rack-and-pinion:

```
travel per rev = pi * module * teeth = pi * 1.0 * 20 = 62.832 mm
steps_per_mm   = 3200 / 62.832       = 50.930
```

A commanded 100 units moved the real mechanism 20 mm, so the corrected value is:

```
50.930 * (100 / 20) = 254.650 steps/mm
```

Verify the correction with a 20 mm physical move before approaching the board.

### E. Direction and ganging checks

- Jog each axis a small distance and confirm it moves the expected direction. If
  reversed, toggle `:low` on the `direction_pin` in the config — do **not**
  swap motor wires.
- **X has two ganged NEMA 23s.** Both direction outputs are now inverted:
  `gpio.14:low` and `gpio.18:low`. This reverses +X/-X relative to the previous
  wiring configuration while preserving the relationship between the rails.
  After uploading and rebooting, check `$J=G91 G21 X1 F100` with room to move,
  then `$J=G91 G21 X-1 F100` to return. Confirm both rails move together and
  recalibrate the board's X origin. The planner maps increasing X from rank 1
  toward rank 8 while keeping the file fixed.

### F. Claw axis

Measured internal claw depth is 30 mm. A0 remains open. Pickup is calibrated
around each piece's body rather than at its base:

| Piece | Pickup Z (positive down) | Grip A |
|---|---:|---:|
| Pawn | 85 | 62 |
| Knight | 75 | 76 |
| Bishop | 80 | 43 |
| Rook | 90 | 43 |
| Queen | 67 | 35 |
| King | 67 | 35 |

At high carry Z0, the piece base is approximately its pickup-Z distance above
the board. The planner routes around all king and queen squares, leaving only
pieces up to the 65 mm bishop to cross; the Z67 profiles clear it by 2 mm.

The claw is an **SG90 9g micro-servo**, not a stepper and not on a TB6600.
It takes a single signal wire on **gpio.19**. There is no fourth driver
available for it anyway — X (×2), Y and Z already consume all four TB6600s.

The config declares it as axis `A` with an `rc_servo` motor, so `G0 A<deg>`
queues in move order with XY/Z. FluidNC maps the pulse range linearly across
the axis travel:

| Command | Pulse | Jaw state |
|---|---|---|
| `G0 A0` | 1000 µs | open, 60 mm outer |
| `G0 A35` | about 1389 µs | queen / king grip |
| `G0 A43` | about 1478 µs | bishop / rook grip |
| `G0 A62` | about 1689 µs | pawn grip |
| `G0 A76` | about 1844 µs | knight grip |
| `G0 A90` | 2000 µs | full sweep end |

`A0` and the measured grip angles are recorded in `motion/config.py`. Tune a
single piece profile there if its grip changes.

**The YAML key is `pwm_hz`.** FluidNC's internal field is `_pwm_freq` and the
wiki prints `pwm_freq`, but the parser only accepts `pwm_hz` — verified
against `FluidNC/src/Motors/RcServo.h`. Get it wrong and the whole config is
rejected, booting the stock `Test Drive no I/O` machine that acks every line
while driving nothing.

**Power:** an SG90 stalls near 700 mA. Feed it from the 5 V buck rail, *not*
the ESP32's regulator, and share a common ground. 1000–2000 µs is a safe
subset of the SG90's ~500–2400 µs full sweep.

**gpio.23 is now free** — it was the DIR pin of a stepper claw that does not
exist.

> Earlier revisions of this file and of `config.yaml` claimed the claw was a
> NEMA 17. That was wrong. As a `stepstick`, FluidNC would have fired ~400
> narrow 6 µs STEP pulses at gpio.19 instead of a 50 Hz PWM, and the claw
> would never have gripped.

---

## 10. Recommended bring-up order tomorrow

Each step isolates one thing. Do not skip ahead.

1. Set all DIP switches (current + microstepping) — **before power**.
2. Power the ESP32 first via USB, leaving the main system PSU off. Wait for
   FluidNC to boot, then `$X`, `?` — confirm
   `<Idle|MPos:0.000,0.000,0.000,0.000|...>` with four numbers. For the powered
   hardware tests below, switch on the rest of the system only after the ESP32
   has booted, following the power-on order in section 4.
3. `python tools/gcode_preview.py --all` — every scenario planned and
   envelope-checked on the Pi, nothing moves.
4. Motors **disconnected**: stream a job, poll `?`, watch `MPos` walk to the
   target and return to `Idle`.
5. **Claw first — it needs no motors wired.** `G0 A0`, then test the measured
   piece angles A35, A43, A62, and A76 one at a time with the gripper clear.
6. Reconnect **X only**: `$J=G91 X10 F500`. Check direction, check both
   ganged motors agree.
7. `$J=G91 X100 F500`, measure, correct `steps_per_mm`.
8. Repeat 6–7 for Y, then Z. **Z with the carriage parked at the top and in
   small downward steps** (`$J=G91 G21 Z10 F500`) — there is no switch or soft limit
   to catch an overrun. Verify `steps_per_mm: 254.650` with a measured move.
9. Wire the Z limit switch, then X/Y limit switches; update config, test `$H`.
10. Set work zero over a1, run a full move with a real piece on the board.
