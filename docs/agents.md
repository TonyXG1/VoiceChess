# System Context: Role 1 — Voice & Phonetic Matching

This document provides system-level context and architectural constraints for future AI agentic sessions (e.g., Claude Code, Cursor, LLMs) interacting with this codebase.

## Project Overview
The `voice_matching` module serves as **Role 1 — Voice & Phonetic Matching** for an offline, voice-controlled chess robot running on a Raspberry Pi 5. Its objective is to capture audio containing the player's spoken move and match it against the current list of legally playable chess moves.

## Hub Architecture Constraints
The robot implements a strict central Hub Orchestrator. 
- **Single-Process Execution**: The central Orchestrator imports this module and invokes it synchronously during the player's turn.
- **No Direct Inter-Role Communication**: Role 1 is isolated from other system roles (e.g., Robot arm motor control, game-state engines, chess UI).
- **Execution Lifecycle**:
  1. The Orchestrator queries the game engine for valid UCI moves (and the piece map).
  2. The Orchestrator invokes this module's entry point: `listen_for_move(legal_moves, pieces, on_prompt) -> MatchResult`.
  3. This module manages mic initialization, dynamic vocabulary configuration, recording, and phonetic matching/classification.
  4. This module returns a `MatchResult` classifying the utterance as `"legal"`, `"illegal"`, or `"unrecognized"`.

## Module Interface Specifications

### 1. Inputs
- `legal_moves: List[str]`: A list of valid move strings in Universal Chess Interface (UCI) format (e.g., `["e2e4", "g1f3", "e1g1", "e7e8q"]`).
- `pieces: Optional[Dict[str, str]]`: Map of each legal UCI move to the spoken word of the piece that moves (`{"e2e4": "pawn", ...}`); restricts piece-name phrases so "knight to g3" can never match a pawn move. `None` = accept any piece word.
- `require_confirmation: Optional[bool]`: Per-call override of `config.REQUIRE_CONFIRMATION` (default `True`).
- `on_prompt: Optional[Callable[[str], None]]`: Sink for read-back/prompt text (defaults to `print`); the Hub may pass a TTS callable.

### 2. Boundaries & Scope
- **Offline operation**: Must only use local offline Vosk models to avoid runtime internet dependency or latencies.
- **Dynamic acoustic graphing**: Restricts Vosk's recognition vocabulary at execution time using only the unique words generated from the current legal moves list to avoid out-of-context misrecognition.
- **No hardware state retention**: Microphones and recording streams are opened only when `listen_for_move` is called, and are strictly closed/released via standard resource management upon method exit.

### 3. Outputs — `MatchResult`
Both `listen_for_move(...)` and the pure-text `match_text(...)` return a
`MatchResult` dataclass: `status: "legal" | "illegal" | "unrecognized"`,
`move: Optional[str]`.

- `status="legal"`, `move=<uci>` — confidently matches a move in `legal_moves`
  (confirmed by the player when confirmation is enabled).
- `status="illegal"`, `move=<uci | piece@square>` — confidently matches a
  well-formed move that is NOT legal right now (e.g. `"e2e5"` from the start
  position, or the unavailable castle side). Matching runs over a precomputed
  superset of every from→to square pair (`phonetics.all_possible_moves()`,
  cached once at module scope), so the move is *identified*, not just flagged.
  Utterances naming only a piece and destination ("knight to c3" with no legal
  knight move to c3) resolve to a piece-destination pseudo-move key
  (`"knight@c3"`); `describe_move` renders it ("knight to c3") like any UCI,
  and the Hub never applies illegal moves, so consumers need no other change.
  Known limitation: promotion suffixes are not in the superset — an illegal
  promotion utterance comes back `"unrecognized"`. No yes/no confirmation is
  run for illegal moves; the Hub announces them.
- `status="unrecognized"`, `move=None` — no confident, unambiguous match:
  - The decoded transcript is empty or consists of whitespace only.
  - The transcript contains any `[unk]` token (`config.REJECT_PARTIAL_UNK`, default `True`).
  - The highest phonetic similarity score fails to reach `config.CONFIDENCE_THRESHOLD` (default: `0.90`).
  - The best move fails to beat the runner-up move by `config.AMBIGUITY_MARGIN` (default: `0.05`) — near-ties count as ambiguous, not just exact ties.
  - The player rejects the read-back move on every attempt (`config.MAX_MOVE_ATTEMPTS`, default `2`), or gives no clear yes/no answer after `config.MAX_CONFIRMATION_ATTEMPTS` prompts (default `2`).

### 4. Confirmation Layer Flow
When `config.REQUIRE_CONFIRMATION` is `True` (default), a matched move triggers a read-back via `on_prompt` ("I understood 'e2 to e4'. Say 'yes' to confirm or 'no' to try again."), then a second Vosk capture restricted to yes/no vocabulary (`phonetics.YES_WORDS` / `phonetics.NO_WORDS`, `config.CONFIRMATION_TIMEOUT_SECONDS`). 'yes' returns the move; 'no' discards it and re-listens; unclear answers fail safe to `"unrecognized"`. An unconfirmed move is never returned.

### 5. Internal Structure (engine.py)
- `listen_for_move()`: orchestrates the attempt/confirmation loop; precomputes phrase cache + grammar.
- `_transcribe(grammar_words, timeout_seconds)`: one mic capture with a dynamic Vosk grammar; returns lowercase transcript.
- `_classify(text, legal_moves, phrases_cache, pieces)`: ONE scoring pass over the static move superset (lean fully-qualified phrases + piece-destination pseudo-moves) plus the per-turn piece-aware legal overlay; superset entries duplicated by the overlay are skipped (the legal UCIs and every legal `piece@destination` pair), then the unk / confidence / ambiguity-margin guards apply, color-twin castle candidates merge, and the winner splits by legality into legal/illegal.
- `_match_move(text, phrases_cache)`: cache-level strict matcher (same guards) kept for targeted tests.
- `_confirm_move(move, prompt)` + `_parse_confirmation(text)`: read-back prompt and yes/no classification.

Note: the Vosk grammar is still built from the LEGAL moves' vocabulary only —
the audio-capture layer is unchanged; the superset exists purely in the
matching/classification step after transcription.

## Key Code Components
- `voice_matching/config.py`: Hardware and confidence configurations.
- `voice_matching/phonetics.py`: Rule-based phonetic generator and flattener.
- `voice_matching/engine.py`: Main `VoiceMatchEngine` class implementing Vosk and RapidFuzz matching.
