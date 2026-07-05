# Project: VoiceChess CPS — Voice-Controlled Chess Robot

A Cyber-Physical Systems course project: a voice-controlled automatic chess board built
for accessibility (handless, immobile, or blind players). The player speaks a move
("pawn to e4"), an AI opponent replies, and a gantry with a claw physically moves
the pieces on a real board. Voice in, audio feedback out — no screen, no phone, no hands.

**This is a SIMPLE DEMO, not production software.** No security/auth, no networking
layer, no persistence, no camera/vision, no move verification. Always favor the simplest
thing that demonstrates the concept.

The user is a full-stack web developer leading the software side of a 5-person team.
Map embedded/robotics concepts to web analogies where useful (firmware = device-driver
service; the orchestrator = a backend service coordinating a saga). Be concise — don't
over-explain basics.

## System architecture (canonical mental model)

    Voice -> Brain (RPi 5) -> ESP32 (FluidNC firmware / muscle) -> motors + claw

Everything in this repo runs as **one Python process on the Pi** — modules talk via
plain in-process function calls. The only real wire protocol in the whole system is a
future USB-serial link to the ESP32 (G-code-like lines, per-line acks + `<Idle>`
polling), which does not exist in this repo yet.

Both players' moves originate in software, so the system always knows the full board
state — nothing physical to reconcile, no camera.

- **Voice input**: USB microphone plugged directly into the Pi. Speech-to-text runs
  ON THE PI (Vosk, offline, with a constrained grammar limited to the current legal
  moves). There is no phone anywhere in this system.
- **Brain (RPi 5, 4GB)** — single compute node and source of truth: on-device STT,
  phonetic move matching, chess rules/state (python-chess), AI opponent (Stockfish),
  motion planning (square -> XY mm -> steps), audio feedback (TTS). Vosk (~300MB) +
  Stockfish NNUE (~50-150MB) + Piper TTS (~100MB transient) fit comfortably because
  the turn-based state machine serializes them — they never run concurrently.
- **ESP32** — the muscle. Flashed with **FluidNC** (off-the-shelf CNC firmware, not
  custom code) which handles the real-time stepper pulses (STEP/DIR), acceleration
  ramps, claw actuation, homing, and limit switches. Doesn't know or care that input
  is voice — same low-level device driver regardless of source.
- **Audio output**: speaker confirms the player's move, announces the AI's reply, and
  reports errors. High value — this is what makes the board usable by blind players.
  **Only one move is ever spoken per side per turn.** The legal-move list is passed to
  the voice matcher as silent matching input — it is text, never audio, and it is
  never read aloud.

One turn end to end: say "pawn to e4" -> on-device STT -> match to a legal move (e2e4)
-> speak confirmation -> plan motion -> gantry moves piece -> Stockfish replies (e7e5)
-> speak it -> gantry moves the AI's piece -> back to listening.

### Key design rule (the make-or-break detail)

Spoken chess letters are highly confusable (b/d/e/g/p/t/c/z). **Never rely on open
transcription.** At any position there are only ~20–40 legal moves: generate them all,
render each as the phrases a person might say ("pawn to e4" / "e4" / "pawn e4"), and
pick the best PHONETIC match to what was heard. Matching noisy audio against a small
known set beats open transcription. Spoken read-back catches the rare misses.

**The UCI string (e.g. "e2e4") is the representation crossing every module boundary.**
Keep it that way end to end. SAN (e.g. "Nf3") is only for human-facing speech/read-back.

