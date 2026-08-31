# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Name vs. branding

- **`MacFlow`** (no space) is the code-level name. Use it for: the `.app` bundle, `CFBundleName`, `rumps.App(name=…)`, dialog/notification titles, and the `~/Library/Application Support/MacFlow/` data directory.
- **`Mac Flow`** (with space) is the marketing name. Use it for: README prose, NOTES.md narrative, and other human-facing documents — never for filenames, paths, or code identifiers.
- Bundle ID is `com.genearnold.macflow` (space-free from day one; don't change it).

## Running the app

```bash
source venv/bin/activate

# Dev mode — fast iteration, runs against source
python main.py
python main.py --list-mics        # enumerate mic devices

# Production build — creates /Applications/MacFlow.app
rm -rf build dist
python setup.py py2app
rm -rf /Applications/MacFlow.app
cp -R dist/MacFlow.app /Applications/MacFlow.app
```

No test suite or linter config. Deps are pure-pip (`pip install -r requirements.txt`). Mac-only — imports pyobjc, `rumps`, and macOS-specific APIs; it won't run on Linux or Windows.

**Stale `.pyc` cache can cause old bugs to reappear after edits.** If `python main.py` shows a traceback that contradicts the current source, blow away bytecode: `find . -name '__pycache__' -exec rm -rf {} +`.

## The pipeline (read `core/engine.py` first)

Same record → transcribe → enhance → inject pipeline as `linux_flow`:

```
hotkey press (pynput thread) → Recorder.start() → on_recording_start UI cb
hotkey release               → spawn daemon thread → _process():
   Recorder.stop() → Transcriber (local mlx-whisper OR Groq Whisper) → Enhancer (Ollama/DeepSeek/OpenRouter/Groq, if mode != "raw")
   → TextInjector.inject() (NSPasteboard + ⌘V) or copy_to_clipboard() → db.save()
```

Three threading realms — same rules as Linux, with one twist:
1. **pynput thread** — `_on_press` / `_on_release`. No blocking, no network.
2. **Per-recording daemon thread** — `_process()`. Groq latency lives here.
3. **Main / rumps thread** — all UI work. Worker threads CAN safely set `app.title`, `app.icon`, and call `rumps.notification()` — rumps is thread-tolerant for those. **But they CANNOT create NSAlert / NSWindow** (see "Dialogs" below).

Whisper hallucinations on silence are filtered by `_WHISPER_HALLUCINATIONS` in `engine.py`.

## Notifications — route through `_notify()`

Every macOS banner goes through `MacFlowApp._notify(title, subtitle, message)`. That helper is the single gate — it checks `cfg["ui"]["notify_on_result"]` and returns early when the user has banners off. **Never call `rumps.notification()` directly**; add new notification points through `_notify()` so the toggle remains authoritative.

The toggle lives at **Settings → Notifications: on/off** (`notify_on_result` in `mac_flow.toml`). Default is `False` — we're quiet by default and the user opts in.

Paste happens in `engine._process()` *before* `on_result` fires, so notifications can never block paste. Don't restructure that ordering.

## Dialogs — do not use `rumps.alert`

`rumps.alert()` opens an NSAlert at the default window level, which means on multi-monitor setups (or any time another window is frontmost) it hides behind other windows and freezes the entire menubar app with an invisible modal. User hit this bug repeatedly.

**Use the helpers in `ui/app.py` instead:**
- `_front_alert(title, message)` — info/confirmation dialog with OK button. Builds NSAlert directly via pyobjc and sets `NSFloatingWindowLevel` before `runModal()`, so it always floats above everything.
- `_front_window(title, message, ...)` — text-input dialog with OK/Cancel and a result object with `.clicked` (bool) and `.text` (str). Same floating-level fix.

**Never call these from a background thread.** NSWindow creation off the main thread throws `NSInternalInconsistencyException` and kills the calling thread. If you need to show a dialog after a background task completes, either:
- Make the whole operation synchronous on the main thread (acceptable for short calls like Groq `models.list()` which takes ~1s — this is what `_verify_api` does), or
- Schedule the dialog on the main thread via `rumps.Timer(cb, 0.01).start()`.

## Adapter layer

`adapters/base.py` defines `TextInjector` and `HotkeyListener` ABCs. Only `adapters/macos.py` implements them. Engine/UI code imports `get_injector()` / `get_hotkey_listener()` — never the concrete classes.

`MacInjector.inject()` uses the clipboard-paste trick: save previous clipboard → write transcript → synthetic ⌘V via `CGEventCreateKeyboardEvent` → restore previous clipboard after `_CLIPBOARD_RESTORE_DELAY_S` (400ms). Instant for any text length, preserves Unicode. Don't replace with `pynput.type(text)` — it's slow and breaks on non-ASCII.

## Paths — source vs bundle

`paths.py` is the single source of truth. Detects runtime mode via `sys.frozen == "macosx_app"`:

| What | Source mode (`python main.py`) | Bundled mode (`.app`) |
|---|---|---|
| Config + .env + history.db | `~/WorkSpace/mac_flow/` (next to script) | `~/Library/Application Support/MacFlow/` |
| Assets (PNG icons) | `~/WorkSpace/mac_flow/assets/` | `.../MacFlow.app/Contents/Resources/assets/` |

`paths.ensure_defaults()` runs in `main.py`; in bundled mode it copies the template `mac_flow.toml` and `.env.example` into Application Support on first launch. **All file paths in the codebase should route through `paths.data_dir()` or `paths.assets_dir()`** — never use `Path(__file__).parent` directly, it breaks in the bundle.

## py2app build gotchas

- `sounddevice` ships `libportaudio.dylib` inside `_sounddevice_data/`. The dylib **cannot** be loaded from inside `python311.zip`, so `setup.py` lists it under `frameworks:` to extract it into `Contents/Frameworks/`. If you ever see `OSError: PortAudio library not found` from the bundled app, that's what broke.
- `_sounddevice_data` is also listed as a `package` so the Python-side wrapper stays intact.
- Local modules (`paths`, `config`, `core.*`, `adapters.*`, `db.history`, `ui.app`) must all be in the `includes` list or py2app's modulegraph may miss them.
- `LSUIElement = True` means menubar-only (no Dock icon while running). Flip to `False` if you want a Dock presence.

## Providers — switchable (`config.py`)

Both pipeline stages are provider-switchable via `mac_flow.toml`, so the app can run fully local or fall back to cloud without code changes:

- **Transcription** (`[transcription] provider`): `"mlx"` = local Whisper via `mlx-whisper` (Metal, offline, no key, model downloaded to HF cache on first use) or `"groq"` = Groq Whisper API. `core/transcriber.py` has one class per backend plus `get_transcriber(cfg)`; the engine only touches the factory. Local model is warmed up in a background thread by `Engine._warm_up_transcriber()` so the first dictation isn't blocked by the ~1.6GB download.
- **Enhancement** (`[enhancement] provider`): any OpenAI-compatible chat endpoint — `"ollama"` (local, default), `"deepseek"`, `"openrouter"`, `"groq"`. Connection details (base_url, key env var, default model) live in `config.ENHANCE_PROVIDERS`; the TOML only names a provider. A single `Enhancer` (openai SDK + custom `base_url`) drives all of them. `model = ""` means "use the provider's default from the registry".
- **Default is 100% local** (`mlx` + `ollama`) — needs no API key and works offline.

## Config & secrets (`config.py`)

- `_DEFAULTS` is the source of truth for every key. Add new settings there first — on-disk TOMLs merge on top, so missing keys silently get the default.
- API keys (`GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`) live exclusively in `.env` (gitignored), never in TOML. `config.set_api_key(env_name, value)` routes to `_save_env_key()`. `load()` surfaces them as `cfg["keys"][ENV_NAME]`.
- `config.load()` re-reads from disk every call. After any settings change, call `engine.reload()` to rebuild all workers.
- In-code default hotkey is **Option+Shift+Space** (`_DEFAULTS["hotkey"] = {"modifiers": ["alt", "shift"], "key": "space"}`). The user's live `mac_flow.toml` currently matches. This combo survived the Session-4 collision cascade (Ctrl+Space → Sublime/input-source, Option+Space → Raycast, Ctrl+Shift+Space → terminal focus-steal, Ctrl+Shift+D → character-key case-match bug in the listener). When changing defaults, remember the on-disk TOML wins, so bumping this only affects fresh installs.

## History DB

SQLite at `history.db` in `paths.data_dir()`. `_ensure_init()` runs at import time so the UI can read history before `Engine.start()` is called. The History submenu in the menubar is click-to-copy: every entry, when clicked, copies its text to the clipboard (via `MacInjector.copy_to_clipboard`).

## TCC grants and rebuilds

macOS keys Accessibility + Input Monitoring grants to `(bundle path, adhoc codesign hash)`. Every `py2app` rebuild changes the hash, so the freshly-copied `.app` looks like a different program to the OS and keystrokes stop reaching the hotkey listener silently.

After every rebuild the user must: (1) remove the old "MacFlow" entry from both privacy panes, (2) relaunch `/Applications/MacFlow.app`, (3) accept the prompt on first hotkey press, (4) quit and relaunch. The right permanent fix is a Developer ID signature; out of scope for v0.

## Known open items (see NOTES.md)

- Custom dictionary / vocabulary not yet designed (Whisper `prompt` param vs post-processing TBD)
- Occasional wrong-content paste — investigate clipboard save/restore race in `adapters/macos.py`
- About dialog content is right but visually plain
- `rumps.Timer(self._prompt_for_api_key, 0.5).start()` is a *repeating* timer that happens to only fire once because the condition flips after first fire — clean this up
- No signed distribution yet; others must clone and build to install

## Things to be careful with

- The paired `linux_flow` repo at `~/WorkSpace/linux_flow` has the same core files. Changes to `core/recorder.py`, `core/transcriber.py`, `core/enhancer.py`, `db/history.py` often make sense to port across. The adapter and UI layers should NOT be unified — they're intentionally platform-specific.
- Enhancement failures fall back to raw Whisper output. Preserve that behaviour.
- `xdotool` is replaced by NSPasteboard+⌘V; the engine still falls back to clipboard if injection fails. Maintain that invariant.
