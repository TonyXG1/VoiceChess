# VoiceChess

A voice-controlled automatic chess board, built as a Cyber-Physical Systems course
project. You **say** your move ("pawn to e4"), the board **confirms it out loud**,
a gantry with a claw physically moves your piece, then Stockfish replies and the
robot moves its own piece too. No screen, no phone, no hands — designed for
accessibility (handless, immobile, or blind players).

```
Voice ──> Brain (Raspberry Pi 5) ──> ESP32 (FluidNC) ──> motors + claw
           · Vosk speech-to-text        · stepper pulses
           · phonetic move matching     · homing / limits
           · python-chess + Stockfish
           · text-to-speech feedback
```

Everything in this repo runs as **one Python process** on the Pi. One turn:

> you say *"pawn to e4"* → offline STT → phonetic match against the current legal
> moves → *"You said Pawn to e4"* → gantry moves your piece → Stockfish thinks →
> *"A I plays Knight to f6"* → gantry moves its piece → listening again.

The key trick: spoken chess letters are highly confusable (b/d/e/g/p/t/c/z), so we
never transcribe freely. At any position there are only ~20–40 legal moves — we
render each as the phrases a person might say and pick the best **phonetic** match
to what was heard. The spoken read-back catches the rare miss.

## Repo layout

| Path | What it is |
|---|---|
| `voice_matching/` | Vosk STT + phonetic matching (Role 1) |
| `chess_ai/` | Rules authority, Stockfish opponent, text-to-speech (Role 2) |
| `motion/` | Square→XY motion planning — **stub** until Role 3's code lands |
| `orchestrator/` | Turn state machine + ESP32 serial link (**stub** until Role 4) |
| `main.py` | Entry point — wires the modules together |
| `speak_test.py` | Standalone audio check — run this first on new hardware |
| `docs/` | `pi-setup.md` (Pi runbook), `interfaces.md`, `changelog.md` |
| `tests/` | pytest suites |

## Setup

Needs **Python 3.10+** and the **Stockfish binary** (a system program, not pip):

```
Windows:            winget install --id Stockfish.Stockfish -e
Raspberry Pi/Debian: sudo apt install stockfish
```

Then install the Python packages:

```bash
pip install -r requirements.txt
```

That's everything for **text mode** — the whole pipeline with typed moves instead
of a microphone:

```bash
python main.py --text                            # type your moves
python main.py --text --script "e2e4,g1f3"       # scripted, fully hands-free
```

### Live voice (microphone)

Download a Vosk model (~40 MB) into `voice_matching/model/`:

```bash
cd voice_matching
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && mv vosk-model-small-en-us-0.15 model
```

If the model or mic is missing, the game logs it and falls back to text mode —
it never crashes.

### Spoken audio (text-to-speech)

```bash
python speak_test.py            # quick check: do you hear four sentences?
python main.py --tts espeak     # play with spoken feedback
```

`--tts espeak` calls the espeak-ng binary on Linux (`sudo apt install espeak-ng`)
and automatically falls back to pyttsx3 (`pip install pyttsx3`) on Windows — same
flag everywhere. Without `--tts`, speech is printed as `[SPEAK] ...` lines instead.

### Raspberry Pi

Full fresh-Pi runbook (apt packages, audio device discovery, test ladder):
**[docs/pi-setup.md](docs/pi-setup.md)**.

## Playing

You are White; speak when you see `[VOICE] listening...`. Say moves any natural
way: *"pawn to e4"*, *"e4"*, *"knight f3"*, *"kingside castle"*. The board
confirms every move out loud before executing it. Take your time — silence never
fails a turn.

Useful flags: `--skill 0-20` (opponent strength, default 5), `--think 0.5`
(seconds per AI move), `--turns 40` (demo safety limit).

## Tests

```bash
python -m pytest tests/ -q
```

## Status

Voice input, chess AI, and audio feedback are fully working. Motion planning
(Role 3) and the ESP32/FluidNC hardware link (Role 4) are stubs with stable
interfaces — their real implementations drop in without touching the game logic.
