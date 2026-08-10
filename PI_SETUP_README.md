# VoiceChess — Raspberry Pi Setup

Quick reference for bringing the Pi up from a cold start: connect, activate the
environment, configure audio, verify.

---

## 1. SSH into the Pi

```bash
ssh anton@voicechess.local
```

If mDNS fails, find the IP and connect directly:

```bash
ping voicechess.local
# or scan the LAN:  nmap -sn 192.168.1.0/24
ssh anton@<ip>
```

## 2. Activate the virtualenv

```bash
cd ~/voicechess
source venv/bin/activate
```

Prompt should show `(venv)`. If the venv doesn't exist yet:

```bash
python3 -m venv ~/voicechess/venv
source ~/voicechess/venv/bin/activate
```

## 3. Install requirements

```bash
pip install -r requirements.txt
```

`externally-managed-environment` error → you are not in the venv. Re-run step 2.
Debian (PEP 668) blocks pip on the system Python because apt and pip both write
to `dist-packages` and neither tracks the other. Do not use
`--break-system-packages`.

System-level dependencies that pip cannot provide:

```bash
sudo apt install -y libportaudio2 espeak-ng unzip stockfish
```

For spoken output, `espeak-ng` (above) is all the Pi needs — `--tts espeak` calls
it directly. The `pyttsx3` wrapper is only required for the `--tts pyttsx3`
backend, which exists for Windows dev boxes:

```bash
pip install pyttsx3      # optional, Windows only
```

## 4. Install the Vosk speech model

Not in `requirements.txt` — it's a ~40 MB model directory, not a pip package.
Without it the engine drops to text-input mode.

```bash
cd ~/voicechess/voice_matching
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
rm -rf model
mv vosk-model-small-en-us-0.15 model
rm vosk-model-small-en-us-0.15.zip
ls model/
```

That last command must show `am  conf  graph  ivector  README` **directly**
inside `model/`. If you instead see `vosk-model-small-en-us-0.15/`, the contents
are nested one level too deep — same error as a missing model:

```bash
mv model/vosk-model-small-en-us-0.15/* model/
```

> This is the **only** step that needs internet. Everything downstream is fully
> offline — get the model onto the SD card ahead of time and back it up, so
> venue WiFi is never a demo-day dependency.

The small model is the right choice: the grammar is constrained per-utterance to
the ~20–40 legal moves, so a larger acoustic model buys almost nothing while
costing load time and RAM.

---

## Audio hardware

| Device | Model | ALSA card name | Direction |
|---|---|---|---|
| Mic | SunFounder USB 2.0 Mini Mic (M-305) | `Device` | capture only |
| Speakers | Logitech S150 USB Stereo | `AUDIO` | playback only |

No drivers needed — both are USB Audio Class devices handled by `snd-usb-audio`.

> Reference cards by **name** (`CARD=AUDIO`), never by index. Indices shift with
> USB enumeration order on replug or reboot.

```bash
lsusb                      # confirm both enumerate
cat /proc/asound/cards     # index → name mapping
arecord -l                 # capture devices (mic)
aplay -l                   # playback devices (speakers)
```

## 5. Configure the speaker

Verify the hardware works before touching config:

```bash
espeak-ng "testing one two three" --stdout | aplay -D plughw:CARD=AUDIO,DEV=0
```

Silent? Check the S150's physical volume wheel and mute button first.

Then write `~/.asoundrc` — this sets playback → speakers **and** capture → mic in
one file. `softvol` synthesizes a volume control because the S150 exposes no
hardware mixer.

```bash
cat > ~/.asoundrc << 'EOF'
pcm.softvol_out {
    type softvol
    slave.pcm "plughw:CARD=AUDIO,DEV=0"
    control { name "SoftMaster"; card AUDIO }
}

pcm.!default {
    type asym
    playback.pcm "softvol_out"
    capture.pcm { type plug; slave.pcm "hw:CARD=Device,DEV=0" }
}

ctl.!default {
    type hw
    card AUDIO
}
EOF
```

`type plug` inserts ALSA's conversion layer so mono 16 kHz gets resampled to
whatever the hardware wants. Without it: `Invalid sample rate` (-9997).

The `SoftMaster` control doesn't exist until audio passes through it once:

```bash
espeak-ng "volume test" --stdout | aplay
alsamixer -c AUDIO                        # now shows a slider
amixer -c AUDIO sset SoftMaster 75%       # or set it from a script
```

Keep `SoftMaster` near 100% (softvol attenuates digitally, costing headroom) and
set overall loudness with the physical knob.

