# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-09-06

### Changed

- High loaded moves, including knights, now use one coordinated XY segment
  whenever the direct path is clear. Safe detour waypoints remain in use only
  when the segment would enter a square occupied by a king or queen.
- Loaded gameplay carries now use `F2000` (2,000 mm/min, or 33.3 mm/s), raised
  from the original `F1200`. Empty X positioning is capped at 3,000 mm/min;
  unloaded Y positioning and Z rapids remain capped at 4,000 mm/min.
- The narrator now says `Your turn` at the start of the game and immediately
  after every completed AI move, before listening for White's next move.
- Serial acknowledgement handling now gives FluidNC `G4` dwell commands the
  full motion timeout. FluidNC completes queued moves before acknowledging a
  dwell, so the previous two-second timeout falsely aborted the first pickup
  at `G4 P0.5`. Ordinary command acknowledgements now allow ten seconds to
  tolerate planner-buffer flow control.
- Z coordinates now match the built machine: Z0 is fully raised and positive Z
  moves the gripper downward. The board is therefore Z115 and the mechanical
  bottom is Z170 after calibration.
- XY square mapping now matches the physical gantry shown at its a1 start:
  58 mm increments, with +X following the a-file toward a8 and +Y following
  rank 1 toward h1. The playing area is 464 x 464 mm, the 20 mm border makes
  the board 504 x 504 mm overall, and h8 is X406 Y406.
- Usable travel limits are X535 mm and Y545 mm from the a1 work origin.
- Pickup and grip are calibrated per piece: pawn Z85/A62, knight Z75/A76,
  bishop Z80/A43, rook Z90/A43, queen Z67/A35, and king Z67/A35. A0 remains
  open; positive Z moves down.
- High loaded travel now runs at Z0 with coordinated XY segments and safe
  waypoints around protected squares.
  The orchestrator supplies current king/queen squares and the planner routes
  around them. The minimum 67 mm carried-piece clearance passes over the tallest
  remaining obstacle, the 65 mm bishop, with 2 mm clearance.
- Z `steps_per_mm` is calibrated from `50.930` to `254.650`: a commanded
  100-unit move physically travelled 20 mm.
- The temporary flat capture area is L-shaped: eight positions run along Y485
  parallel to the h-file and eight run along X485 beyond rank 8. Positions are
  62 mm apart; captures arrive at Z0 and release at Z40. Clear the area and
  reset the planner after 16 captures.
- One promotion queen per colour is reserved in the outer corner: White at
  X403 Y540 and Black at X530 Y540. Both use the queen Z67/A35 profile.
- All standalone FluidNC position tests use `G1 F1500` for controlled X/Y/Z
  motion and retain their four-second `G4 P4` inspection pauses.
- Added a FluidNC all-squares test that visits every board centre in a snake
  path, lowers to Z60, immediately retracts to Z0, and parks at a1.

## [0.4.1] - 2026-09-04

Corrects the FluidNC config against the real hardware. Scope: `fluidnc/`,
`motion/config.py`, `tools/`. No planner logic changed and no emitted G-code
changed — `plan()` produces byte-identical output.

### Fixed

