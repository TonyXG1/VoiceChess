# Raspberry Pi 5 setup (Pi OS Lite 64-bit)

Fresh-Pi-to-playing-game runbook. Assumes SSH access works. Run everything as the
default user (not root).

## 1. System packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git python3-venv python3-pip stockfish espeak-ng alsa-utils libportaudio2 unzip
```

What each is for:

| Package | Why |
|---|---|
| `stockfish` | the AI opponent (system binary, found on PATH by main.py) |
| `espeak-ng` | the actual TTS voice on Linux — pyttsx3 is only a wrapper around it |
| `alsa-utils` | `aplay` / `arecord` / `speaker-test` for audio debugging |
| `libportaudio2` | native library that the `sounddevice` pip package needs |

## 2. Copy the repo from the dev PC

The repo isn't on GitHub yet, so copy it over SSH from the Windows PC (PowerShell):

```powershell
scp -r "D:\Microsoft VS Code Projects\CPS\voicechess" pi@<pi-ip>:~/voicechess
```

This includes `voice_matching/model/` (~40MB Vosk model), which saves re-downloading
it on the Pi. If the copy ever excludes it, fetch it directly on the Pi:

```bash
cd ~/voicechess/voice_matching
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && mv vosk-model-small-en-us-0.15 model
rm vosk-model-small-en-us-0.15.zip
```

## 3. Python environment

Pi OS Bookworm blocks system-wide `pip install` (PEP 668), so use a venv:

```bash
cd ~/voicechess
python3 -m venv .venv
source .venv/bin/activate           # do this in every new SSH session before running
pip install -r requirements.txt
pip install pyttsx3                 # optional TTS backend (commented out in requirements.txt)
```

## 4. Audio hardware — discovery & configuration

**The Pi 5 has NO 3.5mm headphone jack** (it was removed from the Pi 4 design).
The speaker must be USB (or a small USB audio dongle + 3.5mm speaker). Both the USB
mic and USB speaker are standard USB Audio Class devices — Pi OS has the driver
built in, so plugging them in is the whole "installation". Verify they showed up:

```bash
aplay -l     # playback devices — the USB speaker should be listed as a card
arecord -l   # capture devices — the USB mic should be listed as a card
```

On Pi OS **Lite** there is no PulseAudio/PipeWire — just bare ALSA — and the default
card is usually the HDMI output (card 0), not the USB speaker. If sound comes out of
the wrong place (or nowhere), pin the defaults by card number from the listings above:

```bash
# example: USB speaker is card 2, USB mic is card 3
sudo tee /etc/asound.conf > /dev/null <<'EOF'
defaults.pcm.card 2
defaults.ctl.card 2
EOF
```

The app itself needs no config change for the mic: `voice_matching/config.py` has
`DEVICE_INDEX = None`, which means "system default input". PortAudio will pick the
USB mic if it's the only capture device (typical). If it ever grabs the wrong one,
list indexes with `python -c "import sounddevice; print(sounddevice.query_devices())"`
and set `DEVICE_INDEX` to the right number.

Smoke-test the chain before involving Python:

```bash
speaker-test -t wav -c 1 -l 1                      # speaker: should say "front center"
arecord -d 3 -f S16_LE -r 16000 test.wav           # talk for 3 seconds...
aplay test.wav                                     # ...and hear yourself back
espeak-ng "pawn to e4"                             # TTS straight through ALSA
```

## 5. Run it (test ladder — same order every fresh setup)

```bash
cd ~/voicechess && source .venv/bin/activate

# 1. logic only, no audio at all:
python main.py --text --script "e2e4,g1f3,f1c4"

# 2. add real speech output, still typed input:
python main.py --text --tts espeak

