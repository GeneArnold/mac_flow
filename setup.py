"""py2app build script for MacFlow.

Usage:
    python setup.py py2app          # standalone .app (for distribution)
    python setup.py py2app -A       # alias mode (for development — faster, links to source)

The resulting app lands in dist/MacFlow.app.
"""

from setuptools import setup

APP = ["main.py"]
DATA_FILES = [
    ("assets", [
        "assets/mic_idleTemplate.png",
        "assets/mic_recording.png",
        "assets/sparkleTemplate.png",
    ]),
]

OPTIONS = {
    "iconfile": "assets/mac_flow.icns",
    "plist": {
        "CFBundleName": "MacFlow",
        "CFBundleDisplayName": "MacFlow",
        "CFBundleIdentifier": "com.genearnold.macflow",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        # Menubar-only app — no Dock icon while running.
        # The app icon shows in Finder / Launchpad / Spotlight but not
        # in the Dock's running-apps area. This is standard for menubar
        # utilities (like Bartender, Hidden Bar, etc.).
        "LSUIElement": True,
        # Permission usage descriptions — macOS shows these in the
        # system prompt the first time the app requests each permission.
        "NSMicrophoneUsageDescription": (
            "MacFlow needs microphone access to record your voice for transcription."
        ),
        # Needed for global hotkey + simulated keystroke (⌘V paste)
        "NSAppleEventsUsageDescription": (
            "MacFlow uses Accessibility to capture your hotkey and paste transcribed text."
        ),
    },
    "packages": [
        "rumps",
        "groq",
        "openai",
        "pynput",
        "sounddevice",
        "_sounddevice_data",
        "numpy",
        "AppKit",
        "Quartz",
        "Foundation",
        "objc",
        # NOTE: local transcription (transcription.provider = "mlx") is not yet
        # bundle-ready — mlx ships Metal libraries and downloads multi-GB models
        # at runtime, which py2app doesn't package cleanly. Local mode currently
        # targets dev runs (`python main.py`). For a .app build, use a cloud
        # provider, or treat mlx bundling as a follow-up. "mlx_whisper" is left
        # out of packages deliberately so the build doesn't pull it in broken.
    ],
    # sounddevice bundles libportaudio.dylib — it can't live inside a zip
    "frameworks": [
        "venv/lib/python3.11/site-packages/_sounddevice_data/"
        "portaudio-binaries/libportaudio.dylib",
    ],
    "includes": [
        "paths",
        "config",
        "core",
        "core.engine",
        "core.recorder",
        "core.transcriber",
        "core.enhancer",
        "adapters",
        "adapters.base",
        "adapters.macos",
        "db",
        "db.history",
        "ui",
        "ui.app",
    ],
    "resources": [
        "mac_flow.toml",
        ".env.example",
    ],
}

setup(
    app=APP,
    name="MacFlow",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
