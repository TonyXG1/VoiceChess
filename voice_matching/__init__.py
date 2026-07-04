"""VoiceChess Role 1 - offline speech-to-text + phonetic move matching.

Public API:
    from voice_matching import VoiceMatchEngine   # live mic + Vosk (needs hardware)
    from voice_matching import match_text         # pure text -> UCI matcher (no hardware)
"""

from .engine import VoiceMatchEngine, match_text

__all__ = ["VoiceMatchEngine", "match_text"]
