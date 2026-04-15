#!/usr/bin/env python3
"""mac_flow — Wispr Flow alternative for macOS (Apple Silicon).

Usage:
  python main.py              # launch the menubar app
  python main.py --list-mics  # list available microphones and exit
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="mac_flow — voice dictation for macOS"
    )
    parser.add_argument(
        "--list-mics", action="store_true", help="List available microphones"
    )
    args = parser.parse_args()

    if args.list_mics:
        from core.recorder import Recorder

        print("\nAvailable microphones:")
        for dev in Recorder.list_devices():
            print(
                f"  [{dev['index']}] {dev['name']} ({dev['channels']}ch, {dev['sample_rate']}Hz)"
            )
        print("\nSet 'audio.device_index' in mac_flow.toml (or use the Microphone menu).")
        sys.exit(0)

    # Importing ui.app triggers rumps + pyobjc initialisation; defer it so
    # --list-mics doesn't pay that cost.
    from ui.app import run

    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