## 6. Configure the microphone

```bash
alsamixer -c Device
```

Press **F4** for the Capture view. If the slider reads `MM` at the bottom, press
**Space** to enable capture — capture mute is Space, not M. Set gain to ~80%.

Auto Gain Control on is fine for the M-305 (weak capsule). If Vosk starts
misfiring on room hiss between utterances, toggle AGC off with **M** and raise
manual gain instead.

## 7. Test both

Record from the mic, play it back through the speakers:

```bash
arecord -D plughw:CARD=Device,DEV=0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/t.wav
aplay -D plughw:CARD=AUDIO,DEV=0 /tmp/t.wav
```

Test that the **defaults** in `~/.asoundrc` are actually being applied — note the
absence of any `-D` flag:

```bash
espeak-ng "default routing works" --stdout | aplay
```

If this speaks, everything that doesn't name a device explicitly (pyttsx3,
Piper's `aplay` subprocess, `speaker-test`) lands on the S150.

Live input meter — the decisive check that the mic is capturing signal at all:

```bash
arecord -vv -V mono -f S16_LE -r 16000 -c 1 -d 10 /dev/null
```

Talk into it. A flat bar means no capture — go back to step 6.

---

## 8. Run the game

```bash
python main.py                                      # live mic if available, else auto-falls back to text
python main.py --text                               # force text mode — type your moves, no mic/model needed
python main.py --text --script "e2e4,e7e5,g1f3"     # scripted, fully hardware-free (best quick smoke test)
python main.py --tts espeak                         # live mic + spoken feedback (Pi: espeak-ng)
python main.py --tts espeak --voice male            # ...with the male narrator voice
python main.py --text --tts espeak --voice female   # typed input + spoken feedback (female = default)
python main.py --tts pyttsx3                        # spoken feedback on a Windows dev box
```

Reading the startup banner:

- `[SPEAK] ...` printed instead of heard → the TTS backend failed to start and
  fell back to `PrintSpeaker`. The parenthetical on that line names the reason.
- `Falling back to text-input mode` → Vosk model missing or nested wrong
  (step 4).
- `serial: dry-run (print)` → no ESP32 on `/dev/ttyUSB0`; G-code is printed, not
  streamed. Only one process can hold the port at a time, so kill any `screen`
  session first (`Ctrl-A k y`).

## 9. Narrator voice

`--tts espeak` calls espeak-ng directly. Two presets, both tuned for square-name
clarity rather than prose:

| Preset | Voice | Rate | Pitch |
|---|---|---|---|
| `female` (default) | `en-us+f2` | 130 wpm | 40 |
| `male` | `en-us+m3` | 130 wpm | 40 |

The slow rate and lowered pitch matter more than the voice choice — a listener
has no semantic context to correct a misheard "b4" vs "d4", so articulation beats
naturalness here.

Audition other variants before changing the presets in `chess_ai/speech.py`:

```bash
espeak-ng --voices=variant                                    # list all variants
ls /usr/lib/aarch64-linux-gnu/espeak-ng-data/voices/\!v/      # same, on disk
espeak-ng -v en-us+f5 -s 130 -p 40 "b4. d4. e4. g4. c4. b2. d2."
```

Test with confusable letters like the line above, not with a normal sentence.

> Voices listed with `mb/` in `espeak-ng --voices=en` are **MBROLA** and will not
> work — they need the `mbrola` binary and per-voice data packages. Use the
> `gmw/` base voices (`en-us`, `en-gb`, `en-gb-x-rp`, ...) with `+variant`.

`--tts pyttsx3` is a separate backend kept for Windows dev boxes, where it drives
the built-in Microsoft voice. On the Pi it just wraps espeak-ng with less control,
so prefer `--tts espeak` there.

---

## Notes

- `~/.asoundrc` is **per-user**. If the orchestrator later runs as a systemd
  service under another user, move the same content to `/etc/asound.conf`.
- `sounddevice` uses PortAudio's own device numbering, which does not match
  ALSA's. With the defaults above, leave `config.DEVICE_INDEX = None`.
- `--tts espeak` needs only the apt package. `--tts pyttsx3` additionally needs
  `pip install pyttsx3`; without it `get_speaker()` falls back silently to
  `PrintSpeaker`.
- Vosk model lives in `voice_matching/model/`. Missing → the engine drops to
  text-input mode. Contents must sit directly inside `model/` (`am/`, `conf/`,
  `graph/`, `ivector/`), not nested one level deeper.