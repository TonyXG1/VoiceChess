import pytest
from unittest.mock import MagicMock, patch
import json

from voice_matching import phonetics
from voice_matching import config
from voice_matching.engine import (MatchResult, VoiceMatchEngine,
                                   is_ambiguous_castle, match_text)

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

def test_describe_move():
    assert phonetics.describe_move("e2e4") == "e2 to e4"
    assert phonetics.describe_move("e1g1") == "kingside castle"
    assert phonetics.describe_move("e8c8") == "queenside castle"
    assert phonetics.describe_move("e7e8q") == "e7 to e8 promoting to queen"
    assert phonetics.describe_move("knight@c3") == "knight to c3"

# --- Move superset (illegal-vs-unrecognized foundation) ---

def test_all_possible_moves():
    moves = phonetics.all_possible_moves()
    assert len(moves) == 64 * 63          # every from != to square pair
    assert "e2e5" in moves                # illegal shapes are representable
    assert "e1g1" in moves and "e8c8" in moves
    assert "e2e2" not in moves
    assert all(len(m) == 4 for m in moves)  # promotion suffixes: known limitation

def test_superset_index_is_cached_and_parallel():
    phrases, moves = phonetics.superset_index()
    assert len(phrases) == len(moves)
    assert phonetics.superset_index() is not None
    # Cached: the exact same objects come back, no rebuild
    again_phrases, again_moves = phonetics.superset_index()
    assert again_phrases is phrases and again_moves is moves

# --- match_text: three-way classification (pure text path) ---

# Start-position legal moves (subset is enough) with their piece map.
_START_LEGAL = ["e2e4", "e2e3", "d2d4", "g1f3", "b1c3"]
_START_PIECES = {"e2e4": "pawn", "e2e3": "pawn", "d2d4": "pawn",
                 "g1f3": "knight", "b1c3": "knight"}

def test_match_text_legal():
    res = match_text("e two e four", _START_LEGAL, _START_PIECES)
    assert res == MatchResult("legal", "e2e4")
    res = match_text("pawn to e4", _START_LEGAL, _START_PIECES)
    assert res == MatchResult("legal", "e2e4")

def test_match_text_illegal_identifies_the_move():
    # Pawn can't jump to e5 from e2 -- well-formed but not legal right now.
    res = match_text("e two to e five", _START_LEGAL, _START_PIECES)
    assert res.status == "illegal"
    assert res.move == "e2e5"

def test_match_text_illegal_impossible_double_move():
    # Double pawn move from a non-starting rank: e3 -> e5.
    res = match_text("e three to e five", _START_LEGAL, _START_PIECES)
    assert res.status == "illegal"
    assert res.move == "e3e5"

def test_match_text_unrecognized_noise():
    assert match_text("hello world", _START_LEGAL, _START_PIECES) == \
        MatchResult("unrecognized")
    assert match_text("", _START_LEGAL, _START_PIECES) == \
        MatchResult("unrecognized")
    assert match_text("e two [unk] four", _START_LEGAL, _START_PIECES) == \
        MatchResult("unrecognized")

def test_match_text_empty_legal_moves():
    assert match_text("e two e four", []).status == "unrecognized"

def test_match_text_piece_aware():
    # With the pieces map, naming the wrong piece must not match the legal
    # move -- it resolves to the piece-destination pseudo-move as illegal.
    pieces = {"g2g3": "pawn"}
    assert match_text("pawn to g3", ["g2g3"], pieces) == MatchResult("legal", "g2g3")
    res = match_text("knight to g3", ["g2g3"], pieces)
    assert res.status == "illegal"
    assert res.move == "knight@g3"


def test_match_text_piece_dest_illegal():
    # Piece + destination with no origin square ("knight to c3" when no
    # knight can reach c3) must classify as ILLEGAL with a speakable move,
    # not "unrecognized". (b1c3 deliberately absent from the legal list.)
    legal = ["e2e4", "d2d4", "g1f3"]
    pieces = {"e2e4": "pawn", "d2d4": "pawn", "g1f3": "knight"}
    res = match_text("knight to see three", legal, pieces)
    assert res.status == "illegal"
    assert res.move == "knight@c3"
    assert phonetics.describe_move(res.move) == "knight to c3"


def test_match_text_piece_dest_legal_not_shadowed():
    # Regression: the superset move h2c3 renders as "eight two see three" if
    # "eight" is (wrongly) an h-file homophone -- one edit from "knight two
    # see three" -- and its 0.90 score sat exactly at the ambiguity margin
    # below the legal 0.95, where float error rejected the match.
    res = match_text("knight two see three", _START_LEGAL, _START_PIECES)
    assert res == MatchResult("legal", "b1c3")


