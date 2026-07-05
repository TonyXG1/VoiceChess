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
# NOTE: upstream Role 1 pushed 0.00 here (debug leftover) while its changelog
# says 0.90 — the changelog value is the intended "stricter" setting.
CONFIDENCE_THRESHOLD: float = 0.90
# Minimum score gap required between the best-matching move and the runner-up
# move. If two different legal moves score within this margin of each other,
# the transcript is considered ambiguous and rejected.
AMBIGUITY_MARGIN: float = 0.05
# Reject transcripts containing any '[unk]' token (out-of-vocabulary audio),
# not just transcripts that are entirely '[unk]'.
REJECT_PARTIAL_UNK: bool = True

# Listening windows (seconds).
# LISTEN_TIMEOUT_SECONDS: how long to wait for the player to START speaking
# before returning "unrecognized" (the orchestrator re-prompts, so this is
# just a liveness nag, not a failure). Silence never aborts a turn early.
LISTEN_TIMEOUT_SECONDS: float = 30.0
# UTTERANCE_TIMEOUT_SECONDS: once speech is detected near the deadline, how
# much extra time the utterance gets to finish instead of being cut off.
UTTERANCE_TIMEOUT_SECONDS: float = 10.0
# How long to wait for the yes/no answer during the confirmation layer.
CONFIRMATION_TIMEOUT_SECONDS: float = 4.0

# Confirmation Layer
# When enabled, the engine reads the understood move back to the player and
# waits for a spoken yes/no before returning it to the Hub Orchestrator.
REQUIRE_CONFIRMATION: bool = True
# Number of times the player may reject ('no') and re-speak the move within a
# single listen_for_move call before 'unrecognized' is returned.
MAX_MOVE_ATTEMPTS: int = 2
# Number of chances the player gets to give a clear yes/no answer per prompt.
MAX_CONFIRMATION_ATTEMPTS: int = 2

# Vosk Model Configuration
# Defaults to a local directory named 'model' or uses VOSK_MODEL_PATH from environment.
MODEL_PATH: str = os.getenv("VOSK_MODEL_PATH", os.path.join(os.path.dirname(__file__), "model"))

# Audio Input Device Index
# If None, the system default input device is used.
# Set to an integer to select a specific input device index.
DEVICE_INDEX: Optional[int] = None
