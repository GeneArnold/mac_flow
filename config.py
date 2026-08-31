"""Config loader and writer for mac_flow.toml.

The TOML file lives next to this module. On first run it may not exist —
_DEFAULTS covers every key so the app starts safely without it.

Key design decisions:
- _deep_merge lets the on-disk file override only the keys it declares;
  missing keys fall back to defaults automatically.
- API keys live exclusively in .env / environment — never in the TOML. They
  are surfaced on the loaded config dict under cfg["keys"][ENV_NAME].
- Providers are switchable. Transcription is either local mlx-whisper or the
  Groq Whisper API; enhancement is any OpenAI-compatible chat endpoint
  (Ollama locally, or DeepSeek / OpenRouter / Groq in the cloud). The
  connection details for each enhancement provider live in ENHANCE_PROVIDERS
  so the TOML only has to name a provider, not repeat base URLs.
- tomli_w (optional) writes proper TOML. If absent, the manual fallback
  writer handles our flat two-level sections.

When adding new settings, add them to _DEFAULTS first so old configs
without that key still work.
"""

import os
import tomllib
from pathlib import Path
from typing import Any

import paths

try:
    import tomli_w

    _HAS_TOMLI_W = True
except ImportError:
    _HAS_TOMLI_W = False

CONFIG_PATH = paths.data_dir() / "mac_flow.toml"
ENV_PATH = paths.data_dir() / ".env"

# Enhancement backends — all OpenAI-compatible chat APIs, so a single
# OpenAI-SDK client (see core/enhancer.py) drives every one of them. Only the
# base_url, the .env var holding the key, and a default model differ.
# Ollama runs locally and needs no real key (the SDK still wants a non-empty
# string, which the engine supplies). api_key_env=None marks a keyless local
# provider.
ENHANCE_PROVIDERS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": None,
        "model": "qwen2.5:7b",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-v4-flash",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "meta-llama/llama-3.3-70b-instruct",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        # Groq retired the llama-3.3-70b-versatile model that used to be the
        # default here. Because mac_flow.toml ships `model = ""`, meaning "use
        # this registry default", every Groq user silently got a model that no
        # longer exists and every enhancement failed. Verified present on a
        # free-tier account 2026-08-31; gpt-oss-20b was the fastest candidate
        # that fully stripped filler words (0.37s vs 0.60s for the 120b).
        "model": "openai/gpt-oss-20b",
    },
}

# Every .env var the app knows how to read/write. Keys are stored in .env
# (gitignored), never in the TOML, and surfaced as cfg["keys"][NAME].
_KEY_ENV_VARS = ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY")

# Default values for every setting. The on-disk TOML is merged on top of these,
# so any missing key silently falls back to the default here.
#
# Default hotkey is Option+Shift+Space — unclaimed by any common macOS app
# or utility. Avoids collisions with Spotlight (Cmd+Space), macOS
# input-source switching (Ctrl+Space), Raycast (Option+Space), and
# Sublime Text's Auto Complete (Ctrl+Space). See NOTES.md Session 4
# continuation for the full hotkey-collision history.
_DEFAULTS = {
    "app": {"version": "0.1.0", "autostart": False},
    "audio": {"device_index": -1, "sample_rate": 16000, "channels": 1},
    "hotkey": {"modifiers": ["alt", "shift"], "key": "space"},
    # Transcription: "mlx" runs Whisper locally (no key, offline); "groq"
    # uses the Groq Whisper API (needs GROQ_API_KEY). The *_model keys let
    # each backend keep its own model string so switching back is lossless.
    "transcription": {
        "provider": "mlx",
        "mlx_model": "mlx-community/whisper-large-v3-turbo",
        "groq_model": "whisper-large-v3",
    },
    # Enhancement: mode is what to do (raw/clean/rewrite); provider is who
    # does it (see ENHANCE_PROVIDERS). model="" means "use the provider's
    # default model" from the registry.
    "enhancement": {"mode": "clean", "provider": "ollama", "model": ""},
    "output": {"auto_paste": True, "auto_clipboard": True, "save_history": True},
    "ui": {"notify_on_result": False},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_env() -> None:
    """Load .env file into os.environ if it exists.
    Only sets variables not already present in the environment.
    Format: KEY=value (lines starting with # are ignored).
    """
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def load() -> dict:
    """Load config from disk merged with defaults. Safe to call repeatedly.

    API keys are resolved from .env / environment only (never the TOML) and
    attached under cfg["keys"][ENV_NAME].
    """
    _load_env()
    cfg = _deep_merge({}, _DEFAULTS)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            on_disk = tomllib.load(f)
        cfg = _deep_merge(cfg, on_disk)
    cfg["keys"] = {name: os.environ.get(name, "") for name in _KEY_ENV_VARS}
    return cfg


def save(cfg: dict) -> None:
    """Write the full config dict to disk as TOML."""
    # Never persist resolved API keys to the TOML — they live in .env only.
    cfg = _deep_merge({}, cfg)
    cfg.pop("keys", None)

    if _HAS_TOMLI_W:
        with open(CONFIG_PATH, "wb") as f:
            tomli_w.dump(cfg, f)
        return

    # Manual fallback — handles bool, str, list[str], and numeric values.
    lines = []
    for section, values in cfg.items():
        lines.append(f"\n[{section}]")
        for k, v in values.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            elif isinstance(v, list):
                items = ", ".join(f'"{i}"' for i in v)
                lines.append(f"{k} = [{items}]")
            else:
                lines.append(f"{k} = {v}")
    CONFIG_PATH.write_text("\n".join(lines).lstrip() + "\n", encoding="utf-8")


def set_value(section: str, key: str, value: Any) -> None:
    """Update a single key in a section and save to the TOML."""
    cfg = load()
    cfg[section][key] = value
    save(cfg)


def set_api_key(env_name: str, value: str) -> None:
    """Store an API key in .env (gitignored) rather than the TOML, so it can
    never accidentally be committed to git or shared over the network.
    """
    _save_env_key(env_name, value)
    os.environ[env_name] = value


def _save_env_key(env_key: str, value: str) -> None:
    """Write or update a single KEY=value line in the .env file."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{env_key}=") or line.startswith(f"{env_key} ="):
            lines[i] = f'{env_key}="{value}"'
            found = True
            break
    if not found:
        lines.append(f'{env_key}="{value}"')
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