def test_a_file_not_hijacked_by_eight():
    # Regression: "pawn to a3" was transcribed as "pawn two eight three" and
    # matched h2h3 EXACTLY via the "eight" h-file homophone, silently playing
    # the wrong file. It must never resolve to an h-file move again.
    legal = ["a2a3", "a2a4", "h2h3", "h2h4", "e2e4"]
    pieces = {m: "pawn" for m in legal}
    res = match_text("pawn two eight three", legal, pieces)
    assert res.move not in ("h2h3", "h2h4")
    # The clean transcripts resolve to the right file in both directions.
    assert match_text("pawn to a three", legal, pieces) == MatchResult("legal", "a2a3")
    assert match_text("pawn to h three", legal, pieces) == MatchResult("legal", "h2h3")

# --- Castling phrases ---

# The legal moves + pieces map from a real game position where only the
# kingside castle was available (and 'king castle' failed to match at 0.90).
_CASTLE_LEGAL = ["e1g1", "e1e2", "f1e2", "g1h1", "c2c3", "d2d4"]
_CASTLE_PIECES = {"e1g1": "king", "e1e2": "king", "f1e2": "bishop",
                  "g1h1": "rook", "c2c3": "pawn", "d2d4": "pawn"}

def test_castle_phrase_variants_match():
    for utterance in ("kingside castle", "king side castle", "king castle",
                      "short castle", "castle short"):
        assert match_text(utterance, _CASTLE_LEGAL, _CASTLE_PIECES) == \
            MatchResult("legal", "e1g1"), utterance

def test_bare_castle_matches_the_only_legal_castle():
    assert match_text("castle", _CASTLE_LEGAL, _CASTLE_PIECES) == \
        MatchResult("legal", "e1g1")

def test_unavailable_castle_side_is_illegal_not_noise():
    # Only kingside is legal; asking for queenside is a clear, nameable miss.
    for utterance in ("queenside castle", "long castle", "queen side castle"):
        res = match_text(utterance, _CASTLE_LEGAL, _CASTLE_PIECES)
        assert res.status == "illegal", utterance
        assert res.move == "e1c1", utterance

def test_bare_castle_is_ambiguous_when_both_castles_are_legal():
    legal = _CASTLE_LEGAL + ["e1c1"]
    pieces = dict(_CASTLE_PIECES, e1c1="king")
    # Both castles tie -> the player must specify the side. (Regression: the
    # tie rule, not a random pick, decides this.)
    assert match_text("castle", legal, pieces).status == "unrecognized"
    # A specific side still resolves cleanly.
    assert match_text("kingside castle", legal, pieces) == MatchResult("legal", "e1g1")
    assert match_text("queen side castle", legal, pieces) == MatchResult("legal", "e1c1")
    assert match_text("long castle", legal, pieces) == MatchResult("legal", "e1c1")

def test_is_ambiguous_castle_helper():
    legal = _CASTLE_LEGAL + ["e1c1"]
    pieces = dict(_CASTLE_PIECES, e1c1="king")
    assert is_ambiguous_castle("castle", legal, pieces)
    assert is_ambiguous_castle("castle [unk]", legal, pieces)
    # Only one castle legal -> not ambiguous
    assert not is_ambiguous_castle("castle", _CASTLE_LEGAL, _CASTLE_PIECES)
    # Utterance isn't about castling at all
    assert not is_ambiguous_castle("pawn to d4", legal, pieces)
    # e1g1/e1c1 by a non-king piece are ordinary moves, not castling
    rook_slide = {"e1g1": "rook", "e1c1": "rook"}
    assert not is_ambiguous_castle("castle", ["e1g1", "e1c1"], rook_slide)

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
    assert res.status == "unrecognized"
    assert res.move is None
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

        # Test recognition (confirmation disabled explicitly)
        res = engine.listen_for_move(["e2e4", "g1f3"], require_confirmation=False)
        assert res == MatchResult("legal", "e2e4")

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
        res = engine.listen_for_move(["e2e4"], require_confirmation=False)
        assert res.status == "unrecognized"

