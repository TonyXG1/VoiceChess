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
- **The claw is the A axis** (`rc_servo` on gpio.19), not a spindle: `G0 A0` opens;
  the measured grip angle depends on the piece (A35/A43/A62/A76). As an axis it
  queues in move order with XY/Z for free. A bare
  `M3` would be a no-op anyway (spindle speed defaults to 0). The YAML key is
  `pwm_hz`, not the `pwm_freq` the FluidNC wiki prints.
- **FluidNC boots into Alarm** *when homing is enabled*, answering every G-code line
  with `error:9` until `$H` runs. `$H` is a `$` command, not G-code, so the serial
  link owns it — never the planner. **Right now homing is disabled on every axis**
  (`must_home: false`, all cycles `0`), so it boots ready and `$H` has nothing to
  home — `--no-home` is mandatory until switches are wired.

Geometry (all in `motion/config.py`): confirmed squares are **58 x 58 mm**,
with a 464 x 464 mm playing area and a 20 mm border on all four sides
(504 x 504 mm overall). A square's coordinate is its CENTER, so
`a1 = (0, 0)` is the work origin at a1's CENTRE; `h8 = (406, 406)`.
There is no half-square offset in the planner. Start centred over a1.
Z starts fully up at `Z0` and increases downward: `Z_TOP = 0`, board surface at
`Z_BOARD = 115`, and empty/high travel is `Z0`. Measured pickup profiles are
pawn Z85/A62, knight Z75/A76, bishop Z80/A43, rook Z90/A43, queen Z67/A35,
and king Z67/A35. Low carry is 15 mm above that piece's pickup Z.
Requested top clearance is
115 mm (11.5 cm) to the playing surface; physically set and verify it at Z0.
Full mechanical travel remains 170 mm (bottom Z170). High loaded routes travel
at Z0 through adjacent square centres while avoiding all king and queen squares.
The shallowest grip leaves 67 mm below the carried piece, clearing the tallest
unprotected piece (65 mm bishop) by 2 mm. Off-board capture and promotion
positions are configured.
Both X direction pins are inverted (`gpio.14:low`, `gpio.18:low`). On the built
gantry, +X follows the a-file from a1 toward a8, while +Y follows rank 1 from a1
toward h1. Thus `b1 = (0, 58)` and `a2 = (58, 0)`.

**Lift policy** — the claw must never drag a piece across the board:

- Sliding pieces (R/B/Q/K/P) carry LOW. Chess rules guarantee their path is empty.
- Knights carry HIGH at Z0. The orchestrator supplies the mover/captured piece
  types and every current king/queen square, so Role 3 can choose the measured
  profile and route around protected squares without importing `chess`.
- Graveyard trips, the queen-reserve trip, and castling's ROOK leg are always HIGH —
  each may cross occupied squares. Castling updates the protected square from the
  king's old square to its new square before routing the rook.

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
| X | **2x** NEMA 23 (ganged, dual rail) | gpio.13, gpio.16 | gpio.14, gpio.18 | — |
| Y | NEMA 23 | gpio.27 | gpio.26 | — |
| Z | **NEMA 17**, rack-and-pinion | gpio.25 | gpio.33 | limit gpio.17 — **not wired** |
| A | **claw micro-servo (SG90)** | — | — | `rc_servo` gpio.19, 1000-2000us @50Hz |

X step is on **gpio.13, not gpio.12**: gpio.12 is the flash-voltage strapping pin, and
a common-anode TB6600 pulls it high at boot, putting the ESP32 into a permanent boot
loop that is only fixable by physically removing the wire.

Motor count is 3x NEMA 23 + 1x NEMA 17 (X ×2, Y, Z) — that is all four TB6600s, so
there is no driver spare for a stepper claw. The claw is an **SG90 9g micro-servo**
on a single signal wire; it needs no driver. `gpio.23` is free.

**Dimensions**: playing area 464 x 464mm, 58mm squares, 20mm border on all
four sides (504 x 504mm overall; outer edges -49..455 from a1's centre).
Usable travel from
a1's centre is X0..535mm and Y0..545mm; full Z travel is 170mm.
Claw opening 60mm outside / 45mm inside; confirmed internal depth 30mm
(does not establish pickup height or servo endpoints). Piece heights: king 76, queen
75, bishop 65, knight 58, rook 47, pawn 45. Loaded paths route around kings and
queens; at Z0, every measured grip profile clears the 65 mm bishop.

This is a gantry with **3 linear axes (X/Y/Z) plus a claw actuator** — not a 2-axis
system.

### Decided (don't re-litigate these)

- **ESP32 firmware: FluidNC**, flashed as-is — no custom real-time firmware is being
  written. Role 4 owns the physical build; `fluidnc/config.yaml` now exists in-repo.
- **Claw actuator: an SG90 9g micro-servo on the A axis**, opened with `G0 A0`;
  measured closing commands are piece-specific A35/A43/A62/A76 (reverted from
  the NEMA 17 stepper; the pin table is the authority).
  `fluidnc/config.yaml` briefly declared it as a `stepstick` — that was wrong and
  the claw would never have gripped. Confirmed SG90 by the team on 2026-09-04.
- **Z-axis**: vertical drop via rack-and-pinion (uses pinion pitch circumference in
  the steps_per_mm calculation, unlike the belt-driven X/Y axes).
- **Z scale**: bench calibration commanded 100 units and measured 20 mm, replacing
  the theoretical 50.930 value with `254.650 steps/mm`.
- **Microstepping: 1/16 on all four drivers** (S1 OFF, S2 OFF, S3 ON) = 3200 pulse/rev
  -> 80 steps/mm on the GT2/20T belt axes. Must match the physical DIPs.
- **Board geometry**: confirmed 58mm squares with a 20mm outer border.
- **Off-board storage**: 16 capture positions form an L at Y485 and X485;
  promotion reserves are White X403 Y540 and Black X530 Y540.

### Still open — flag if a software choice depends on one

- **No axis is homed.** X and Y have no limit switches in the pin table, and Z's
  (gpio.17) is not wired yet, so all three are `cycle: 0` / `NO_PIN` and `$H` has
  nothing to home. After any power cycle the machine knows neither where a1 is nor
  how high the claw sits. **This is the largest risk to a working demo.** Stopgap
  documented in `fluidnc/config.yaml`: select G54, jog to a1's centre, then
  `G10 L20 P1 X0 Y0` (preferred
  over `G92`, which is an offset that survives in surprising ways).
  Z is switchless **on purpose** for bench testing, which means `soft_limits: false`
  and no controller-side backstop — park the Z carriage at the top of its
  travel before power-on/reset or opening the Python serial port so the
  controller's initial Z0 is physically correct. After boot at the top, select
  `G21`, `G54`, then `G10 L20 P1 X0 Y0 Z0` while centred over a1 when migrating
  old work offsets.
- **`BOARD_ORIGIN_X/Y`** in `motion/config.py` identifies a1's centre, calibrated
  to X0 Y0. Verify alignment and the measured `Z_BOARD` on the built machine.
- **NEMA 23 current rating**: 2.8A or 4.2A? Decides whether the TB6600 DIP ceiling is
  2.8A or 3.5A (the driver caps at 3.5A continuous either way).
- **Claw clearance**: the L-shaped capture area's positions are at least 62 mm
  apart, just wider than the claw's 60 mm open outer width.
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
