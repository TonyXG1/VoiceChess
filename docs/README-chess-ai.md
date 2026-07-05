# Chess AI (Role 2) - read this before you merge it in

Everything you need to know explained below

## What you get from it

One object, called ChessEngine. You make one at the start of a game and keep it
around. Everything goes through it. It holds the real board, so it's the single
source of truth for the game. You don't track the board yourself, you ask it.

The stuff you'll actually call:

- legal_moves() gives you the list of every legal move right now, as short
  strings like "e2e4". This is the list you feed to the voice guy every turn.
- apply("e2e4") checks a move and plays it. Works for both the human's move and
  the computer's move. If the move is illegal it refuses and the board doesn't
  change (more on that below).
- ai_move() asks Stockfish for the computer's reply and gives you the move
  string. Important: it does NOT play it. You get the string, then you call
  apply() on it when you're ready. That's on purpose, so you can move the human's
  piece with the arm first, then deal with the computer's.
- status() tells you the state after the last move: whose turn, is it check,
  checkmate, stalemate, draw, is the game over, and who won.
- speak("some text") says a sentence out loud (or prints it, depending on how
  audio is set up).

And two handy extras: describe("e2e4") gives you the spoken sentence for a move
without playing it, and fen() gives you a snapshot string of the board if you
ever need to save or debug a position.


## The one thing to understand about apply()

apply() gives you back a little result object every time. The fields you care
about:

- ok is True if the move was legal and got played, False if it was refused.
- error is a plain-English reason when ok is False.
- readback is the spoken sentence, like "Queen takes f7, check".
- status is the same game-state info status() would give you.

So the rule is simple: if ok is False, don't move the arm, tell the player to
try again, and stay on the same turn. If ok is True, you're good to move the
piece. This is the safety net that stops an impossible move from ever reaching
the motors. You don't have to validate anything yourself, just check ok.


## The turn loop, in order

This is the sequence for one full turn. This exact loop is now the real turn state
machine in orchestrator/state_machine.py (driven by main.py), but here it is in words:

1. Ask legal_moves() for the current move list.
2. Give that list to the voice guy's listen_for_move(list). He gives you back one
   move string, or the word "unrecognized".
3. If it's "unrecognized", speak something like "didn't catch that", and go back
   to step 1 without changing the turn.
4. Otherwise call apply(move). If ok is False, same thing, tell them to retry.
5. If ok is True, speak the readback, then tell the arm to move the human's piece.
6. Check status. If the game's over, announce it and stop.
7. Call ai_move() to get the computer's reply string.
8. Call apply() on that reply. Speak its readback. Tell the arm to move the
   computer's piece.
9. Check status again. If it's over, announce and stop. Otherwise back to step 1.

One practical note that matters for you: only let the mic listen during step 2.
Keep it off while the arm is moving, or it'll pick up motor noise and think
that's a move. The engine doesn't control the mic, you do, so that's your call
to make in the loop.


## How it connects to the voice guy

You two already line up because you both use the same move format (those "e2e4"
strings, the format is called UCI). That's literally the only thing that crosses
between his part and mine. I give a list, he returns one item from the list.

That integration is now done: main.py wires my ChessEngine to his VoiceMatchEngine
through the orchestrator. If his mic or voice model isn't there, `python main.py
--text` swaps in a typed/scripted stand-in (TextVoice) that runs the whole matching
pipeline without hardware, so you can test the plumbing anytime. It prints a line
telling you which voice source it used, live or text.

If you ever want to drive the engine yourself, you don't need the orchestrator at
all — just import ChessEngine and call it directly in your own loop (see the code
example below).


## Where the robot arm goes

In the demo output you'll see lines like "[ROBOT] move human piece: e2e4". Those
are the two spots where the arm actually moves a piece, once for the human's move
(after step 5) and once for the computer's (after step 8). Right now they just
print. That's where the motion-planning and motor code hooks in. My part hands
off a clean, already-validated move string at each of those points, so whoever
owns the arm just needs the from-square and to-square, which is right there in
the move.


## Audio

Three modes: print only (just shows the text), espeak (offline robotic voice,
what we'll use on the Pi), and piper (offline but sounds much more human, needs a
voice file downloaded). You pick the mode in one spot and nothing else changes.
On Windows, espeak automatically uses the built-in Microsoft voice, so it talks
without installing anything. On the Pi you install espeak-ng first.

Quick tell: if you see the [SPEAK] lines printing, it's in print mode. If those
lines are gone, it's actually talking out loud.


## Setup and how to test it yourself

You need Python and, for the computer opponent, the Stockfish program.

    pip install -r requirements.txt

Get Stockfish. On the Pi:

    sudo apt install stockfish

On Windows, run `winget install --id Stockfish.Stockfish -e`, or download it from
stockfishchess.org and drop stockfish.exe in the repo root (next to main.py).

Fast check that everything works (no mic needed, a few seconds):

    python -m pytest -q

You want all tests passing. A "1 skipped" line just means Stockfish isn't installed
yet, that's fine, only the opponent test skipped.

Watch a full game run itself, scripted moves feeding the real matcher, Stockfish
answering (no mic, no hardware):

    python main.py --text --script "e2e4,f1c4,d1h5,h5f7" --skill 1 --think 0.1

Add --tts espeak to that line to hear it talk. Drop --text (once the mic + Vosk
model are set up) to use the real microphone.


## A tiny code example, if you'd rather just call it

    from chess_ai import ChessEngine

    eng = ChessEngine(skill_level=5, think_time=0.5)   # human is White

    legal = eng.legal_moves()          # give this to the voice guy
    result = eng.apply("e2e4")         # play the move he returns
    if result.ok:
        eng.speak("You said " + result.readback)
        if not result.status.is_game_over:
            reply = eng.ai_move()      # computer's move (string only)
            eng.apply(reply)           # now play it
    eng.close()                        # shuts Stockfish down at the end


## Settings you can change

- skill_level is 0 to 20, higher is stronger. think_time is seconds the computer
  gets to think. I set them low for a friendly, fast demo. Turn them up if you
  want a tougher opponent.
- Human plays White by default.
- If Stockfish isn't in the default spot, you can pass its path in when you make
  the ChessEngine.


## Three things the team should agree on (not blockers, just decisions)

- Promotion (a pawn reaching the far end and turning into a queen) fully works
  right now. If it makes the arm's life hard we can cut it for the demo.
- Which color the human plays. White right now, easy to flip.
- How strong and fast the computer should be.


## The files

- chess_ai/engine.py - the brain (rules, Stockfish, spoken text). The main thing.
- chess_ai/speech.py - the talking part (print, espeak, piper).
- chess_ai/mock_voice.py - the fake voice for testing without a mic.
- orchestrator/state_machine.py - the full turn loop, wired up by main.py.
- main.py - the entry point that builds and runs everything.
- speak_test.py - just checks audio works on its own.
- docs/interfaces.md - the short technical cheat sheet for the calls.
- tests/ - the automatic tests.

That's everything. If something doesn't line up when you merge, the first place
to look is the move strings matching on both sides and Stockfish being installed.

