"""Phonetic phrase expansion and vocabulary flattening for Chess moves (Role 1).

Maps chess moves to spoken representations and gathers unique words for Vosk's
grammar engine.
"""

from typing import List, Dict, Optional

# Map letters to common spoken homophones and NATO variations
FILE_PHONETICS: Dict[str, List[str]] = {
    "a": ["a", "alpha"],
    "b": ["b", "be", "bee", "bravo"],
    "c": ["c", "see", "sea", "charlie"],
    "d": ["d", "de", "dee", "delta"],
    "e": ["e", "echo"],
    "f": ["f", "foxtrot"],
    "g": ["g", "gee", "golf"],
    "h": ["h", "eight", "aitch", "hotel"],
}

# Map ranks to digits and fully spelled-out words/homophones
RANK_PHONETICS: Dict[str, List[str]] = {
    "1": ["1", "one", "wun"],
    "2": ["2", "two", "too", "to"],
    "3": ["3", "three", "tree"],
    "4": ["4", "four", "fower"],
    "5": ["5", "five", "fife"],
    "6": ["6", "six"],
    "7": ["7", "seven"],
    "8": ["8", "eight"],
}

# Promotion piece to word mapping
PROMOTION_MAP: Dict[str, List[str]] = {
    "q": ["queen", "promote queen", "promote to queen"],
    "r": ["rook", "promote rook", "promote to rook"],
    "b": ["bishop", "promote bishop", "promote to bishop"],
    "n": ["knight", "promote knight", "promote to knight"],
}

# List of standard chess pieces for prefix generation
PIECES: List[str] = ["pawn", "knight", "bishop", "rook", "queen", "king"]


def _square_permutations(square: str) -> List[str]:
    """Generates phonetic variations for a single chess square (e.g., 'e2').

    For example, 'e2' yields ['e2', 'e 2', 'e two', 'e too', 'e to', 'echo two', ...].
    """
    if len(square) != 2:
        return [square]

    file_char = square[0]
    rank_char = square[1]

    # Initialize with raw digit format and space-separated digits
    perms = [square, f"{file_char} {rank_char}"]

    file_words = FILE_PHONETICS.get(file_char, [file_char])
    rank_words = RANK_PHONETICS.get(rank_char, [rank_char])

    # Generate Cartesian product of phonetic homophones
    for f in file_words:
        for r in rank_words:
            val = f"{f} {r}"
            if val not in perms:
                perms.append(val)

    return perms


def generate_phrases(move: str, piece: Optional[str] = None) -> List[str]:
    """Maps a standard legal UCI move (e.g. 'e2e4', 'e1g1', 'e7e8q') to natural phrasing permutations.

    Strictly applies rules for castling, promotions, and letters/numbers spacing.

    Args:
        piece: Spoken word for the piece that actually moves ("pawn".."king"),
            as supplied by the rules authority. When given, ONLY that word is
            used as a prefix — saying "knight to g3" can then never match the
            pawn move g2g3. When None (no board context), every piece word is
            accepted, which risks exactly that cross-piece false match.
    """
    move_lower = move.lower().strip()

    # STRICT CASTLING RULE (only the king castles; e1g1 by a rook/queen is a
    # normal move and falls through to standard phrasing when piece is known)
    if piece in (None, "king"):
        if move_lower in ("e1g1", "e8g8"):
            return ["kingside castle", "short castle"]
        if move_lower in ("e1c1", "e8c8"):
            return ["queenside castle", "long castle"]

    piece_words = [piece] if piece else PIECES

    # Standard square extraction
    if len(move_lower) >= 4:
        from_sq = move_lower[0:2]
        to_sq = move_lower[2:4]
        from_perms = _square_permutations(from_sq)
        to_perms = _square_permutations(to_sq)

        phrases = []

        # Check if there is a promotion (5th character)
        promo_suffixes = [""]
        if len(move_lower) == 5:
            promo_char = move_lower[4]
            promo_suffixes = PROMOTION_MAP.get(promo_char, [promo_char])

        # Generate combinatorics for movements
        for f in from_perms:
            for t in to_perms:
                for promo in promo_suffixes:
                    suffix = f" {promo}".rstrip()
                    # 1. Standard movements
                    phrases.append(f"{f} {t}{suffix}")
                    phrases.append(f"{f} to {t}{suffix}")
                    
                    # 2. Piece-prefixed movements
                    for pw in piece_words:
                        phrases.append(f"{pw} {f} {t}{suffix}")
                        phrases.append(f"{pw} to {t}{suffix}")
                        phrases.append(f"{pw} {f} to {t}{suffix}")

        # Piece prefix mapping for destination square only (e.g. "pawn to e4")
        for t in to_perms:
            for promo in promo_suffixes:
                suffix = f" {promo}".rstrip()
                for pw in piece_words:
                    phrases.append(f"{pw} to {t}{suffix}")
                    phrases.append(f"{pw} {t}{suffix}")

        # Clean double spaces and deduplicate
        unique_phrases = []
        for p in phrases:
            cleaned = " ".join(p.split())
            if cleaned not in unique_phrases:
                unique_phrases.append(cleaned)
        return unique_phrases

    return []


def flatten_vocabulary(legal_moves: List[str],
                       pieces: Optional[Dict[str, str]] = None) -> List[str]:
    """Extracts every unique individual word across all current phrase permutations.

    Returns a clean list of unique lowercase words to feed into Vosk's grammar engine.
    """
    words = set()
    for move in legal_moves:
        phrases = generate_phrases(move, (pieces or {}).get(move))
        for phrase in phrases:
            for word in phrase.split():
                clean_word = word.lower().strip()
                if clean_word:
                    words.add(clean_word)
    return sorted(list(words))
