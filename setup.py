"""py2app build script for MacFlow.

Usage:
    python setup.py py2app          # standalone .app (for distribution)
    python setup.py py2app -A       # alias mode (for development — faster, links to source)

The resulting app lands in dist/MacFlow.app.
"""

import sys

from setuptools import setup

# py2app's modulegraph walks imports via the AST and blows the default 1000-frame
# limit on deeply-nested packages. Harmless to raise; it only affects the build.
sys.setrecursionlimit(10000)


def _portaudio_dylib() -> str:
    """Absolute path to libportaudio.dylib inside the installed sounddevice.

    Resolved from the imported package rather than hardcoded, because the path
    embeds the interpreter version (venv/lib/pythonX.Y/...). Hardcoding it
    silently breaks the build on any other Python — the bundle then fails at
    launch with "OSError: PortAudio library not found".
    """
    import _sounddevice_data
    from pathlib import Path

    dylib = (Path(_sounddevice_data.__file__).parent
             / "portaudio-binaries" / "libportaudio.dylib")
    if not dylib.exists():
        raise SystemExit(f"libportaudio.dylib not found at {dylib}")
    return str(dylib)

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
    "frameworks": [_portaudio_dylib()],
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
    # The local-transcription stack (mlx-whisper and its torch/numba/scipy
    # dependency tree) is deliberately kept out of the bundle. `import
    # mlx_whisper` in core/transcriber.py is lazy, inside the transcribe call,
    # so a Groq-configured bundle never touches it. Without these excludes
    # modulegraph tries to walk all of torch and dies with RecursionError —
    # and even when it survives, it inflates the bundle by well over a GB.
    # A bundled app must therefore use transcription.provider = "groq".
    "excludes": [
        "mlx",
        "mlx_whisper",
        "torch",
        "sympy",
        "numba",
        "llvmlite",
        "scipy",
        "networkx",
        "tiktoken",
        "huggingface_hub",
        "transformers",
        "setuptools",
        "pip",
    ],
    "resources": [
        # The template, not the live config — mac_flow.toml is untracked and
        # machine-specific. paths.ensure_defaults() copies this into
        # Application Support on first launch.
        "mac_flow.toml.example",
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
