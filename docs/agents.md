# System Context: Role 1 — Voice & Phonetic Matching

This document provides system-level context and architectural constraints for future AI agentic sessions (e.g., Claude Code, Cursor, LLMs) interacting with this codebase.

## Project Overview
The `voice_matching` module serves as **Role 1 — Voice & Phonetic Matching** for an offline, voice-controlled chess robot running on a Raspberry Pi 5. Its objective is to capture audio containing the player's spoken move and match it against the current list of legally playable chess moves.

## Hub Architecture Constraints
The robot implements a strict central Hub Orchestrator. 
- **Single-Process Execution**: The central Orchestrator imports this module and invokes it synchronously during the player's turn.
- **No Direct Inter-Role Communication**: Role 1 is isolated from other system roles (e.g., Robot arm motor control, game-state engines, chess UI).
- **Execution Lifecycle**:
  1. The Orchestrator queries the game engine for valid UCI moves.
  2. The Orchestrator invokes this module's entry point: `listen_for_move(legal_moves: list[str]) -> str`.
  3. This module manages mic initialization, dynamic vocabulary configuration, recording, and phonetic matching.
  4. This module returns a single UCI move string or `"unrecognized"`.

## Module Interface Specifications

### 1. Inputs
- `legal_moves: List[str]`: A list of valid move strings in Universal Chess Interface (UCI) format (e.g., `["e2e4", "g1f3", "e1g1", "e7e8q"]`).

### 2. Boundaries & Scope
- **Offline operation**: Must only use local offline Vosk models to avoid runtime internet dependency or latencies.
- **Dynamic acoustic graphing**: Restricts Vosk's recognition vocabulary at execution time using only the unique words generated from the current legal moves list to avoid out-of-context misrecognition.
- **No hardware state retention**: Microphones and recording streams are opened only when `listen_for_move` is called, and are strictly closed/released via standard resource management upon method exit.

### 3. Outputs
The return value must be one of:
- A single matching string from the `legal_moves` list (e.g., `"e2e4"`, `"e8g8"`, `"e7e8q"`).
- The exact literal string `"unrecognized"` if:
  - The decoded transcript is empty or consists of whitespace only.
  - The highest phonetic similarity score fails to reach `config.CONFIDENCE_THRESHOLD` (default: `0.85`).
  - An exact similarity score tie occurs between two or more different legal moves.

## Key Code Components
- [config.py](file:///Users/david/Desktop/Chess/config.py): Hardware and confidence configurations.
- [phonetics.py](file:///Users/david/Desktop/Chess/phonetics.py): Rule-based phonetic generator and flattener.
- [engine.py](file:///Users/david/Desktop/Chess/engine.py): Main `VoiceMatchEngine` class implementing Vosk and RapidFuzz matching.
