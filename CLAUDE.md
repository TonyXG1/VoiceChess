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
    motion/           Role 3 — REAL. square->mm + G-code for standard/capture/
                      castling/en-passant/promotion moves. Retargeted to the real
                      machine 2026-08-10 (see "Motion / G-code contract" below).
                      config.py holds EVERY physical constant — it is the one file
                      to edit when calibrating; planner.py is pure string-building
                      and imports no chess library. plan(uci, move_type,
                      is_capture, high_lift) returns ONE newline-joined G-code
                      string; the orchestrator splitlines() it for serial.
                      startup() returns the one-time G21/G90/G94 preamble.
    orchestrator/     Role 5 — the turn state machine (state_machine.py) and the
                      ESP32 serial link (serial_link.py — REAL pyserial sender,
                      gated behind main.py's --serial <port>). No port = dry-run:
                      it just prints [SERIAL-STUB] lines, so tests/CI/--text are
                      unchanged. With a port it homes ($H), streams each G-code
                      line, and reads ok/<Idle> acks. Dry-run stays TOLERANT; a
                      real port is STRICT (an error: reply raises SerialError
                      instead of streaming into a wedged machine) — pass
                      strict=False for pre-flash bench work, as serial_test.py does.
    fluidnc/          config.yaml for the ESP32 — pin map, steps/mm, travel,
                      homing. Generated 2026-08-10 from the team's pin table so it
                      and motion/config.py come from the same numbers. Role 4 still
                      owns it; [BLOCKED]/[VERIFY] markers flag what needs sign-off.
    tools/            gcode_preview.py — dry-run a planned move, trace it, time it,
                      and fail on any position outside the machine envelope. Run it
                      before the first powered move and after any config.py change.
    main.py           Entry point. `python main.py --text --script "e2e4,..."` runs
                      the whole pipeline with no mic/model/robot; add
                      `--serial /dev/ttyUSB0` to stream the G-code to the ESP32
                      (`--no-home` skips $H for bench testing).
    tests/            pytest suites for voice_matching, chess_ai, motion, and the
                      serial link.
    docs/             agents.md, interfaces.md, changelog.md, original READMEs.

**Orchestration rule**: the orchestrator is the ONLY module that calls other modules.
Role 1 and Role 2 never call each other; Role 3 never talks to Role 4 at runtime.
Every result returns to the orchestrator before the next call goes out. Turn loop:

    LISTEN -> PARSE -> CONFIRM -> MOVE(human) -> THINK(AI) -> MOVE(AI) -> LISTEN

Only listen during LISTEN — never trigger audio capture during MOVE/THINK.

Role 3's real planner and Role 1's 0.2.0 update are merged (their raw clones were
merge inputs only and have been deleted; their history lives on their own remotes).
Role 4 (the physical build + flashing the ESP32) is still another team member's work.
The Pi-side serial SENDER (serial_link.py) is real, and `fluidnc/config.yaml` was
generated on 2026-08-10 at the user's direction so the controller and the planner
share one set of numbers — but Role 4 still owns the file and the build. Don't
change Role 1/2 logic when integrating.

## Motion / G-code contract

The dialect is **GRBL/FluidNC, not Marlin**. Four differences have already caused
real bugs here; don't reintroduce them:

- **`G4 P` is SECONDS.** `G4 P500` dwells for 8 minutes, not 500ms.
- **`G0` ignores `F`.** Rapid speed comes from `max_rate_mm_per_min` in the YAML.
  Only `G1` carries a feed word.
- **The claw is the A axis** (`rc_servo` on gpio.19), not a spindle: `G0 A0` opens,
  `G0 A45` closes. As an axis it queues in move order with XY/Z for free. A bare
  `M3` would be a no-op anyway (spindle speed defaults to 0).
- **FluidNC boots into Alarm** with homing enabled and answers every G-code line
  with `error:9` until `$H` runs. `$H` is a `$` command, not G-code, so the serial
  link owns it — never the planner.

Geometry (all in `motion/config.py`): squares are **57.0 x 57.375 mm**, derived from
the board's measured 456 x 459 mm spans rather than the nominal 58 mm — the nominal
value drifts ~8mm by the h-file. A square's coordinate is its CENTER, so
`a1 = (28.50, 28.69)` and `h8 = (427.50, 430.31)`. Z homes to the top, so the work
envelope is **negative**: `Z_TOP = 0`, board surface at `Z_BOARD = -150`, grip at
`-132`, low carry `-117`, safe/high carry `-22`.

**Lift policy** — the claw must never drag a piece across the board:

- Sliding pieces (R/B/Q/K/P) carry LOW. Chess rules guarantee their path is empty.
- Knights carry HIGH (clear of the 95mm king). This is the ONLY chess knowledge the
  motion path needs, and the orchestrator derives it (`_classify_move` returns
  `(move_type, is_capture, high_lift)`) so Role 3 never imports `chess`.
- Graveyard trips, the queen-reserve trip, and castling's ROOK leg are always HIGH —
  each crosses occupied squares. (Kingside, the rook h1->f1 passes through g1 where
  the king now stands; moving the rook first just swaps who is in whose way.)

`tools/gcode_preview.py --all` asserts every scenario stays in the envelope; the
same check runs in `tests/test_motion.py`, along with an invariant that XY never
moves below carry height.

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
**Power**: Mean Well LRS-350-24 (~14.6A) -> drivers; DC-DC buck converter 24V->5V for
the ESP32; separate Pi PSU; common ground across everything; motor-current wiring kept
away from logic wiring.

**Motion** — 4x TB6600 drivers, per the team's ESP32 pin table (2026-08-10), which
**supersedes the earlier reconstruction** that put NEMA 23s on all three axes and a
NEMA 17 on the claw:

| Axis | Motor | Step | Dir | Extras |
|---|---|---|---|---|
| X | **2x** NEMA 23 (ganged, dual rail) | gpio.12, gpio.16 | gpio.14, gpio.18 | — |
| Y | NEMA 23 | gpio.27 | gpio.26 | — |
| Z | **NEMA 17**, rack-and-pinion | gpio.25 | gpio.33 | limit `gpio.17:low:pu` |
| A | **claw micro-servo** | — | — | `rc_servo` gpio.19, 1000-2000us @50Hz |

Motor count is unchanged (3x NEMA 23 + 1x NEMA 17); the *assignment* moved. The
electronics BOM's MG996R servo line item — previously marked stale — is live again.

**Dimensions**: board 456mm (a-h) x 459mm (1-8), 57mm squares. Travel 720mm on X and
Y, 170mm on Z. Claw opening 60mm outside / 45mm inside. Piece heights: king 95, queen
75, bishop 65, knight 58, rook 46, pawn 45 — the 95mm king sets `LIFT_HIGH`, and the
45mm claw opening is what the piece bases must fit inside.

This is a gantry with **3 linear axes (X/Y/Z) plus a claw actuator** — not a 2-axis
system.

### Decided (don't re-litigate these)

- **ESP32 firmware: FluidNC**, flashed as-is — no custom real-time firmware is being
  written. Role 4 owns the physical build; `fluidnc/config.yaml` now exists in-repo.
- **Claw actuator: RC micro-servo on the A axis**, commanded `G0 A0` / `G0 A45`
  (reverted from the NEMA 17 stepper; the pin table is the authority).
- **Z-axis**: vertical drop via rack-and-pinion (uses pinion pitch circumference in
  the steps_per_mm calculation, unlike the belt-driven X/Y axes).
- **Microstepping: 1/16 on all four drivers** (S1 OFF, S2 OFF, S3 ON) = 3200 pulse/rev
  -> 80 steps/mm on the GT2/20T belt axes. Must match the physical DIPs.
- **Board geometry**: 57.0 x 57.375mm squares from the measured spans, not 58mm.
- **Graveyard is a 4x8 grid** (X500-686, Y40-425), not a single point — 32 slots for
  the 30 capturable pieces. Queen reserve has one row per colour.

### Still open — flag if a software choice depends on one

- **X and Y limit switches are not in the pin table** (only Z has one). Without them
  `$H` cannot establish a repeatable origin, so after any power cycle the machine
  does not know where a1 is. **This is the largest risk to a working demo.** Stopgap
  documented in `fluidnc/config.yaml`: jog to a1 and `G92 X28.50 Y28.69`.
- **Z pinion module + tooth count** — needed for Z `steps_per_mm`. The YAML currently
  assumes module 1.0 / 20 teeth (50.930 steps/mm). A wrong value here makes every
  grip miss the piece or drive the claw into the board.
- **`BOARD_ORIGIN_X/Y` and `Z_BOARD`** in `motion/config.py` are placeholders until
  measured on the built machine. Everything else derives from them.
- **NEMA 23 current rating**: 2.8A or 4.2A? Decides whether the TB6600 DIP ceiling is
  2.8A or 3.5A (the driver caps at 3.5A continuous either way).
- **Claw jaw axis**: the graveyard grid's 62mm X spacing assumes the jaws open along
  X. If they open along Y, swap `GRAVEYARD_DX`/`GRAVEYARD_DY`.
- **Kinematics**: plain Cartesian dual-rail vs. CoreXY/H-bot. A cross-shaft link
  between the two base X-rails (to prevent gantry racking) is planned regardless of
  which is chosen.
- **Underpromotion** still always places a queen; the planner now emits a `WARNING`
  comment rather than substituting silently.

## Working conventions

- Simple demo: no security/networking/verification unless asked.
- Don't change Role 1/Role 2 matching/chess logic when integrating — packaging,
  imports, and orchestration only.
- Verbal/chat decisions override stale spreadsheet or BOM data when they conflict
  (e.g. the claw servo->stepper change above) — always sanity-check hardware "facts"
  in this file against the most recent team conversation, not just the BOM sheet.
- Keep this file updated when the architecture actually changes (e.g. when Role 3/4
  real code lands and stubs are replaced, or when open decisions above get resolved).