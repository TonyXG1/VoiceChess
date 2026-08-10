# Role 2 - Interface Contract

This is the surface other roles build against. If you are Role 1, 3, 4 or 5, you
only need what's on this page. Everything is plain Python and in-process (except
where noted). Every move is a **UCI string** (`"e2e4"`, `"e7e8q"`, `"e1g1"`).

## Construct it

```python
from chess_ai import ChessEngine
eng = ChessEngine(skill_level=5, think_time=0.5)   # human = White by default
```

`skill_level` 0-20 (lower = weaker/faster), `think_time` seconds per AI move.
Stockfish is auto-detected on PATH / `/usr/games/stockfish`; override with
`ChessEngine(stockfish_path="/path/to/stockfish")`.

## Methods

| Call | Returns | Notes |
|------|---------|-------|
| `legal_moves()` | `list[str]` UCI | **The list you give Role 1 every turn.** Recompute after each move. |
| `legal_moves_san()` | `list[{"uci","san"}]` | Same list with human-readable SAN. |
| `is_legal(uci)` | `bool` | Never raises. |
| `apply(uci)` | `MoveResult` | Validates + applies. Used for BOTH human and AI moves. |
| `ai_move()` | `str` UCI | Stockfish's reply. **Does not apply** - call `apply()` on it. |
| `status()` | `GameStatus` | check / checkmate / stalemate / draw / result. |
| `describe(uci)` | `str` | Spoken read-back for a move, without applying it. |
| `speak(text)` | `None` | Speaks via the attached Speaker (see TTS). |
| `fen()` / `set_fen(f)` | `str` / `None` | Portable board snapshot. |
| `reset()` / `undo()` | `None` / `str?` | New game / take back last move. |
| `close()` | `None` | Shut down Stockfish. Or use `with ChessEngine() as eng:`. |

### `MoveResult`
`ok` (bool), `uci`, `san`, `readback` (e.g. `"Pawn to e4, check"`),
`error` (why it was rejected, if `ok` is False), `status` (a `GameStatus`).

If `ok` is False the board is **unchanged** - this is the guard that stops an
impossible move ever reaching the robot arm.

### `GameStatus`
`turn` (`"white"`/`"black"`), `in_check`, `is_checkmate`, `is_stalemate`,
`is_draw`, `is_game_over`, `result` (`"1-0"`/`"0-1"`/`"1/2-1/2"`/`None`),
`reason` (`"checkmate"`, `"stalemate"`, `"insufficient_material"`, ...),
`fullmove_number`.

## For Role 1 (Voice)

Your `listen_for_move(legal_moves, pieces)` receives exactly what
`eng.legal_moves()` returns plus `eng.legal_moves_pieces()` — a map of each
UCI move to the spoken word of the piece that moves (`{"e2e4": "pawn",
"g1f3": "knight", ...}`) — and returns a **`MatchResult`**
(`from voice_matching import MatchResult`):

- `status="legal"`, `move=<uci from legal_moves>` — play it.
- `status="illegal"`, `move=<uci>` — a well-formed move that is NOT legal
  right now (matched against Role 1's full move superset); the orchestrator
  announces it by name and re-listens.
- `status="unrecognized"`, `move=None` — noise/silence/ambiguity; generic
  retry.

The piece map restricts phrase generation so an utterance naming one piece
can never match a different piece's move ("knight to g3" must not match the
pawn move g2g3). `pieces` is optional (`None` = accept any piece word).
`MockVoice` in this package implements the identical signature and return
contract for testing without a mic.

## For Role 3 (Motion) and Role 4 (Hardware)

Role 2 hands you a **validated UCI move** to translate into geometry. To know
if a move is a capture / castle / promotion (your special cases), you can ask:

```python
res = eng.apply("e2e4")
res.san           # "exd5", "O-O", "e8=Q" etc. carry the flags in notation
```

or read them from the board before applying via python-chess if you prefer.
Role 2 never emits G-code and never touches the serial port - that's your lane.

### Motion contract (Role 3)

```python
planner = MotionPlanner()
planner.reset()                      # per game: rewinds graveyard/queen slots
planner.startup()  -> str            # one-time G21/G90/G94 + open claw + retract Z
planner.plan(uci_move,               # "e2e4" (a promotion suffix is tolerated)
             move_type="standard",   # | "castling" | "en_passant" | "promotion"
             is_capture=False,
             high_lift=False) -> str # ONE newline-joined G-code string
```

The orchestrator derives all three flags from Role 2's board **before** applying
the move (`_classify_move` -> `(move_type, is_capture, high_lift)`), then
`splitlines()` the result for the serial link. `high_lift` is True only for
knights - see CLAUDE.md's lift policy. The planner imports no chess library and
never talks to the serial port.

Every physical constant lives in `motion/config.py`, and only the four
`[MEASURE]` values there should change during calibration. `plan()` raises
`ValueError` for an off-board square or a position outside the machine envelope,
so nothing invalid can reach the motors.

### Serial contract (Role 4 boundary)

```python
link = SerialLink(port=None, baud=115200, strict=None)
link.home()                          # $H, then blocks until <Idle>
link.send(gcode.splitlines())        # blocks until motion completes
link.close()
```

`port=None` is dry-run (prints `[SERIAL-STUB]`, opens nothing, tolerant).
A real port is **strict**: an `error:` reply raises `SerialError` rather than
streaming the rest of a move into a controller that already rejected a line.
Pass `strict=False` for pre-flash bench work.

Protocol notes that matter: `ok` means *queued*, not executed - `send()` polls
`?` until `Idle` before returning, which is what keeps the game loop out of
LISTEN while the gantry is moving. FluidNC boots into **Alarm** with homing
enabled and answers every line `error:9` until `$H` runs. Comments are stripped
before they reach the wire (they still appear in the local trace).

## For Role 5 (Orchestrator)

You own the turn loop and the *timing* of every call above. `orchestrator/state_machine.py`
(run via `main.py`) is the runnable turn loop (LISTEN -> CONFIRM -> MOVE -> THINK -> MOVE).
Role 2 exposes capabilities; it never decides when they run.

## TTS (audio output)

```python
from chess_ai import get_speaker
eng.speaker = get_speaker("print")     # default: prints "[SPEAK] ..."
eng.speaker = get_speaker("pyttsx3")   # later: real offline speech
eng.speak("You said pawn to e4")
```

Swapping backends is one line; no other code changes.

## Open decisions (need a call from the team)

1. **Promotion** - kept and fully working here (`e7e8q` -> "promote to queen").
   If Role 3/4 want it cut for the demo, say so and I'll gate it. The plan doc
   flags this as undecided.
2. **Human color** - currently human = White. If the human should be able to
   play Black, that's a one-line construction flag; tell me the desired default.
3. **AI strength** - `skill_level=5`, `think_time=0.5s`. Tune for the demo.
