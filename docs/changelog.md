# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Phonetic matching** (`voice_matching/phonetics.py`, `chess_ai/engine.py`,
  `orchestrator/state_machine.py`): `generate_phrases` prefixed every move with
  EVERY piece word, so "knight to g3" matched the pawn move g2g3 at 0.95 and the
  robot played a move the player never said. The engine now exposes
  `legal_moves_pieces()` (UCI -> spoken piece word) and the orchestrator passes
  it through `listen_for_move`/`match_text`, so piece-name phrases are generated
  only for the piece that actually moves. The `pieces` argument is optional
  everywhere (omitted = old behavior). Bonus: `e1g1` by a rook/queen is no
  longer treated as castling when the piece is known.
- **Voice capture** (`voice_matching/engine.py`): the listen loop ended on the
  FIRST Vosk endpoint, which fires on leading silence too — so every turn after
  a TTS announcement failed instantly with "I didn't catch that" before the
  player could speak. Empty endpoints are now ignored; only an endpoint with
  actual words ends the listen. The hard 5s window became a 30s wait-for-speech
  window (`config.LISTEN_TIMEOUT`) with a 10s utterance grace period
  (`config.UTTERANCE_TIMEOUT`), and the engine prints `[VOICE] listening...` /
  `[VOICE] heard: ...` so the LISTEN state is visible in the console.
  `match_text()` and all phonetic matching logic are unchanged.
- **Speech** (`chess_ai/speech.py`): `Pyttsx3Speaker` went silent after its first
  sentence (pyttsx3 issue #193 — stale event loop after `runAndWait()`); it now
  builds a fresh engine per utterance. All real speakers also print `[SPEAK] ...`
  so the dialogue is visible in the console/SSH even when audio plays.
- **Entry point** (`main.py`): `find_stockfish()` also checks the winget shim dir
  (`%LOCALAPPDATA%\Microsoft\WinGet\Links`), fixing detection in terminals opened
  before `winget install` ran.

### Added
- **Speech** (`chess_ai/speech.py`): `EspeakSpeaker` — calls the espeak-ng binary
  directly, the most reliable TTS path on the Pi. `--tts espeak` now uses it when
  the binary exists and falls back to `Pyttsx3Speaker` (Windows dev boxes).
- **Docs**: `docs/pi-setup.md` — full Raspberry Pi 5 (Pi OS Lite 64-bit) install
  runbook: apt packages, repo copy, venv, ALSA mic/speaker discovery, test ladder.

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
