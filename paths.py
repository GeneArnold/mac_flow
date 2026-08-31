"""Path resolution for MacFlow — works in both source and .app bundle modes.

When running from source (`python main.py`):
    All files live next to the script, same as before.

When running inside a py2app .app bundle:
    - User data (config, .env, history) → ~/Library/Application Support/MacFlow/
    - Bundled assets (icons) → .../MacFlow.app/Contents/Resources/assets/
    - Default config is copied from Resources to Application Support on first launch.
"""

import os
import shutil
import sys
from pathlib import Path


def is_bundled() -> bool:
    """True when running inside a py2app .app bundle."""
    return getattr(sys, "frozen", False) == "macosx_app"


def _source_root() -> Path:
    """Project root when running from source."""
    return Path(__file__).parent


def _bundle_resources() -> Path:
    """The .app's Contents/Resources directory."""
    # sys.executable → .../MacFlow.app/Contents/MacOS/MacFlow
    return Path(sys.executable).parent.parent / "Resources"


def _app_support() -> Path:
    """~/Library/Application Support/MacFlow/ — created on first access."""
    d = Path.home() / "Library" / "Application Support" / "MacFlow"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- Public API ---

def data_dir() -> Path:
    """Directory for user-writable files (config, .env, history.db)."""
    if is_bundled():
        return _app_support()
    return _source_root()


def assets_dir() -> Path:
    """Directory containing icon PNGs."""
    if is_bundled():
        return _bundle_resources() / "assets"
    return _source_root() / "assets"


def pending_dir() -> Path:
    """Directory where WAVs are stashed when transcription fails,
    so the audio isn't lost to a transient network error. Created
    on first access.
    """
    d = data_dir() / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_defaults() -> None:
    """Create the user's config files from the shipped templates if absent.

    Runs in both modes. mac_flow.toml is deliberately NOT tracked in git — it
    holds machine-specific settings (device_index, hotkey, provider choices),
    and syncing it between machines breaks whichever one has a different audio
    device. The tracked file is mac_flow.toml.example; the live config is
    generated from it on first run, exactly as .env is generated from
    .env.example.
    """
    dst = data_dir()
    resources = _bundle_resources() if is_bundled() else _source_root()

    # Live config from the template, if the user has none yet.
    toml_dst = dst / "mac_flow.toml"
    toml_src = resources / "mac_flow.toml.example"
    if not toml_dst.exists() and toml_src.exists():
        shutil.copy2(toml_src, toml_dst)

    # Copy .env.example as .env if no .env exists
    env_dst = dst / ".env"
    env_src = resources / ".env.example"
    if not env_dst.exists() and env_src.exists():
        shutil.copy2(env_src, env_dst)
