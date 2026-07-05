# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