- **The claw would never have gripped** (`fluidnc/config.yaml`): the A axis was
  declared as a `stepstick`, so FluidNC would have fired ~400 narrow 6µs STEP
  pulses at gpio.19 instead of a 50Hz PWM. The claw is an **SG90 9g micro-servo**.
  Reverted to `rc_servo` (`pwm_hz: 50`, 1000–2000µs, `max_travel_mm: 90`), which
  maps `A0 -> 1000µs` open and `A45 -> 1500µs` closed — exactly what
  `CLAW_OPEN_A` / `CLAW_CLOSED_A` already assumed.

  This undoes the A-axis change from `cb8b3f3`, whose stated reason ("would have
  pulsed a servo signal into a stepper driver") only held if the claw were a
  stepper. Every other file in the repo — `CLAUDE.md`, `motion/config.py`,
  `motion/planner.py`, the tests, and `gcode_preview.py`'s 0–90 A clamp — had
  continued to describe a servo. There is also no fifth TB6600 for a stepper
  claw: X(2) + Y(1) + Z(1) already uses all four. **`gpio.23` is now free.**

  The YAML key is `pwm_hz`, *not* the `pwm_freq` the FluidNC wiki prints —
  verified against `FluidNC/src/Motors/RcServo.h`.

### Changed

- **XY rapids 4000 -> 8000 mm/min, Z 1500 -> 2500** (`fluidnc/config.yaml`,
  mirrored in `tools/gcode_preview.py`). The 4000 was set in the first commit and
  was the only conservative number in the file with no written rationale; it
  cannot simply be removed, since `max_rate_mm_per_min` is required and defaults
  to 1000. Z mattered more than XY: a transfer moves Z by 250–440mm, more than it
  moves XY. Measured effect on planned move times: 21–35% faster.
  `acceleration_mm_per_sec2: 300` is unchanged — it is the anti-topple value, and
  it is what actually caps short moves (reaching 8000 needs 29.6mm of runway; a
  square is 57mm). `F_CARRY` is unchanged at 1200.
- **Z configured switchless** (`fluidnc/config.yaml`): `limit_pos_pin: NO_PIN`,
  `homing.cycle: 0`, `soft_limits: false`, so the axis can be bench-tested before
  its switch is wired. The negative envelope was already correct and is unchanged
  (`Z_TOP = 0`, board at `-150`). **No axis is now homed at all, so `$H` has
  nothing to home and `--no-home` is mandatory.** With no datum and no soft
  limits there is no controller-side backstop — the Z carriage must be parked at
  the top of its travel before power-on.
- **Piece heights encoded** (`motion/config.py`): `PIECE_HEIGHTS` now holds all
  six measured heights (king 76, queen 75, bishop 65, knight 58, rook 47, pawn
  45) and `TALLEST_PIECE` / `LIFT_HIGH` derive from it, replacing a hand-typed
  `95.0`. Added `CLAW_OPEN_MM` / `CLAW_GRIP_MM` (60/45) and `RAIL_LENGTH` (720),
  which had been comment-only. Values are unchanged — this makes them auditable.
- **Graveyard spacing is now checked, not asserted in a comment**
  (`motion/config.py`): `_check()` fails at import if any two capture positions
  are not farther apart than the claw's open outer width.

### Notes

- Board square geometry (`57.0 x 57.375`) is **deliberately untouched** pending
  re-measurement. The supplied figures conflict: 8 x 58 = 464, not the measured
  456mm a–h span.
- `steps_per_mm: 50.930` on Z is still the unverified module-1.0 / 20-tooth
  pinion assumption, and is now the largest remaining calibration risk.

## [0.4.0] - 2026-08-10

Retargets the motion path from placeholder geometry to the real machine. Scope:
`motion/`, `orchestrator/`, `fluidnc/`, `tools/`. (Entries below 0.4.0 are
`voice_matching`-only — that module's history starts this file.)

### Fixed
- **`G4 P500` dwelled for 500 SECONDS** (`motion/planner.py`): GRBL/FluidNC read
  `G4 P` in seconds; milliseconds is Marlin. Four dwells per standard move meant
  ~33 minutes of stalling. Now `G4 P0.5`, with a regression test asserting every
  dwell is a plausible number of seconds.
- **Carried pieces swept the board** (`motion/planner.py`): every carry used a
  fixed 40mm lift, below the 45mm pawn. Captures, castling and knight moves all
  drag pieces across occupied squares. Replaced with a lift policy — low carry
  only where chess rules guarantee an empty path, high carry (clear of the 95mm
  king) for knights, graveyard trips, the queen-reserve trip, and castling's rook
  leg. A test asserts XY never moves below carry height in any scenario.
- **The claw never actuated** (`motion/planner.py`): bare `M3` is spindle-on at
  speed 0. The claw is an `rc_servo` on the A axis; it is now `G0 A0` / `G0 A45`,
  which also queues in move order with XY/Z for free.
- **No preamble, no homing** (`orchestrator/`): FluidNC boots into Alarm and
  rejects every line with `error:9` until `$H`. Added `MotionPlanner.startup()`
  (G21/G90/G94, open claw, retract Z) and `SerialLink.home()`, both wired into
  the game loop; `main.py --no-home` skips homing for bench work.
- **Errors were streamed straight past** (`orchestrator/serial_link.py`): an
  `error:` reply was logged and the rest of the move sent anyway, so a wedged
  machine produced a clean-looking log. A real port is now strict and raises
  `SerialError`; dry-run and `strict=False` stay tolerant.
- **Promotion handed Black a white queen** (`motion/planner.py`): the reserve was
  a single point regardless of side. Colour is now derived from the destination
  rank. Underpromotion still places a queen but emits a `WARNING` comment.
- **`idle_timeout` 30s -> 180s**: a capture is ~1.5m of travel, so the old
  timeout expired mid-move — and because it was non-fatal, the orchestrator
  returned to LISTEN while the gantry was still moving.

### Added
- **`motion/config.py`** — every physical constant in one file, with an import-time
  self-check that the build has enough Z travel to lift a piece over the king and
  that no off-board zone falls outside the envelope. Four `[MEASURE]` values are
  all that should change during calibration.
- **`fluidnc/config.yaml`** — pin map (ganged X across gpio.12/16, Y, Z with its
  limit switch, A as `rc_servo` on gpio.19), steps/mm, travel, homing. Generated
  from the team's pin table so the controller and the planner share one set of
  numbers. `[BLOCKED]`/`[VERIFY]` markers flag what needs hardware sign-off.
- **`tools/gcode_preview.py`** — dry-run a planned move: per-line X/Y/Z/A trace,
  travel distance, duration estimate, and a hard fail on any position outside the
  envelope or below the board surface. `--all` covers every move type.
- **`tests/test_serial_link.py`** — streaming, comment stripping, error/alarm
  handling and homing against a fake port.
- **Graveyard grid** — captured pieces land in a 4x8 grid of 32 slots instead of
  piling up on one point.

### Changed
- **BREAKING — `plan()` and `_classify_move()` signatures**: `plan()` takes a
  fourth argument `high_lift`; `_classify_move()` returns a 3-tuple
  `(move_type, is_capture, high_lift)`. The knight test lives in the orchestrator
  so Role 3 still imports no chess library.
- **Board geometry**: 57.0 x 57.375mm squares derived from the measured 456 x
  459mm spans, replacing the placeholder 50mm grid. Coordinates are square
  CENTERS (`a1 = 28.50, 28.69`). Rapids no longer carry a meaningless `F` word.

## [0.3.0] - 2026-07-05

### Added
- **Three-way classification: legal / illegal / unrecognized** (`engine.py`, `phonetics.py`): utterances are now matched in ONE pass against a precomputed superset of every syntactically possible move (`phonetics.all_possible_moves()`, all 64×63 square pairs; cached once per process via `phonetics.superset_index()`), with the current legal moves layered on top with their full piece-aware phrases. A confident match that is not in `legal_moves` is reported as **illegal with the move identified** (e.g. "e2 to e5", or the unavailable castle side) instead of being indistinguishable from noise. Superset entries carry lean fully-qualified phrases only (destination-only forms like "pawn to e4" would be shared by all 63 moves ending on a square and tie everything; full sets would be ~8M strings). Scoring moved to `rapidfuzz.process.extract` (C-level) to absorb the ~740k-phrase pool (~140ms per utterance on a dev box). Known limitation: promotion suffixes are not in the superset — illegal promotion utterances classify as unrecognized.
- **Piece-destination pseudo-moves** (`phonetics.py`, `engine.py`): the superset also carries one candidate per (piece, destination) pair (key `"knight@c3"`, ~8k extra phrases) so utterances that name a piece and destination but no origin — the natural way to speak a move — classify as **illegal with speakable feedback** ("That move, knight to c3, isn't legal right now") instead of "unrecognized". Pairs matching a current legal move are skipped during scoring so they can never shadow or tie the legal match; `describe_move` renders the pseudo key like any UCI.
- **Castle-phrase variants + targeted ambiguity hint** (`phonetics.py`, `engine.py`): "king side castle", "king castle", "castle kingside", "castle short", bare "castle" (and queenside mirrors). Bare "castle" resolves to the only legal castle, or — when both are legal — is rejected as ambiguous and the engine speaks "Both castles are possible. Say kingside or queenside." via `on_prompt` (`is_ambiguous_castle` / `CASTLE_AMBIGUITY_HINT`).

### Fixed
- **a-file moves hijacked by the h-file** (`phonetics.py`): removed `"eight"` as an h-file homophone. With it in the Vosk grammar, a spoken "a" was transcribed as "eight" ("pawn to a3" → "pawn two eight three"), which then matched h3's phrase "pawn to eight three" almost exactly and silently played `h2h3`. "eight" remains a rank-8 word only; "aitch"/"hotel" still cover h-file mishearings, and "eight" now leaves the grammar entirely whenever no rank-8 move is legal, letting Vosk resolve a-vs-h from the audio.
- **Exact-margin matches rejected by float error** (`engine.py`): a best-vs-runner-up gap of exactly `AMBIGUITY_MARGIN` (e.g. scores 0.95 vs 0.90 on a 20-char transcript — precisely the "knight two see three" case) computed as 0.04999… and was rejected as ambiguous; an epsilon in `_rank_and_guard` now lets it through.

### Changed
- **BREAKING — return contract**: `listen_for_move(...)` and `match_text(...)` now return a `MatchResult` dataclass (`status: "legal" | "illegal" | "unrecognized"`, `move: Optional[str]`) instead of a bare string. All consumers updated (orchestrator branches per status and announces illegal moves by name; `MockVoice` and `TextVoice` implement the same contract). Illegal classifications return immediately without the yes/no confirmation round — announcing them is the Hub's job.

## [0.2.0] - 2026-07-05

### Added
- **Spoken Confirmation Layer** (`engine.py`): After a move is recognized, the engine reads it back ("I understood 'e2 to e4'. Say 'yes' to confirm or 'no' to try again.") and listens for a spoken yes/no with a grammar restricted to confirmation vocabulary. 'no' discards the move and re-listens (up to `MAX_MOVE_ATTEMPTS`); unclear answers fail safe to `"unrecognized"`. Controlled by `config.REQUIRE_CONFIRMATION` (default `True`) or the `require_confirmation` parameter; prompts route through the new `on_prompt` callback (default `print`) so the Hub can attach TTS.
- **Move read-back helper** (`phonetics.describe_move`): Renders UCI moves as natural phrases (castling and promotion aware) for the confirmation prompt.
- **Confirmation vocabulary** (`phonetics.YES_WORDS` / `NO_WORDS`).

### Changed
- **Stricter matching** (`engine.py`, `config.py`):
  - `CONFIDENCE_THRESHOLD` raised from `0.85` to `0.90`.
  - New `AMBIGUITY_MARGIN` (`0.05`): the best move must beat the runner-up move by this margin — near-ties are now rejected, not just exact ties.
  - New `REJECT_PARTIAL_UNK` (`True`): transcripts containing *any* `[unk]` token are rejected, not only pure-`[unk]` transcripts.
- **Refactor** (`engine.py`): audio capture extracted into `_transcribe()` (reused for move and confirmation capture); matching extracted into `_match_move()`; listen timeouts moved to `config.LISTEN_TIMEOUT_SECONDS` / `CONFIRMATION_TIMEOUT_SECONDS`.

## [0.1.0] - 2026-06-27

### Added
- **Configuration** (`config.py`): Introduced default hardware requirements (16000Hz mono int16 PCM) and confidence threshold defaults (`0.85`).
- **Phonetics Layer** (`phonetics.py`):
  - Created `generate_phrases` which expands UCI moves to natural spacing, letters, and numbers.
  - Implemented the strict castling phrase rules, mapping kingside and queenside castling moves to descriptive text, blocking standard square-to-square translations.
  - Added support for 5-character promotion moves by mapping trailing characters to chess piece names.
  - Created `flatten_vocabulary` to output deduplicated word lists.
- **Engine Layer** (`engine.py`):
  - Created `VoiceMatchEngine` with standard offline Vosk Model loading inside `__init__` to avoid turn latency spikes.
  - Implemented `listen_for_move` containing the thread-safe `sounddevice.RawInputStream` loop, dynamic Vosk recognizer grammar setup, and `try...finally` resource safety blocks.
  - Added matching evaluation using `rapidfuzz.distance.Levenshtein.normalized_similarity` with confidence checks, noise bypass guards, and exact tie-breaker logic.
- **Documentation** (`agents.md`): Initialized high-density system context files for AI developers.
