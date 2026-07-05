"""VoiceChess Role 1 - offline speech-to-text + phonetic move matching.

Public API:
    from voice_matching import VoiceMatchEngine   # live mic + Vosk (needs hardware)
    from voice_matching import match_text         # pure text classifier (no hardware)
    from voice_matching import MatchResult        # three-way return contract
"""

from .engine import VoiceMatchEngine, MatchResult, match_text

__all__ = ["VoiceMatchEngine", "MatchResult", "match_text"]