@patch("vosk.Model")
def test_listen_for_move_illegal_returns_without_confirmation(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    prompts = []
    # A single transcribe call: the illegal classification must return
    # immediately -- no yes/no round, no second capture.
    with patch.object(engine, "_transcribe",
                      side_effect=["e two to e five"]) as mock_transcribe:
        res = engine.listen_for_move(_START_LEGAL, pieces=_START_PIECES,
                                     on_prompt=prompts.append)
    assert res.status == "illegal"
    assert res.move == "e2e5"
    assert mock_transcribe.call_count == 1
    assert prompts == []                   # no confirmation prompt was spoken

# --- Strictness guards (_match_move) ---

@patch("vosk.Model")
def test_match_move_rejects_partial_unk(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    cache = {"e2e4": phonetics.generate_phrases("e2e4")}
    # A transcript containing any [unk] token is rejected outright
    assert engine._match_move("e two [unk] four", cache) == "unrecognized"
    assert engine._match_move("[unk]", cache) == "unrecognized"

@patch("vosk.Model")
def test_match_move_rejects_near_tie(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    # Two moves whose best phrases differ by a single character over 25:
    # scores 1.0 vs 0.96 -> gap 0.04 < AMBIGUITY_MARGIN (0.05) -> ambiguous
    cache = {
        "m1": ["a" * 25],
        "m2": ["a" * 24 + "b"],
    }
    assert engine._match_move("a" * 25, cache) == "unrecognized"

@patch("vosk.Model")
def test_match_move_accepts_gap_exactly_at_margin(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    # Scores 1.0 vs 0.95: the gap equals AMBIGUITY_MARGIN but computes as
    # 0.04999... in floats -- the epsilon in the guard must let it through.
    cache = {
        "m1": ["a" * 20],
        "m2": ["a" * 19 + "b"],
    }
    assert engine._match_move("a" * 20, cache) == "m1"

@patch("vosk.Model")
def test_match_move_accepts_clear_winner(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    cache = {
        "e2e4": phonetics.generate_phrases("e2e4"),
        "g1f3": phonetics.generate_phrases("g1f3"),
    }
    assert engine._match_move("e two e four", cache) == "e2e4"

@patch("vosk.Model")
def test_match_move_confidence_threshold(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    cache = {"e2e4": phonetics.generate_phrases("e2e4")}
    assert engine._match_move("hello world", cache) == "unrecognized"

# --- Confirmation layer ---

@patch("vosk.Model")
def test_parse_confirmation(mock_model):
    parse = VoiceMatchEngine._parse_confirmation
    assert parse("yes") == "yes"
    assert parse("yeah") == "yes"
    assert parse("no") == "no"
    assert parse("cancel") == "no"
    assert parse("") == "unclear"
    assert parse("yes no") == "unclear"  # contradictory answer
    assert parse("banana") == "unclear"

@patch("vosk.Model")
def test_confirmation_yes_returns_move(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    prompts = []
    # First utterance: the move; second: the confirmation
    with patch.object(engine, "_transcribe", side_effect=["e two e four", "yes"]):
        res = engine.listen_for_move(["e2e4", "g1f3"], on_prompt=prompts.append)
    assert res == MatchResult("legal", "e2e4")
    assert any("e2 to e4" in p for p in prompts)

@patch("vosk.Model")
def test_confirmation_no_then_new_move(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    # Player rejects the first understanding, then speaks a different move
    with patch.object(
        engine, "_transcribe",
        side_effect=["e two e four", "no", "knight g one f three", "yes"],
    ):
        res = engine.listen_for_move(["e2e4", "g1f3"], on_prompt=lambda _: None)
    assert res == MatchResult("legal", "g1f3")

@patch("vosk.Model")
def test_confirmation_repeated_no_gives_unrecognized(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    # Rejecting on every attempt exhausts MAX_MOVE_ATTEMPTS
    side_effects = ["e two e four", "no"] * config.MAX_MOVE_ATTEMPTS
    with patch.object(engine, "_transcribe", side_effect=side_effects):
        res = engine.listen_for_move(["e2e4"], on_prompt=lambda _: None)
    assert res.status == "unrecognized"

@patch("vosk.Model")
def test_confirmation_unclear_gives_unrecognized(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    # No clear yes/no after MAX_CONFIRMATION_ATTEMPTS prompts -> fail safe
    side_effects = ["e two e four"] + [""] * config.MAX_CONFIRMATION_ATTEMPTS
    with patch.object(engine, "_transcribe", side_effect=side_effects):
        res = engine.listen_for_move(["e2e4"], on_prompt=lambda _: None)
    assert res.status == "unrecognized"

@patch("vosk.Model")
def test_confirmation_uses_restricted_yes_no_grammar(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    calls = []

    def fake_transcribe(grammar_words, timeout):
        calls.append(grammar_words)
        return "e two e four" if len(calls) == 1 else "yes"

    with patch.object(engine, "_transcribe", side_effect=fake_transcribe):
        engine.listen_for_move(["e2e4"], on_prompt=lambda _: None)

    confirmation_grammar = calls[1]
    assert "yes" in confirmation_grammar
    assert "no" in confirmation_grammar
    # Move vocabulary must not leak into the confirmation grammar
    assert "four" not in confirmation_grammar

@patch("vosk.Model")
def test_live_castle_ambiguity_hint_then_specified(mock_model):
    engine = VoiceMatchEngine(model_path="/fake/path")
    prompts = []
    legal = _CASTLE_LEGAL + ["e1c1"]
    pieces = dict(_CASTLE_PIECES, e1c1="king")
    # Bare "castle" -> hint + re-listen; then the side is specified + confirmed
    with patch.object(engine, "_transcribe",
                      side_effect=["castle", "king side castle", "yes"]):
        res = engine.listen_for_move(legal, pieces=pieces, on_prompt=prompts.append)
    assert res == MatchResult("legal", "e1g1")
    assert any("Both castles are possible" in p for p in prompts)
