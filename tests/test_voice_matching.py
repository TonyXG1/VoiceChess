import pytest
from unittest.mock import MagicMock, patch
import json

from voice_matching import phonetics
from voice_matching import config
from voice_matching.engine import VoiceMatchEngine

def test_square_permutations():
    # Test standard square permutations mapping
    perms = phonetics._square_permutations("e2")
    assert "e2" in perms
    assert "e 2" in perms
    # Check that phonetics homophones are included
    assert "e two" in perms
    assert "echo too" in perms

def test_generate_phrases_standard():
    phrases = phonetics.generate_phrases("e2e4")
    # Verify standard square translations exist
    assert "e2 e4" in phrases
    assert "e two to e four" in phrases
    # Verify piece prefixes exist
    assert "pawn e2 e4" in phrases
    assert "pawn to e4" in phrases

def test_generate_phrases_castling():
    # Kingside castling
    white_kingside = phonetics.generate_phrases("e1g1")
    assert "kingside castle" in white_kingside
    assert "short castle" in white_kingside
    assert "e1 g1" not in white_kingside  # standard mapping blocked

    # Queenside castling
    black_queenside = phonetics.generate_phrases("e8c8")
    assert "queenside castle" in black_queenside
    assert "long castle" in black_queenside
    assert "e8 c8" not in black_queenside

def test_generate_phrases_promotion():
    promotions = phonetics.generate_phrases("e7e8q")
    assert "e7 e8 queen" in promotions
    assert "e7 e8 promote queen" in promotions
    assert "pawn e7 to e8 promote to queen" in promotions

def test_flatten_vocabulary():
    vocab = phonetics.flatten_vocabulary(["e2e4", "e1g1"])
    # Verify castling vocabulary words are present
    assert "kingside" in vocab
    assert "castle" in vocab
    assert "short" in vocab
    # Verify square coordinates are present
    assert "e" in vocab
    assert "four" in vocab
    assert "two" in vocab

@patch("vosk.Model")
def test_engine_init(mock_model):
    # Verify model is initialized with the correct path
    mock_path = "/fake/model/path"
    engine = VoiceMatchEngine(model_path=mock_path)
    mock_model.assert_called_once_with(mock_path)

@patch("vosk.Model")
@patch("vosk.KaldiRecognizer")
@patch("sounddevice.RawInputStream")
def test_listen_for_move_unrecognized_empty_legal(mock_stream, mock_rec, mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    # No legal moves should immediately return unrecognized without capturing audio
    res = engine.listen_for_move([])
    assert res == "unrecognized"
    mock_stream.assert_not_called()

@patch("vosk.Model")
@patch("vosk.KaldiRecognizer")
@patch("sounddevice.RawInputStream")
def test_listen_for_move_matching(mock_stream, mock_rec_class, mock_model):
    # Set up KaldiRecognizer mock to return a pre-defined transcript
    mock_rec = MagicMock()
    mock_rec_class.return_value = mock_rec
    
    # Mock result JSON for "e two e four"
    mock_rec.AcceptWaveform.return_value = True
    mock_rec.Result.return_value = json.dumps({"text": "e two e four"})
    
    engine = VoiceMatchEngine(model_path="/fake/path")
    
    # Mock sounddevice queue read
    with patch("queue.Queue.get") as mock_queue_get:
        mock_queue_get.return_value = b"\x00\x00"
        
        # Test recognition
        res = engine.listen_for_move(["e2e4", "g1f3"])
        assert res == "e2e4"

@patch("vosk.Model")
@patch("vosk.KaldiRecognizer")
@patch("sounddevice.RawInputStream")
def test_listen_for_move_confidence_guard(mock_stream, mock_rec_class, mock_model):
    mock_rec = MagicMock()
    mock_rec_class.return_value = mock_rec
    
    # Low confidence result (completely unrelated text)
    mock_rec.AcceptWaveform.return_value = True
    mock_rec.Result.return_value = json.dumps({"text": "hello world"})
    
    engine = VoiceMatchEngine(model_path="/fake/path")
    
    with patch("queue.Queue.get") as mock_queue_get:
        mock_queue_get.return_value = b"\x00\x00"
        res = engine.listen_for_move(["e2e4"])
        assert res == "unrecognized"
