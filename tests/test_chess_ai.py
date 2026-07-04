"""Tests for Role 2 (ChessEngine). Run: pytest -q

The rules/read-back tests need only python-chess. The AI test needs a Stockfish
binary and is skipped automatically if none is found.
"""

import chess
import pytest

from chess_ai import ChessEngine, MockVoice


# ------------------------------- legal moves ------------------------------- #

def test_start_position_has_20_legal_moves():
    eng = ChessEngine()
    moves = eng.legal_moves()
    assert len(moves) == 20
    assert "e2e4" in moves and "g1f3" in moves
    assert all(isinstance(m, str) for m in moves)


def test_legal_moves_san_pairs_up():
    eng = ChessEngine()
    pairs = eng.legal_moves_san()
    lookup = {p["uci"]: p["san"] for p in pairs}
    assert lookup["e2e4"] == "e4"
    assert lookup["g1f3"] == "Nf3"


def test_legal_moves_recompute_after_change():
    eng = ChessEngine()
    before = set(eng.legal_moves())
    eng.apply("e2e4")
    after = set(eng.legal_moves())
    assert before != after                       # list is live, per-position
    assert "e7e5" in after                        # now Black to move


# ------------------------------- validation -------------------------------- #

def test_illegal_move_is_rejected_and_board_unchanged():
    eng = ChessEngine()
    fen_before = eng.fen()
    res = eng.apply("e2e5")                        # pawn can't jump 3
    assert res.ok is False
    assert res.error and "not legal" in res.error
    assert eng.fen() == fen_before                 # nothing happened

def test_malformed_move_is_rejected():
    eng = ChessEngine()
    res = eng.apply("banana")
    assert res.ok is False
    assert "not a valid UCI" in res.error

def test_is_legal():
    eng = ChessEngine()
    assert eng.is_legal("e2e4") is True
    assert eng.is_legal("e2e5") is False
    assert eng.is_legal("zzzz") is False


# --------------------------- apply + game state ---------------------------- #

def test_apply_updates_turn_and_san():
    eng = ChessEngine()
    res = eng.apply("e2e4")
    assert res.ok and res.san == "e4"
    assert res.status.turn == "black"

def test_checkmate_detected_fools_mate():
    eng = ChessEngine()
    for mv in ["f2f3", "e7e5", "g2g4", "d8h4"]:   # Fool's mate
        res = eng.apply(mv)
        assert res.ok
    st = eng.status()
    assert st.is_checkmate and st.is_game_over and st.result == "0-1"
    assert res.readback.endswith("checkmate")

def test_stalemate_detected():
    # Classic stalemate: Black to move, no legal move, not in check.
    eng = ChessEngine(start_fen="7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    st = eng.status()
    assert st.is_stalemate and st.is_draw and st.result == "1/2-1/2"

def test_check_is_reported_in_readback():
    # Scholar's-mate-ish check on f7.
    eng = ChessEngine()
    for mv in ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6"]:
        eng.apply(mv)
    res = eng.apply("h5f7")                        # Qxf7 is checkmate here
    assert res.ok
    assert "takes f7" in res.readback


# ------------------------------ read-back ---------------------------------- #

def test_readback_capture_and_castle_and_promo():
    eng = ChessEngine()
    assert eng.describe("e2e4") == "Pawn to e4"
    assert eng.describe("g1f3") == "Knight to f3"
    # castling read-back
    eng2 = ChessEngine(start_fen="r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert eng2.describe("e1g1") == "Kingside castle"
    assert eng2.describe("e1c1") == "Queenside castle"
    # promotion read-back
    eng3 = ChessEngine(start_fen="8/P7/8/8/8/8/8/k6K w - - 0 1")
    assert eng3.describe("a7a8q") == "Pawn to a8, promote to queen"


def test_undo():
    eng = ChessEngine()
    eng.apply("e2e4")
    assert eng.undo() == "e2e4"
    assert eng.undo() is None                       # nothing left


# --------------------------------- AI -------------------------------------- #

@pytest.mark.skipif(ChessEngine()._find_stockfish() is None,
                    reason="no Stockfish binary installed")
def test_ai_move_is_legal_and_does_not_apply():
    eng = ChessEngine(skill_level=1, think_time=0.1)
    eng.apply("e2e4")
    fen_before = eng.fen()
    ai = eng.ai_move()
    assert ai in eng.legal_moves()                  # a legal reply
    assert eng.fen() == fen_before                  # ai_move did NOT apply
    eng.close()


# --------------------- Role 1 contract via the mock ------------------------ #

def test_mock_voice_matches_role1_contract():
    eng = ChessEngine()
    voice = MockVoice(script=["e2e4"])
    legal = eng.legal_moves()
    uci = voice.listen_for_move(legal)              # same signature as Role 1
    assert uci in legal
    assert eng.apply(uci).ok

def test_mock_voice_unrecognized():
    voice = MockVoice(unrecognized_every=1)
    assert voice.listen_for_move(["e2e4"]) == "unrecognized"
