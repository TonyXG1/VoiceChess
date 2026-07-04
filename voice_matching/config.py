"""Configuration definitions for VoiceChess Voice Matching (Role 1).

Specifies hardware constants, thresholds, and paths.
"""

import os
from typing import Optional


# Hardware Constraints
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
# sounddevice/vosk standard format is signed int16 (16-bit mono PCM)
DTYPE: str = "int16"

# Thresholds
CONFIDENCE_THRESHOLD: float = 0.85

# Listening window (seconds).
# LISTEN_TIMEOUT: how long to wait for the player to START speaking before
# returning "unrecognized" (the orchestrator re-prompts, so this is just a
# liveness nag, not a failure). Silence never aborts a turn early.
LISTEN_TIMEOUT: float = 30.0
# UTTERANCE_TIMEOUT: once speech is detected near the deadline, how much extra
# time the utterance gets to finish instead of being cut off mid-word.
UTTERANCE_TIMEOUT: float = 10.0

# Vosk Model Configuration
# Defaults to a local directory named 'model' or uses VOSK_MODEL_PATH from environment.
MODEL_PATH: str = os.getenv("VOSK_MODEL_PATH", os.path.join(os.path.dirname(__file__), "model"))

# Audio Input Device Index
# If None, the system default input device is used. 
# Set to an integer to select a specific input device index.
DEVICE_INDEX: Optional[int] = None

