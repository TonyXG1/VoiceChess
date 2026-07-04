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

## 6. Later (not needed yet)

- ESP32 serial link: `sudo usermod -aG dialout $USER` (then log out/in) so Python
  can open `/dev/ttyUSB0` without sudo. The `pyserial` package is already in
  requirements.txt.
- Piper TTS (nicer voice than espeak): needs the `piper` binary + a `.onnx` voice
  model, then `--tts piper` with `PIPER_MODEL=/path/voice.onnx`. Skip until the
  espeak voice becomes annoying.