## Repo layout & module ownership

    voice_matching/   Role 1 — Vosk STT + phonetic matching. match_text() is the
                      pure text classifier; listen_for_move() wraps it with live
                      audio plus a spoken yes/no confirmation layer (config.
                      REQUIRE_CONFIRMATION; prompts route through the
                      orchestrator's TTS via on_prompt). Both return a
                      MatchResult: status "legal" | "illegal" | "unrecognized"
                      (+ the move for legal/illegal — a UCI, or for illegal a
                      piece@dest pseudo like "knight@c3" when no origin was
                      spoken; describe_move renders both). Illegal detection
                      matches against a lazily-cached superset of all ~4k
                      square-pair moves (lean qualified phrases only — full
                      phrase sets for the superset would be ~8M strings and
                      would tie every "pawn to e4"-style utterance) plus one
                      piece@dest pseudo-move per (piece, square); pseudos for
                      currently-legal pairs are skipped so they never shadow
                      the legal match. Legal moves keep full piece-aware
                      phrases. Matching is strict: confidence threshold 0.90 +
                      AMBIGUITY_MARGIN runner-up gap (with a float epsilon:
                      exact-margin gaps must pass) + partial-[unk] reject.
                      Promotion UCIs are not in the superset (illegal
                      promotions -> unrecognized). "eight" must NEVER be an
                      h-file homophone — it pulls spoken "a" into the h-file
                      via the Vosk grammar ("pawn to a3" once silently played
                      h2h3); it is a rank-8 word only.
    chess_ai/         Role 2 — rules authority (python-chess), Stockfish AI, TTS
                      (get_speaker falls back to PrintSpeaker on any failure).
    motion/           Role 3 — REAL (merged 2026-07-05). square->mm + G-code for
                      standard/capture/castling/en-passant/promotion moves.
                      plan(uci, move_type, is_capture) returns ONE newline-joined
                      G-code string; the orchestrator splitlines() it for serial.
                      The orchestrator derives move_type/is_capture from Role 2's
                      board BEFORE applying the move (_classify_move).
    orchestrator/     Role 5 — the turn state machine (state_machine.py) and the
                      ESP32 serial link (serial_link.py — STUB until Role 4's
                      firmware exists; will become pyserial + ok/<Idle> polling).
    main.py           Entry point. `python main.py --text --script "e2e4,..."` runs
                      the whole pipeline with no mic/model/robot.
    tests/            pytest suites for voice_matching and chess_ai.
    docs/             agents.md, interfaces.md, changelog.md, original READMEs.

**Orchestration rule**: the orchestrator is the ONLY module that calls other modules.
Role 1 and Role 2 never call each other; Role 3 never talks to Role 4 at runtime.
Every result returns to the orchestrator before the next call goes out. Turn loop:

    LISTEN -> PARSE -> CONFIRM -> MOVE(human) -> THINK(AI) -> MOVE(AI) -> LISTEN

Only listen during LISTEN — never trigger audio capture during MOVE/THINK.

Role 3's real planner and Role 1's 0.2.0 update are merged (their raw clones were
merge inputs only and have been deleted; their history lives on their own remotes).
Role 4 (ESP32/FluidNC config + hardware build) is still another team member's work —
serial_link.py stays a stub; don't invent its logic. Don't change Role 1/2/3 logic
when integrating.

## Runtime facts

- Stockfish is a **system binary**, not pip. Fail loudly at startup if missing.
  (Debian/Pi: `sudo apt install stockfish`; Windows dev box: winget, or drop
  `stockfish.exe` in the repo root — main.py checks both.)
- If the Vosk model dir (`voice_matching/model/`, gitignored) is missing, don't crash:
  log it and fall back to text-input mode (same pattern as get_speaker's fallback).
- vosk/sounddevice may be uninstallable on dev machines — `--text` mode must keep
  working without them (their imports are guarded in voice_matching/engine.py).

## Hardware (for context; code rarely touches this)

**Compute**: Raspberry Pi 5 Model B **(4GB)**, official PSU, 32GB microSD, ESP32 dev
board. **Voice I/O**: USB microphone + small USB/3.5mm speaker, both on the Pi.
**Motion**: 4x TB6600 stepper drivers — 3x NEMA 23 (X/Y/Z axes, Z via rack-and-pinion)
+ 1x NEMA 17 (claw grip). **Note**: the claw was changed from an MG996R servo to the
NEMA 17 stepper by team decision; the electronics BOM spreadsheet still lists the old
servo line item — treat that entry as stale, don't reorder it. **Power**: Mean Well
LRS-350-24 (~14.6A) -> drivers; DC-DC buck converter 24V->5V for the ESP32; separate
Pi PSU; common ground across everything; motor-current wiring kept away from logic
wiring. Mechanical limit switches (already sourced) for homing.

This is a gantry with **3 linear axes (X/Y/Z) plus a claw actuator** — not a 2-axis
system. (Motor/axis assignment above is reconstructed from prior team discussion; the
current electronics BOM doesn't itemize the motors themselves, so double-check the
final assignment with whoever owns Role 3/4 before treating it as locked.)

### Decided (don't re-litigate these)

- **ESP32 firmware: FluidNC**, flashed as-is — no custom real-time firmware is being
  written. Role 4 owns the YAML config (pins, steps/mm, homing) and the physical build.
- **Claw actuator: NEMA 17 stepper** (not a servo — see hardware note above).
- **Z-axis**: vertical drop via rack-and-pinion (uses pinion pitch circumference in
  the steps_per_mm calculation, unlike the belt-driven X/Y axes).

### Still open — flag if a software choice depends on one

- **Board/square dimensions**: BOM lists the board as ~50x50cm overall, which doesn't
  cleanly match the 50mm-square / 400mm-board example math used elsewhere for the
  square->mm coordinate formula. Role 3's merged planner now HARDCODES 50mm squares
  (25mm offsets, graveyard at X420 Y200, queen reserve at X480 Y200) — verify against
  the physical build before the first powered run.
- **Kinematics**: plain Cartesian dual-rail vs. CoreXY/H-bot. A cross-shaft link
  between the two base X-rails (to prevent gantry racking) is planned regardless of
  which is chosen.
- Captures/promotion: Role 3's planner drops EVERY captured piece on the single point
  (X420, Y200) — pieces will physically pile up there; a sequential single-file strip
  is still the recommended evolution. Promotion always fetches a queen from one
  reserve point (X480, Y200) regardless of the promotion piece in the UCI move.

## Working conventions

- Simple demo: no security/networking/verification unless asked.
- Don't change Role 1/Role 2 matching/chess logic when integrating — packaging,
  imports, and orchestration only.
- Verbal/chat decisions override stale spreadsheet or BOM data when they conflict
  (e.g. the claw servo->stepper change above) — always sanity-check hardware "facts"
  in this file against the most recent team conversation, not just the BOM sheet.
- Keep this file updated when the architecture actually changes (e.g. when Role 3/4
  real code lands and stubs are replaced, or when open decisions above get resolved).