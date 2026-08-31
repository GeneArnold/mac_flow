# Mac Flow

**Hold a key, speak, release — your words appear wherever your cursor is.**

Mac Flow is a free, open-source voice dictation app for macOS (Apple Silicon). It captures your microphone, sends the audio to Groq's Whisper API for transcription, optionally polishes the result with a DeepSeek LLM, and pastes the text straight into whatever window you're typing in — no copy/paste required.

Ported from [Linux Flow](https://github.com/GeneArnold/linux_flow). Same core, different platform plumbing.

> **Note on naming.** The app is branded **Mac Flow** everywhere you see prose — but the installed `.app`, its bundle name, and every file path use **`MacFlow`** (no space). macOS does weird things with spaces in `.app` names, so the code-level name dropped the space. They're the same project.

---

## Requirements

- **macOS 12 (Monterey) or later**, **Apple Silicon (M1/M2/M3/M4) only**. The bundle is built `arm64`-thin and will not run on Intel Macs. (A universal-binary build is on the roadmap for Intel support.)
- **A free Groq API key** — you'll get one in a couple of minutes, instructions below.

That's it for end users. If you want to build from source yourself, see the **For developers** section at the end.

---

## Install — if someone sent you `MacFlow.app`

This is the normal path for people receiving a prebuilt copy of the app (by AirDrop, email, cloud storage, or a download link). Follow the steps in order — skipping any one of them will leave the app looking like it "doesn't work."

### Step 1 — Put the app in `/Applications`

1. If you received a `.zip`, double-click it to unpack. You should now have a file called **`MacFlow.app`**.
2. Drag `MacFlow.app` into your **Applications** folder. (You can run it from anywhere, but Applications is the standard spot and makes the rest of the instructions simpler.)

### Step 2 — Open it past the Gatekeeper warning (first launch only)

Mac Flow isn't signed by a paid Apple Developer account, so the first time you open it macOS will put up a scary-looking dialog saying something like *"macOS cannot verify that this app is free of malware."* This is expected for any indie app that isn't in the App Store.

To get past it:

1. Open **Finder** and go to your **Applications** folder.
2. **Control-click** (or right-click) on `MacFlow.app`.
3. Choose **Open** from the context menu.
4. In the warning dialog that appears, click **Open** (on macOS 15+ the button may say **Open Anyway**, sometimes found under *System Settings → Privacy & Security* after the first denial).
5. If macOS still refuses, open **System Settings → Privacy & Security**, scroll to the bottom, and click **Open Anyway** next to the MacFlow notice there.

You only need to do this once — after the first successful launch macOS trusts the app and a normal double-click works from then on.

Once it launches, look at the top-right of your screen (the menubar). You should see a small microphone icon. **That's the app** — it has no window and no Dock icon by design. Click the mic icon to see the menu.

### Step 3 — Get a free Groq API key

Mac Flow uses **Groq** for speech-to-text and **DeepSeek** for optional cleanup/rewrite. You need to create an account with each and generate your own API keys — the developer is not sharing theirs. Groq's free tier is generous enough for normal daily dictation; DeepSeek is inexpensive.

1. Go to **[console.groq.com](https://console.groq.com)** in your browser.
2. Click **Sign up** (you can use Google, GitHub, or email). Confirm your email if asked.
3. Once you're in the console, click **API Keys** in the left sidebar.
4. Click **Create API Key**. Give it a name (e.g. `MacFlow`).
5. The full key will be shown **only once** — it starts with `gsk_` followed by a long random string. **Copy it now.** If you lose it, just make a new one.

Keep the key somewhere safe (a password manager is ideal). You'll paste it into MacFlow in the next step.

### Step 4 — Give MacFlow your API key

1. Click the mic icon in the menubar to open the menu.
2. Click **Settings → Set Groq API Key…**.
3. A floating dialog pops up. Paste your `gsk_...` key into the text field.
4. Click **Save**.

That's it — the key is stored in `~/Library/Application Support/MacFlow/.env` on your Mac. It never leaves your machine except to talk to Groq, and it's never written to any file the app shares over the network.

You can verify the connection works: **Settings → Verify API Connection**. You should see a green "Verified" dialog listing the number of models Groq has available.

**DeepSeek key (for cleanup/rewrite modes):** get a key at **[platform.deepseek.com](https://platform.deepseek.com)**, then add it to the same `.env` file as `DEEPSEEK_API_KEY="sk_..."`. There's no in-app dialog for it yet — edit the file directly (Settings → Open Config File reveals its folder). If you only ever use `raw` mode, no DeepSeek key is needed.

### Step 5 — Grant the three system permissions

The first time you press the hotkey, macOS will ask you to grant permissions. Mac Flow needs **all three** or it can't function. You'll see a separate prompt for each; approve each one.

| Permission | Why Mac Flow needs it |
|---|---|
| **Microphone** | To record your voice |
| **Accessibility** | To simulate `⌘V` and paste your transcribed text into whatever app you're typing in |
| **Input Monitoring** | To detect the global hotkey even when other apps are in focus |

If you miss a prompt or click "Don't Allow" by accident, open **System Settings → Privacy & Security** and you'll find each of the three categories in the sidebar. Turn **MacFlow** on in each pane.

**Important:** after granting these, **quit MacFlow and launch it again**. macOS only re-reads permissions when a process starts. If you skip this step the hotkey will still silently ignore you.

### Step 6 — Try it out

1. Open any app with a text field — Notes, Messages, your browser's address bar, anything.
2. Click in the text field so the cursor is blinking there.
3. **Hold** `Option+Shift+Space`.
4. Say something out loud: *"Hello, this is a test of MacFlow."*
5. **Release** the keys.
6. A moment later your words appear pasted into the text field.

If that worked — you're done. Go dictate some emails.

If nothing happened, jump to **Troubleshooting** below.

---

## Usage

| Action | How |
|---|---|
| Start recording | **Hold** `Option+Shift+Space` |
| Stop & transcribe | **Release** the hotkey |
| Change hotkey | Edit `mac_flow.toml` (see Configuration below) and relaunch |
| Switch microphone | Menubar → Microphone |
| Switch AI mode | Menubar → Mode → Raw / Clean / Rewrite |
| Copy the last transcript | Menubar → Copy Last Transcript |
| Browse history | Menubar → History (click any row to copy it) |
| Toggle macOS banners | Menubar → Settings → Notifications |
| Turn auto-paste off | Menubar → Settings → Auto-paste |

**AI modes:**
- **Raw** — return Whisper's output verbatim. Fastest, no LLM call.
- **Clean** — light cleanup: punctuation, capitalization, remove filler words. Default.
- **Rewrite** — heavier polish. Good for turning "um, so I was thinking maybe we could…" into a cleaner sentence.

---

## Configuration

User settings live in `~/Library/Application Support/MacFlow/mac_flow.toml`. The menubar toggles write directly to this file, but you can edit it by hand too — changes take effect on the next launch.

```toml
[audio]
device_index = -1               # -1 = system default; use --list-mics to see indices
sample_rate  = 16000
channels     = 1

[hotkey]
modifiers = ["ctrl"]            # any of: cmd | ctrl | alt (option) | shift
key       = "space"             # single character or pynput key name

[groq]
whisper_model = "whisper-large-v3"

[deepseek]
base_url = "https://api.deepseek.com"
model    = "deepseek-v4-flash"

[enhancement]
mode = "clean"                  # raw | clean | rewrite

[output]
auto_paste     = true           # paste into the focused field via ⌘V
auto_clipboard = true           # also leave the text on the clipboard
save_history   = true           # write every transcript to history.db

[ui]
notify_on_result = false        # macOS banner after each transcription
```

The Groq API key lives separately in `~/Library/Application Support/MacFlow/.env` so it can never accidentally land in the TOML file or your git history.

---

## Troubleshooting

### "Nothing happens when I hold the hotkey"

Almost always a missing permission. Open **System Settings → Privacy & Security** and check all three of these:

- **Microphone** — MacFlow should be listed and toggled **on**.
- **Accessibility** — same.
- **Input Monitoring** — same.

If MacFlow is missing from any of those panes, launch the app and press `Option+Shift+Space` once inside any text field — macOS should prompt you. If it still doesn't appear, try toggling the app off and back on in each pane.

After any change, **quit MacFlow and launch it again**. Permissions only take effect at process start.

### "macOS says the app is damaged and can't be opened"

This is macOS's Gatekeeper reacting to an unsigned app. Don't move it to Trash — instead:

1. Right-click (or Control-click) `MacFlow.app` in Applications → **Open** → **Open Anyway**.
2. If that still fails, open Terminal and run:
   ```bash
   xattr -cr /Applications/MacFlow.app
   ```
   That clears the quarantine flag and the warning goes away for good.

### "The mic icon stays red forever after I release the hotkey"

Either your network is down, or your Groq API key is invalid. Click the mic → **Settings → Verify API Connection**. You'll get a clear dialog one way or the other.

### "I never got prompted for permissions"

macOS only prompts on the very first attempt. If you'd already declined, the prompt never returns. Remove the MacFlow entry from each pane with the `−` button, then launch the app and press `Option+Shift+Space` — the prompts should come back.

### "I can see the transcript in History, but nothing pasted into my cursor"

This is the classic symptom of **Accessibility being off** for MacFlow — the pipeline records, transcribes, and saves your text, but the synthetic ⌘V keystroke gets silently dropped by macOS because the app isn't trusted to simulate keys. Check in this order:

1. **System Settings → Privacy & Security → Accessibility.** MacFlow must be in the list and toggled **on**. If the toggle is off, flip it on — then **quit and relaunch MacFlow** (macOS only re-reads grants at process start).
2. **Test the paste into a different app.** Open Notes.app or TextEdit, click in a blank document, and dictate something. If it pastes there but not in your original target, the target app is intercepting ⌘V or your hotkey is stealing focus from it. Common culprits: terminal emulators that bind your hotkey combo to a menu or action.
3. **Check that MacFlow is actually the focused paste target.** When you press your hotkey, does the cursor visibly jump somewhere? If yes, another app is catching the same shortcut — try a less-claimed combo (see "Hotkey passthrough" under Known Limitations).

### "It pasted something I copied earlier instead of what I said"

Known rare bug. MacFlow saves your previous clipboard before pasting and restores it ~400 ms later; under unusual timing that restore can race. If it happens to you reliably, please file an issue with the steps.

### "Banners for every transcription are annoying"

Menubar → **Settings → Notifications: off**. (They're off by default on a fresh install — if you're seeing them, something flipped the setting back on.)

### "I granted permissions but the hotkey still does nothing"

The most common cause on a fresh install is that macOS hasn't loaded the grants into the already-running MacFlow process. Fully quit the app (mic icon → **Quit MacFlow**) and launch it again.

If you're still stuck, reach out to whoever shared the app with you — a fresh rebuild will sometimes be needed.

---

## Architecture (short version)

```
mac_flow/
├── main.py               Entry point. --list-mics flag for mic debugging.
├── config.py             TOML loader/writer with deep-merge defaults.
├── paths.py              Resolves file locations (source vs bundled mode).
├── setup.py              py2app build script.
├── mac_flow.toml         Template user config (copied to Application Support on first launch).
│
├── core/
│   ├── engine.py         Orchestrates the pipeline. Owns all workers and threading.
│   ├── recorder.py       sounddevice → CoreAudio mic capture → WAV bytes.
│   ├── transcriber.py    Groq Whisper API call.
│   └── enhancer.py       DeepSeek API call (clean / rewrite modes).
│
├── adapters/
│   ├── base.py           ABCs + factory functions (TextInjector, HotkeyListener).
│   └── macos.py          NSPasteboard + Quartz CGEvent ⌘V; pynput hotkey listener.
│
├── db/
│   └── history.py        SQLite store for transcription history.
│
└── ui/
    └── app.py            rumps menubar app, NSAlert-based settings dialogs.
```

**How text injection works.** Most Mac apps don't expose a "type this text here" API. The reliable trick (used by Wispr Flow, TextExpander, etc.) is: save the current clipboard → write the transcript to `NSPasteboard` → fire a synthetic `⌘V` via `Quartz.CGEventCreateKeyboardEvent` → restore the clipboard ~400ms later. Instant regardless of length, preserves Unicode.

**Threading.** The pynput listener runs its own thread. Each recording spawns a fresh daemon thread for the Groq calls, so network latency never blocks the hotkey. rumps handles main-thread menu callbacks automatically.

---

## Known Limitations

- **Ad-hoc signed binary.** Rebuilding the `.app` invalidates prior Accessibility / Input Monitoring grants. One-time re-grant per build. A Developer ID signature would fix this; not yet in place.
- **Hotkey passthrough.** pynput is a passive listener on macOS — every press of the hotkey still reaches the focused app in parallel with MacFlow. `Option+Shift+Space` (the default) is unclaimed by any common app. If you switch the hotkey, watch out: `Ctrl+Space` triggers Sublime Text's autocomplete (inserts Lorem Ipsum) and macOS's input-source switcher; `Option+Space` is Raycast's launcher; `Ctrl+Shift+Space` moves focus in several terminal emulators. Symptoms of a collision are MacFlow's transcript showing up in your history but not pasting into your cursor, or pasting into the wrong window entirely.
- **First-run permissions dance.** macOS prompts are per-binary. If you run `python main.py` from Terminal during development and then install the `.app`, you'll need to grant the new Python binary *and* the `.app` separately.
- **Internet required.** Transcription happens server-side via Groq. There's no offline/on-device fallback.

---

## For developers — build from source

If you're not receiving a prebuilt copy and want to build `MacFlow.app` yourself, you'll need:

- **Python 3.11+** (Homebrew: `brew install python@3.11`, or pyenv, or the macOS installer)
- **Xcode Command Line Tools** — `xcode-select --install` if you don't have them
- A Groq API key (see **Step 3** above)

### Clone and build

```bash
# 1. Clone the repo
git clone https://github.com/GeneArnold/mac_flow.git
cd mac_flow

# 2. Create a venv and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install py2app              # only needed for building the .app

# 3. Build the .app and install to /Applications
rm -rf build dist
python setup.py py2app
rm -rf /Applications/MacFlow.app
cp -R dist/MacFlow.app /Applications/MacFlow.app

# 4. Launch it
open /Applications/MacFlow.app
```

Then follow **Steps 3 through 6** of the install instructions above to set up your API key and permissions.

### Run from source (no bundle)

For fast iteration while editing code, skip `py2app` and run directly:

```bash
source venv/bin/activate
python main.py
```

In source mode, config and history live inside the repo directory (`./mac_flow.toml`, `./history.db`, `./.env`) instead of Application Support — so you can experiment without touching your production data. Grant Accessibility / Input Monitoring to whatever terminal is running Python (Terminal.app, iTerm2, etc.).

```bash
python main.py --list-mics      # list available input devices with their indices
```

### Rebuild gotcha

Every time you rebuild the `.app`, macOS sees it as a different program (the ad-hoc code signature hash changes) and silently invalidates your previous Accessibility and Input Monitoring grants. After each rebuild:

1. Quit MacFlow (`pkill -9 -f "MacFlow.app/Contents/MacOS"` if it won't quit).
2. Open **System Settings → Privacy & Security → Accessibility**. Remove any existing MacFlow entry with the `−` button.
3. Do the same under **Input Monitoring**.
4. Relaunch `/Applications/MacFlow.app`. macOS will prompt fresh on the next hotkey press.

### Stale bytecode

If Python starts throwing tracebacks that don't match the current source, you've got stale `.pyc` cache:

```bash
find . -name '__pycache__' -not -path './venv/*' -exec rm -rf {} +
```

### Further reading

- `CLAUDE.md` — architecture overview, threading model, adapter layer, py2app gotchas.
- `NOTES.md` — session-by-session history of what was built and why.

---

## License

MIT — same as Linux Flow.
