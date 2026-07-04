#!/usr/bin/env python3
"""Demonstration and hardware verification script for VoiceChess Voice Matching.

Lists available audio hardware devices, initializes the offline speech engine,
and runs a test recognition loop.

Run from the repo root as a module (needs a mic + Vosk model):
    python -m voice_matching.demo
"""

import sys
import sounddevice
from . import config
from .engine import VoiceMatchEngine

def print_audio_devices():
    """Prints a list of all audio devices detected by PortAudio."""
    print("\n--- Detected Audio Devices ---")
    devices = sounddevice.query_devices()
    for idx, dev in enumerate(devices):
        max_in = dev.get('max_input_channels', 0)
        max_out = dev.get('max_output_channels', 0)
        default_marker = " [Default Input]" if idx == sounddevice.default.device[0] else ""
        print(f"Index {idx}: {dev['name']} (Inputs: {max_in}, Outputs: {max_out}){default_marker}")
    print("------------------------------\n")

def main():
    # 1. Print all detected audio devices to help with debugging hardware
    print_audio_devices()

    # 2. Check if a model exists in the path
    print(f"Checking for Vosk model at: {config.MODEL_PATH}")
    try:
        engine = VoiceMatchEngine()
        print("Vosk model loaded successfully!\n")
    except Exception as e:
        print(f"\n[ERROR] Failed to load Vosk model: {e}")
        print("Please ensure you have downloaded a Vosk model and placed it in the 'model' directory,")
        print("or that you have set the VOSK_MODEL_PATH environment variable correctly.")
        print("\nRefer to the README.md for step-by-step setup instructions.")
        sys.exit(1)

    # 3. Define test moves to recognize
    test_moves = ["e2e4", "g1f3", "e1g1", "d7d8q"]
    print(f"We will test speech recognition against these valid moves: {test_moves}")
    print("Specifically, you can test speaking:")
    print("  - 'e2 e4' or 'pawn to e4'")
    print("  - 'g1 f3' or 'knight to f3'")
    print("  - 'kingside castle' or 'short castle'")
    print("  - 'd7 d8 queen' or 'd7 to d8 promote queen'")
    print("\nStarting microphone capture...")
    print("Listening (speak now, you have 5 seconds)...")

    try:
        result = engine.listen_for_move(test_moves)
        print(f"\nRecognition Finished!")
        print(f"Recognized UCI Move: {result}")
    except Exception as e:
        print(f"\n[ERROR] Failed during audio capture/decoding: {e}")
        print("If you encountered a PortAudioError, check if another application is using the mic")
        print("or if the config.DEVICE_INDEX or sample rate settings need adjustment.")

if __name__ == "__main__":
    main()
