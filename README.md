# Mac Flow

**Hold a key, speak, release — your words appear wherever your cursor is.**

Mac Flow is a free, open-source voice dictation app for macOS (Apple Silicon). It captures your microphone, sends the audio to Groq's Whisper API for transcription, optionally polishes the result with a Llama LLM, and pastes the text directly into whatever window you're typing in — no copy/paste required.

Ported from [Linux Flow](https://github.com/GeneArnold/linux_flow). Same core, different platform plumbing.

---

## Requirements

- macOS 12+ on Apple Silicon (M1/M2/M3/M4). Intel Macs are untested but should work.
- Python 3.11+ (Homebrew's `python@3.11` or newer, or pyenv)
- A free [Groq API key](https://console.groq.com)

---

## Installation

```bash
# 1. Clone
git clone https://github.com/GeneArnold/mac_flow.git
cd mac_flow

# 2. Create venv and install Python packages
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Set your Groq API key (either method works)
cp .env.example .env              # then edit .env
#   — or use the menubar: Settings → Set Groq API Key...

# 4. Run
python main.py
```

The first time you run Mac Flow, macOS will prompt for permissions. **Grant all of these** in **System Settings → Privacy & Security**:

| Permission | Why | Where |
|---|---|---|
| **Microphone** | record your voice | Microphone |
| **Accessibility** | simulate ⌘V to paste transcribed text | Accessibility |
| **Input Monitoring** | global hotkey capture | Input Monitoring |

Each of these must be granted to whatever is running Python — if you `python main.py` from Terminal, grant them to **Terminal**. If you run from iTerm, grant them to iTerm. If you use the LaunchAgent installer below, grant them to `/usr/bin/python3` or your venv Python.

After granting Accessibility / Input Monitoring, **quit and relaunch** the app — macOS only re-reads these on process start.

---

## Launch on Login

```bash
bash install.sh install      # adds ~/Library/LaunchAgents/com.genearnold.mac_flow.plist
bash install.sh uninstall    # removes it
```

Logs go to `mac_flow.log` in the project directory.

---

## Usage

| Action | How |
|---|---|
| Start recording | Hold `Option+Space` (default) |
| Stop & transcribe | Release the hotkey |
| Change hotkey | Edit `mac_flow.toml` and restart |
| Change mic | Menubar → Microphone |
| AI enhancement mode | Menubar → Mode |
| Copy last transcript | Menubar → Copy Last Transcript |
| Browse history | Menubar → Show Recent History |

**Why `Option+Space` and not `Ctrl+Space`?** macOS uses `Cmd+Space` for Spotlight and many users have `Ctrl+Space` bound to input-source switching. `Option+Space` is free in default macOS and matches Wispr Flow's default.

---

## Architecture

```
mac_flow/
├── main.py                Entry point. --list-mics flag for mic debugging.
├── config.py              TOML loader/writer with deep-merge defaults.
├── mac_flow.toml          User config (hotkey, mic, models, etc.)
│
├── core/
│   ├── engine.py          Orchestrates the full pipeline. Owns all workers.
│   ├── recorder.py        sounddevice → CoreAudio mic capture → WAV bytes.
│   ├── transcriber.py     Groq Whisper API call.
│   └── enhancer.py        Groq Llama API call (clean / rewrite modes).
│
├── adapters/
│   ├── base.py            ABCs + factory functions.
│   └── macos.py           NSPasteboard + Quartz CGEvent Cmd+V, pynput hotkey.
│
├── db/
│   └── history.py         SQLite store for transcription history.
│
└── ui/
    └── app.py             rumps menubar app with NSAlert-based settings.
```

**How text injection works:** Most Mac apps don't expose a "type this text here" API. The reliable trick (used by Wispr Flow, TextExpander, etc.) is to save the current clipboard, write the transcript to the pasteboard, fire a synthetic `⌘V` via `Quartz.CGEventCreateKeyboardEvent`, and restore the clipboard ~400ms later. It's instant regardless of text length and preserves Unicode.

**Threading model:** The pynput hotkey listener runs its own thread. When the hotkey fires, `_process()` runs on a fresh daemon thread so Groq API calls never block the listener. rumps handles main-thread menu callbacks automatically — no `GLib.idle_add` needed like on Linux.

---

## Configuration

Edit `mac_flow.toml` directly or use the menubar. The API key is stored separately in a gitignored `.env` file:

```toml
[audio]
device_index = -1       # -1 = system default

[hotkey]
modifiers = ["alt"]     # cmd | ctrl | alt (option) | shift
key = "space"

[groq]
whisper_model = "whisper-large-v3"
llm_model = "llama-3.3-70b-versatile"

[enhancement]
mode = "clean"          # raw | clean | rewrite

[output]
auto_paste = true       # false = clipboard only
save_history = true
```

---

## Known Limitations

- **Hotkey passthrough** — pynput is a passive listener on macOS; the hotkey combo still reaches the active application. Apps that respond to `Option+Space` (rare) may show a brief reaction. Pick a less-common combo if this bothers you.
- **No waveform overlay** — the Linux version has a floating GTK window showing mic levels. On Mac, the menubar icon flips between `🎙` / `🔴` / `✨` instead. Good enough; simpler.
- **First-run permissions dance** — macOS prompts are per-binary. If you run from Terminal and later switch to a LaunchAgent, you'll need to grant the new Python binary its own set of permissions.

---

## License

MIT — same as Linux Flow.