# 3. the real thing — live mic + spoken feedback:
python main.py --tts espeak
```

If step 3 prints "Live voice unavailable... falling back to text", the Vosk model
folder is missing or the mic wasn't found — recheck section 4.

`--tts espeak` calls the espeak-ng binary directly — the most reliable audio path
on the Pi (no Python audio layer). On the Windows dev box the same flag falls back
to pyttsx3 automatically, so the command line is identical on both machines. With
either backend, every spoken line is also printed as `[SPEAK] ...`, so you can
follow the dialogue over SSH even when you can't hear the speaker.

## 6. ESP32 / G-code output over USB

**Startup order: power the ESP32 first via USB, wait for FluidNC to finish
booting, then power the rest of the system (main PSU, motor drivers, and the
servo's 4.9 V buck supply).** Connect common grounds before power-on; do not
also power the USB-connected ESP32 from the buck. The servo may move when its
supply turns on, so its startup position must be within the gripper's usable
travel. For initial setup, keep the main PSU off through the checks below.
Place Z fully up before boot/reset or opening the Python serial connection.
The top is now Z0, with a requested 115 mm clearance above the playing surface.
Physically set and verify that height; changing the config does not move Z.
Start directly above the CENTRE of a1: this is work X0 Y0, not the outside
corner. After boot at that position, use `G21`, `G54`, then
`G10 L20 P1 X0 Y0 Z0` to set the work origin without moving.
Usable travel from that origin is X0..540 mm and Y0..550 mm, matching
`motion/config.py` and `fluidnc/config.yaml`. All board square centres fit,
but the old capture/promotion storage positions require recalibration:
they extend to X686 and Y590. Soft limits are still disabled on the ESP32.
Confirmed board dimensions: 60 mm squares, 480 x 480 mm playing area, and
a 20 mm border on every side (520 x 520 mm overall). With a1 centred at
X0 Y0, h8 is X420 Y420 and the outer board edges are -50..470 mm.
The claw's internal depth is 30 mm; actual pickup Z and working servo A
positions still need measurement. Internal depth is not the grip offset.

> **Do all of this with the 24V PSU OFF.** The ESP32 runs off USB alone, and with
> no 24V the TB6600s cannot turn a motor no matter what G-code arrives. FluidNC
> still tracks position internally, so every step below is fully verifiable with
> nothing able to move. Only power the 24V rail once section 6.5 passes.

### 6.1 Find the port and get permission to open it

```bash
# One-time: let Python open the port without sudo, then log out and back in.
sudo usermod -aG dialout $USER
groups                              # 'dialout' must appear in the list

# Plug the ESP32 in over micro-USB, then:
ls -l /dev/ttyUSB* /dev/ttyACM*     # usually /dev/ttyUSB0 (CP2102/CH340)
dmesg | tail -20                    # names the driver that grabbed it
python -m serial.tools.list_ports -v
```

Nothing listed? It's the cable or the board. **Many micro-USB cables are
charge-only and carry no data lines** — that is the single most common cause.
Try another cable before anything else.

### 6.2 Find out what firmware is actually on the board

```bash
python -m serial.tools.miniterm /dev/ttyUSB0 115200
```

Press the ESP32's EN/RST button, then type `$I` and Enter. Exit with `Ctrl-]`.

| What you see | What it means |
|---|---|
| `[VER:3.x FluidNC ...]` / a `Grbl 3.x [FluidNC ...]` banner | FluidNC is flashed — skip to 6.4 |
| Readable text from some other sketch | Something else is flashed — reflash (6.3) |
| Garbage, or nothing at all | Blank or non-FluidNC board — flash it (6.3) |

Garbage at 115200 right after reset is normal even on a good board: the ESP32 ROM
bootloader prints at 74880 baud. What matters is whether `$I` gets a reply.

### 6.3 Flash FluidNC (only if 6.2 says it isn't there)

This is Role 4's job per CLAUDE.md, but the commands are here so the software
side isn't blocked on it.

```bash
python -m pip install esptool
```

Download the **`-posix` bundle** for the latest release from
<https://github.com/bdring/FluidNC/releases>, unzip it, and run the WiFi install
script it contains (the exact filename carries the version number):

```bash
cd ~/FluidNC-<version>-posix
./install-wifi.sh                   # calls esptool against /dev/ttyUSB0
```

If it can't find the board, hold the BOOT/IO0 button while it starts erasing.
Re-run 6.2 afterwards — you should now get a FluidNC banner.

### 6.4 Upload our config.yaml

**Yes, you need one.** A freshly flashed FluidNC has no pin map, so it does not
know which GPIOs drive the TB6600s. `fluidnc/config.yaml` in this repo is that
map, generated from the team's pin table so it and `motion/config.py` agree.

```bash
cd ~/voicechess && git pull         # gets fluidnc/config.yaml
```

Two ways to get the file onto the ESP32's own flash:

**Over WiFi (easiest).** A fresh FluidNC raises an access point called `FluidNC`
(password `12345678`). Join it, open <http://192.168.0.1> in a browser, and use
the WebUI's file browser to upload `fluidnc/config.yaml`.

**Over the same USB cable**, using the `fluidterm` script from the release
bundle you unzipped in 6.3:

```bash
./fluidterm.sh                      # connects to /dev/ttyUSB0
# press Ctrl-U, give it the path to fluidnc/config.yaml (XMODEM upload)
```

Then, still connected:

```
$Files/List                         # config.yaml should be listed
$Bye                                # restart so the new config loads
$I                                  # after reboot: no config-error complaints
```

The `$` command set shifts slightly between FluidNC versions — `$Help` lists
what yours actually supports.

### 6.5 Prove the link end to end

```bash
python serial_test.py /dev/ttyUSB0
```

The script prints how to read its own output. In short: `< ok` on every line plus
`? Idle` means the chain works. `< error:9` on every line means FluidNC is alive
but in Alarm and needs `$H` (or `$X` to unlock without homing). `(no reply within
timeout)` means bytes are going out but nothing is listening — go back to 6.2.

To prove FluidNC is really *planning motion* and not just acking, jog it with the
motors still unpowered and watch its position change:

```bash
python -m serial.tools.miniterm /dev/ttyUSB0 115200
```
```
$X                                  # clear Alarm without homing
?                                   # note MPos
G91 G0 X10                          # relative 10mm move
?                                   # MPos X should have advanced by 10.000
G90                                 # back to absolute mode
```

If MPos moves, every layer below the Pi is working and the only thing left is
power and wiring.

### 6.6 Run the game against it

```bash
# Dry-run first — no port, prints the exact G-code the gantry would receive:
python main.py --text --script "e2e4,e7e5,g1f3"

