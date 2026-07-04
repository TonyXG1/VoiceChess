"""VoiceChess Role 2 - Chess Engine, AI Opponent & Audio Output.

Public API:
    from chess_ai import ChessEngine, GameStatus, MoveResult
    from chess_ai import get_speaker            # TTS factory
    from chess_ai import MockVoice              # Role 1 stand-in for testing
"""

from .engine import ChessEngine, GameStatus, MoveResult
from .speech import get_speaker, Speaker, PrintSpeaker, Pyttsx3Speaker, PiperSpeaker
from .mock_voice import MockVoice

__all__ = [
    "ChessEngine", "GameStatus", "MoveResult",
    "get_speaker", "Speaker", "PrintSpeaker", "Pyttsx3Speaker", "PiperSpeaker",
    "MockVoice",
]
