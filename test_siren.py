"""Quick siren sound test — run this to verify audio works on your machine.

Usage:  python test_siren.py
"""

import os
import sys
import time


def play_siren_wav(duration_seconds: int = 10) -> None:
    """Play a loud looping alarm WAV through the system audio driver."""
    import winsound

    alarm_wav = r"C:\Windows\Media\Alarm01.wav"
    if not os.path.exists(alarm_wav):
        print("[!] Alarm01.wav not found, falling back to Windows Critical Stop")
        alarm_wav = r"C:\Windows\Media\Windows Critical Stop.wav"

    print(f"[*] Playing WAV siren for ~{duration_seconds}s  =>  {alarm_wav}")
    print("    (Press Ctrl+C to stop early)\n")

    end = time.monotonic() + duration_seconds
    while time.monotonic() < end:
        # SND_FILENAME = play from file, SND_NOSTOP = don't interrupt previous
        winsound.PlaySound(alarm_wav, winsound.SND_FILENAME)


def play_siren_beep(duration_seconds: int = 10) -> None:
    """Play a siren using winsound.Beep (console-speaker; may be silent on some PCs)."""
    import winsound

    print(f"[*] Playing Beep-based siren for ~{duration_seconds}s")
    print("    (If you hear NOTHING, your system blocks console beeps)\n")
    end = time.monotonic() + duration_seconds
    while time.monotonic() < end:
        winsound.Beep(2500, 400)
        winsound.Beep(1000, 400)


def main() -> None:
    if os.name != "nt":
        print("This test is for Windows only.")
        sys.exit(1)

    print("=" * 50)
    print("  RCB Monitor — Siren Sound Test")
    print("=" * 50)
    print()

    # ── Test 1: WAV-based siren (reliable) ─────────────────────────────────
    print("── TEST 1: WAV-file siren (uses system audio driver) ──")
    try:
        play_siren_wav(duration_seconds=6)
        heard_wav = input("Did you hear the alarm? (y/n): ").strip().lower()
    except KeyboardInterrupt:
        heard_wav = "y"
        print()

    print()

    # ── Test 2: Beep-based siren (current implementation) ──────────────────
    print("── TEST 2: winsound.Beep siren (current monitor code) ──")
    try:
        play_siren_beep(duration_seconds=6)
        heard_beep = input("Did you hear the beep siren? (y/n): ").strip().lower()
    except KeyboardInterrupt:
        heard_beep = "y"
        print()

    print()
    print("=" * 50)
    print("  RESULTS")
    print("=" * 50)
    if heard_wav == "y" and heard_beep != "y":
        print("  >> WAV works, Beep does NOT.")
        print("  >> The monitor should be switched to WAV-based siren.")
    elif heard_wav == "y" and heard_beep == "y":
        print("  >> Both work! WAV is still recommended (louder).")
    elif heard_wav != "y" and heard_beep == "y":
        print("  >> Beep works but WAV does not — unusual. Check volume.")
    else:
        print("  >> Neither worked. Check your system volume / audio output!")
    print()


if __name__ == "__main__":
    main()