# Check every planned move stays inside the machine envelope:
python tools/gcode_preview.py --all

# Then stream it for real (--no-home is mandatory: no axis is configured to home):
python main.py --text --script "e2e4,e7e5,g1f3" --serial /dev/ttyUSB0 --no-home
```

`pyserial` is already in requirements.txt. Without `--serial`, planned G-code is
printed as `[SERIAL-STUB]` lines instead of sent (the default, and what CI/tests
use). With a real port the link is **strict**: an `error:` reply raises rather
than streaming the rest of a move into a controller that already rejected a line.
`serial_test.py` deliberately stays tolerant so it works pre-flash.

### 6.7 Before the 24V goes on

Three values in the config are still placeholders, and two of them can damage
something:

- **Z `steps_per_mm` in `fluidnc/config.yaml`** assumes a module-1.0, 20-tooth
  pinion. Wrong here and the claw either misses every piece or drives into the
  board. Measure the real pinion first.
- **TB6600 current DIPs** must be set at or below each motor's rated current per
  phase. Above it is the only setting that can physically cook a motor.
- **`BOARD_ORIGIN_X/Y` in `motion/config.py`** is the centre of a1, set to work
  X0 Y0 at startup. Verify the physical alignment and the measured `Z_BOARD`.

Also note that **no axis is homed**: X and Y have no limit switches, and Z's is not
wired, so `$H` has nothing to home and cannot establish an origin — see the
`G10 L20` stopgap documented at the bottom of `fluidnc/config.yaml`. Because Z has
no switch and no soft limits either, park the Z carriage at the top of its
travel before powering on/resetting or opening the Python serial connection;
that physical position is the controller's initial Z0. The board is Z-115,
pickup is Z-97, and low carry is Z-82. The mechanical travel is still 170 mm.
Automatic play currently fails the motion config's envelope check: top
clearance is 115 mm, but the configured grip height and high lift need 120 mm.
Resolve that physical discrepancy before running the game; do not bypass it.
When migrating from the old coordinates, upload the updated FluidNC config
and update the Pi's motion config together. After boot, with Z still at the
top and the gripper centred over a1, send `G21`, `G54`, and
`G10 L20 P1 X0 Y0 Z0` to set the new work origin without moving any axis.
See the coordinate setup in `fluidnc/ESP32_README.md`.

## 7. Later (not needed yet)

- Piper TTS (nicer voice than espeak): needs the `piper` binary + a `.onnx` voice
  model, then `--tts piper` with `PIPER_MODEL=/path/voice.onnx`. Skip until the
  espeak voice becomes annoying.
