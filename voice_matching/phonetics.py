"""Phonetic phrase expansion and vocabulary flattening for Chess moves (Role 1).

Maps chess moves to spoken representations and gathers unique words for Vosk's
grammar engine. Also owns the position-independent move SUPERSET used to tell
"illegal move" apart from "unrecognized speech" (see all_possible_moves /
superset_index).
"""

import functools
from typing import List, Dict, Optional, Tuple

# Map letters to common spoken homophones and NATO variations.
# "eight" must NEVER be a homophone of file "h": having it in the grammar
# makes Vosk transcribe a spoken "a" as "eight" ("a three" -> "eight three"),
# which then matched h3 EXACTLY and silently played the wrong file. "eight"
# stays a rank-8 word only; "aitch"/"hotel" cover the h-file mishearings, and
# dropping it from the file map also keeps "eight" out of the grammar whenever
# no rank-8 move is legal, so Vosk must resolve a-vs-h from the audio itself.
FILE_PHONETICS: Dict[str, List[str]] = {
    "a": ["a", "alpha"],
    "b": ["b", "be", "bee", "bravo"],
    "c": ["c", "see", "sea", "charlie"],
    "d": ["d", "de", "dee", "delta"],
    "e": ["e", "echo"],
    "f": ["f", "foxtrot"],
    "g": ["g", "gee", "golf"],
    "h": ["h", "aitch", "hotel"],
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

# Confirmation vocabulary: spoken words accepted as an affirmative or negative
# answer when the engine reads the understood move back to the player.
YES_WORDS: List[str] = ["yes", "yeah", "yep", "correct", "confirm", "affirmative"]
NO_WORDS: List[str] = ["no", "nope", "wrong", "cancel", "negative", "incorrect"]

# Spoken names for promotion pieces, used when describing a move back to the player.
PROMOTION_NAMES: Dict[str, str] = {"q": "queen", "r": "rook", "b": "bishop", "n": "knight"}

# Castling phrases. "king side" / "queen side" (two words) cover Vosk splitting
# the compound word; "king castle" covers it dropping "side" entirely. The bare
# "castle" appears in BOTH lists on purpose: when only one castle is legal it
# matches that move outright; when both are legal the two moves tie at the same
# score and the ambiguity guard rejects the utterance, so the player must say
# which side.
KINGSIDE_CASTLE_PHRASES: List[str] = [
    "kingside castle", "king side castle", "king castle",
    "castle kingside", "castle king side",
    "short castle", "castle short", "castle",
]
QUEENSIDE_CASTLE_PHRASES: List[str] = [
    "queenside castle", "queen side castle", "queen castle",
    "castle queenside", "castle queen side",
    "long castle", "castle long", "castle",
]


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
            return list(KINGSIDE_CASTLE_PHRASES)
        if move_lower in ("e1c1", "e8c8"):
            return list(QUEENSIDE_CASTLE_PHRASES)

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


_SQUARES: List[str] = [f + r for f in "abcdefgh" for r in "12345678"]
_CASTLE_UCIS = ("e1g1", "e1c1", "e8g8", "e8c8")

# Piece-destination pseudo-moves ("knight@c3") let the superset classify
# utterances that name a piece and a destination but no origin ("knight to
# c3") as ILLEGAL when no such move is legal. They cannot be UCI strings --
# the origin is unknown and irrelevant to the spoken feedback. The engine
# skips every (piece, destination) pair that IS legal right now, so these
# only ever surface for moves the player cannot play.
PIECE_DEST_SEP = "@"


def piece_dest_key(piece: str, square: str) -> str:
    """Superset key for a piece+destination utterance, e.g. 'knight@c3'."""
    return f"{piece}{PIECE_DEST_SEP}{square}"


def _piece_dest_phrases(piece: str, square: str) -> List[str]:
    """Phrases a player says when naming only piece and destination."""
    return [form
            for t in _square_permutations(square)
            for form in (f"{piece} to {t}", f"{piece} {t}")]


def all_possible_moves() -> List[str]:
    """Every syntactically possible move, independent of position.

    All from->to square pairs (from != to) as UCI strings — the four castling
    UCIs (e1g1/e1c1/e8g8/e8c8) are square pairs and thus inherently included.
    KNOWN LIMITATION: promotion suffixes (e7e8q, ...) are skipped, so an
    illegal promotion utterance classifies as unrecognized, not illegal.
    """
    return [a + b for a in _SQUARES for b in _SQUARES if a != b]


def _superset_phrases(move: str) -> List[str]:
    """Lean phrase set for one SUPERSET move: fully-qualified forms only.

    Deliberately narrower than generate_phrases(): destination-only forms
    ("pawn to e4") would be shared by all 63 moves ending on that square and
    tie every natural utterance into "unrecognized"; piece prefixes without a
    board are meaningless. Full sets for ~4000 moves would also be ~8M strings
    — far beyond the Pi's memory. Legal moves get their full piece-aware
    phrases layered on top by the engine.
    """
    if move in _CASTLE_UCIS:
        # Side-specific phrases only: the bare "castle" phrase lives solely in
        # the LEGAL overlay, so "castle" resolves to the one legal castle (or
        # ties when both are legal) instead of always tying against the
        # not-even-possible side here in the superset.
        side = (KINGSIDE_CASTLE_PHRASES if move in ("e1g1", "e8g8")
                else QUEENSIDE_CASTLE_PHRASES)
        return [p for p in side if p != "castle"]
    from_perms = _square_permutations(move[0:2])
    to_perms = _square_permutations(move[2:4])
    phrases = []
    for f in from_perms:
        for t in to_perms:
            phrases.append(f"{f} {t}")
            phrases.append(f"{f} to {t}")
    return phrases


@functools.cache
def superset_index() -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(phrases, move_of_phrase) parallel flat tuples over ALL possible moves.

    Contains every square-pair move (fully-qualified phrases) plus every
    piece-destination pseudo-move (see PIECE_DEST_SEP). Built once per process
    on first use and cached at module scope — never regenerate per call/turn
    (~4000 moves, ~800k phrases; the build costs about a second). Lazy rather
    than at import so `import voice_matching` stays instant for text mode and
    non-matching tests.
    """
    phrases: List[str] = []
    moves: List[str] = []
    for move in all_possible_moves():
        for phrase in _superset_phrases(move):
            phrases.append(phrase)
            moves.append(move)
    for piece in PIECES:
        for square in _SQUARES:
            key = piece_dest_key(piece, square)
            for phrase in _piece_dest_phrases(piece, square):
                phrases.append(phrase)
                moves.append(key)
    return tuple(phrases), tuple(moves)


def describe_move(move: str) -> str:
    """Renders a UCI move as a natural phrase for reading back to the player.

    For example: 'e2e4' -> 'e2 to e4', 'e1g1' -> 'kingside castle',
    'e7e8q' -> 'e7 to e8 promoting to queen', 'knight@c3' -> 'knight to c3'.
    """
    move_lower = move.lower().strip()

    if PIECE_DEST_SEP in move_lower:
        piece, square = move_lower.split(PIECE_DEST_SEP, 1)
        return f"{piece} to {square}"

    if move_lower in ("e1g1", "e8g8"):
        return "kingside castle"
    if move_lower in ("e1c1", "e8c8"):
        return "queenside castle"

    if len(move_lower) >= 4:
        description = f"{move_lower[0:2]} to {move_lower[2:4]}"
        if len(move_lower) == 5:
            piece = PROMOTION_NAMES.get(move_lower[4], move_lower[4])
            description += f" promoting to {piece}"
        return description

    return move_lower


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
