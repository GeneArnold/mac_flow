# Mac Flow — Session Notes

## Session 1 — Initial port (2026-04-15)

### What was built

Fresh port of Linux Flow to macOS (Apple Silicon). New GitHub repo at
**github.com/GeneArnold/mac_flow** (public), distinct from `linux_flow`
because GitHub doesn't allow forking your own repo into your own account.

Core pipeline reused verbatim from Linux Flow — only the platform layer
was rewritten.

| Layer | Linux Flow | Mac Flow |
|---|---|---|
| Text injection | `xdotool type --clearmodifiers` | NSPasteboard + synthetic ⌘V via `Quartz.CGEventCreateKeyboardEvent` |
| Hotkey listener | pynput (X11 passive grab) | pynput (CGEventTap — same class, macOS backend) |
| Menubar | pystray + AppIndicator3 in a **GTK3 subprocess** | `rumps` (native NSStatusItem, same process) |
| Settings UI | Adw.ApplicationWindow (GTK4 + libadwaita) | `rumps.alert` / `rumps.Window` (NSAlert under the hood) |
| Autostart | `.desktop` file + `install.sh` | LaunchAgent plist + `install.sh install` |
| Default hotkey | `Ctrl+Space` | `Option+Space` (default — user changed to `Ctrl+Space` in session) |

Shared untouched: `core/recorder.py`, `core/transcriber.py`,
`core/enhancer.py`, `db/history.py`, `config.py` pattern (deep-merge +
`.env` for the API key).

### What was confirmed working end-to-end

- Ctrl+Space hold-to-record
- MacBook internal mic capture via sounddevice/CoreAudio
- Groq Whisper transcription
- Clean-mode Llama enhancement
- NSPasteboard + ⌘V inject into an active text field
- Menubar state transitions: 🎙 → 🔴 → ✨ → 🎙
- `.env`-sourced Groq key (never hits TOML)
- Groq SDK auth, 18 models visible

### Permissions granted (on this machine)

- **Accessibility** → `Terminal.app`. That grant is inherited by any
  `python main.py` run from Terminal. Switching launcher (iTerm,
  LaunchAgent, bundled `.app`) will need a **fresh grant** for the new
  parent binary — macOS tracks the executable, not the app name.
- Microphone: prompted automatically on first record, granted.
- Input Monitoring: not required for pynput on this macOS build — the
  "not trusted" error text said *accessibility* and clearing that alone
  fixed it.

### Gotchas hit during this session

1. **"Input Monitoring not in the list":** On macOS 26.3 the category
   only appears after something has requested that permission. Since
   pynput on this build goes through Accessibility only, it never
   appeared — and that's fine.
2. **"App hung when I clicked Hotkey":** The menubar Hotkey item opens
   a `rumps.alert`, which runs an NSAlert modal. If the alert appears
   behind another window, the app looks frozen because the menubar is
   blocked until it's dismissed. Workaround was Cmd+Tab to find it;
   real fix is a proper hotkey-capture dialog (see next-steps).
3. **"System crashed, can't kill":** Actually was a stuck/hidden modal,
   not a crash. `kill -9 <pid>` from a second terminal works. Also
   `pkill -9 -f "python main.py"`.
4. **Ctrl+Space vs macOS input-source switching:** macOS's
   "Select the previous input source" shortcut defaults to Ctrl+Space.
   Harmless with one input source; if you add a second language, the
   hotkey will double-fire. Fix: System Settings → Keyboard →
   Keyboard Shortcuts → Input Sources → uncheck that item, or pick a
   combo like `ctrl+shift+space`.

### Files in this repo

```
mac_flow/
├── main.py               # entry + --list-mics flag
├── config.py             # TOML + .env loader (api_key never in TOML)
├── mac_flow.toml         # user settings
├── requirements.txt      # groq, sounddevice, pynput, rumps, pyobjc-*
├── install.sh            # LaunchAgent install/uninstall
├── .env                  # gitignored; holds GROQ_API_KEY
├── .env.example          # template
├── core/
│   ├── engine.py         # orchestrator — threading realms + callbacks
│   ├── recorder.py       # sounddevice mic capture → WAV bytes
│   ├── transcriber.py    # Groq Whisper
│   └── enhancer.py       # Groq Llama (clean/rewrite)
├── adapters/
│   ├── base.py           # ABCs + get_injector() / get_hotkey_listener()
│   └── macos.py          # MacInjector + MacHotkeyListener
├── db/history.py         # SQLite history
└── ui/app.py             # rumps MacFlowApp
```

### Threading model (unchanged from Linux)

1. **pynput listener thread** — fires `_on_press` / `_on_release`. No
   network or blocking work allowed here.
2. **Per-recording daemon thread** — runs `Engine._process()`. Groq
   latency lives here.
3. **Main / rumps thread** — menu callbacks, NSAlert modals, menubar
   title updates. Worker threads set `app.title` directly; rumps is
   thread-tolerant enough for that.

### Security cleanup still pending

- `linux_flow/groq_api_key.txt` is a loose key file in the linux_flow
  working tree. Never committed, but I added safety patterns to
  `linux_flow/.gitignore` (unstaged — needs user commit). **Delete that
  file once the key is confirmed live in `mac_flow/.env`.**
- If the key ever feels stale/leaked: rotate at console.groq.com →
  API Keys → delete + regenerate → update both `.env` files.

---

## Next session — "make it a real app"

Stated goal: no more `python main.py` from a terminal. Needs to:
- Launch at login automatically.
- Have a launchable icon (Spotlight / Launchpad / Applications folder).
- Run in the background — no terminal window attached.
- Match the quality-of-life of Linux Flow on GNOME.

### Plan

1. **Bundle with `py2app`** into a proper `.app` at `/Applications/Mac Flow.app`.
   - Adds `pip install py2app` to dev deps.
   - `python setup.py py2app` produces a standalone bundle with the
     venv's Python embedded. No more `python main.py`.
   - Info.plist keys to set:
     - `CFBundleIdentifier = com.genearnold.macflow`
     - `CFBundleName = Mac Flow`
     - `CFBundleShortVersionString` = match `config._DEFAULTS["app"]["version"]`
     - `NSMicrophoneUsageDescription` = "Mac Flow needs microphone access to transcribe your voice."
     - `LSUIElement = 1` if we want menubar-only (no Dock icon while running — typical for menubar apps)
     - `LSUIElement = 0` if user wants a persistent Dock icon (user's phrasing suggests they want this — confirm)
2. **App icon.** Reuse / resize `linux_flow/assets/linux-flow-icon.png`
   → `.icns` via `iconutil` or `sips`. Drop in
   `mac_flow/assets/mac_flow.icns` and reference in setup.py.
3. **Login item** — two options:
   - **Simple:** after install, have user right-click the app in the
     Dock → Options → *Open at Login*. Zero code.
   - **Programmatic:** `SMAppService.mainAppService.register()` (macOS
     13+) via pyobjc, fired by a Settings toggle. More polish, more code.
4. **Retire `install.sh`** (or keep it as a dev-mode fallback). A
   bundled `.app` replaces the LaunchAgent approach.
5. **Permissions story post-bundle:** the bundled app's binary is the
   new thing to trust in Accessibility. The user will need to grant
   that once, separately from the Terminal grant. Document in README.

### Nice-to-haves discovered during testing

- Proper **"Change Hotkey…"** capture dialog (right now the menu item
  just shows informational text — a modal trap in disguise).
- Better **hotkey collision hint** — detect when the current combo
  matches a known macOS shortcut (Ctrl+Space = input-source switch) and
  warn in the menubar.
- **Per-app disable** (pause recording while a specific bundle ID is
  frontmost — e.g. don't inject into 1Password).
- **Waveform or mic-level feedback** — Linux has a floating GTK
  overlay. On Mac the equivalent is probably a brief menubar title
  animation or a tiny NSWindow. Low priority; icon state is enough for v0.

### Pointers for whoever picks this up

- `adapters/macos.py:_send_cmd_v` — keycode 9 is 'v'. If injection
  fails silently, first check Accessibility grants for the parent
  binary; next verify pasteboard write succeeded before the CGEvent.
- `ui/app.py:_prompt_for_api_key` — when no key is set at startup,
  schedules a repeating `rumps.Timer` that fires after 0.5s. It's
  *repeating* (rumps API quirk) but only starts when no key is set, so
  it only fires once in practice. Still worth tidying.
- Engine callbacks (`on_recording_start` etc.) fire from worker
  threads — rumps is thread-tolerant for title assignment and
  `rumps.notification`, but NOT for opening NSAlert. Never pop an
  alert from a worker thread; marshal to main via
  `rumps.Timer(cb, 0.01).start()` or similar.

---

## Session 2 — Icons, dialogs, app bundle, QA (2026-04-16)

### What was done

**Menubar icons** — replaced emoji characters (🎙/🔴/✨) with proper
macOS template images. Three PNGs in `assets/`:
- `mic_idleTemplate.png` — black mic, macOS renders white (template)
- `mic_recording.png` — red mic (non-template, keeps red color)
- `sparkleTemplate.png` — black 4-point star, renders white (template)

All confirmed working: white mic → red mic → white sparkle → white mic.

**Dialog fix (hidden-modal freeze)** — `rumps.alert` opens an NSAlert
modal at default window level. On multi-monitor setups it appeared
behind other windows, freezing the menubar with no visible dialog.
Fixed by building NSAlert directly via pyobjc and setting
`NSFloatingWindowLevel` before `runModal()`. Helper functions
`_front_alert()` and `_front_window()` in `ui/app.py`.

**Menu layout changes:**
- Hotkey item moved under Status (both grey/non-interactive, grouped)
- Hotkey item made non-clickable (`set_callback(None)`)
- History changed from a single alert dump to a **submenu**: each
  entry shows timestamp + preview, clicking copies full text to
  clipboard. Clear All button at bottom. Auto-refreshes after each
  transcription.
- Added "Auto-clipboard" toggle in Settings (default on). Controls
  whether text is automatically copied to clipboard. If both
  auto-paste and auto-clipboard are off, text only lives in history.
- About dialog now dynamically shows both models from config
  (Speech-to-text and Enhancement) instead of hardcoded text.
- Verify API changed from background thread + notification to
  synchronous main-thread call + `_front_alert` dialog. Shows clear
  "Verified" or error message with OK button.

**App bundle** — built with py2app into a standalone `.app`:
- `setup.py` configures py2app with icon, Info.plist, data_files
- `paths.py` module handles path resolution for both source mode
  (`python main.py` → files next to script) and bundled mode
  (`.app` → `~/Library/Application Support/Mac Flow/`)
- App icon: blue gradient rounded-rect with white mic, generated as
  `.icns` via `iconutil`
- PortAudio dylib extracted from zip into Frameworks/ so sounddevice
  can load it
- Installed to `/Applications/Mac Flow.app` — launchable from
  Spotlight, Launchpad, Finder
- `LSUIElement = True` — menubar-only, no Dock icon while running
- User config/data lives in `~/Library/Application Support/Mac Flow/`
  (mac_flow.toml, .env, history.db)

**Build command:**
```bash
source venv/bin/activate
rm -rf build dist
python setup.py py2app
cp -R "dist/Mac Flow.app" "/Applications/Mac Flow.app"
```

### What was confirmed working

- All three icon states (idle/recording/processing)
- Copy Last Transcript
- Mode switching (Raw/Clean/Rewrite)
- Hotkey as non-clickable label
- History submenu with click-to-copy
- Clear All History
- Auto-paste toggle (on/off)
- Auto-clipboard toggle
- API key entry dialog (floats above all windows)
- Verify API (shows success/failure dialog)
- About (dynamic model names)
- Open Config File (reveals in Finder)
- Quit
- .app bundle launches from /Applications

### Known issues to fix next session

1. **Notifications are too aggressive.** Every transcription triggers a
   macOS notification. User wants control over these — either reduce
   them, make them optional, or remove most of them. The
   `notify_on_result` toggle exists but may not cover all the
   notification points. Audit every `rumps.notification()` call and
   decide which are truly needed vs. noisy.

2. **Stale .pyc cache causes old errors.** After editing source files,
   Python sometimes runs stale bytecode. Add `find ... -name
   '__pycache__' -exec rm -rf {} +` to the build script or a
   `clean.sh` helper.

3. **Accessibility permission re-grant for .app.** When switching from
   `python main.py` (Terminal) to `Mac Flow.app`, the user must
   re-grant Accessibility to the new binary. This is a one-time thing
   per binary but confusing. Document clearly.

4. **About dialog needs cosmetic polish.** Content is correct (version,
   both models, attribution) but the NSAlert layout is plain. Consider
   a custom NSWindow with proper formatting, logo, links.

5. **Dictionary / custom vocabulary.** User wants to discuss building a
   dictionary — likely a personal word list that gets fed to Whisper's
   `prompt` parameter or used for post-processing corrections (e.g.
   "MacFlow" → "Mac Flow", company names, jargon). This is a new
   feature, not a bug fix. Needs design discussion.

6. **Auto-paste sometimes pastes wrong content.** User reported that
   Mac Flow pasted something unintended — possibly a race condition
   between clipboard save/restore and the user's own copy actions, or
   the 400ms restore delay is too short/long. Investigate the clipboard
   lifecycle in `adapters/macos.py`.

### Rebuild workflow

```bash
cd ~/WorkSpace/mac_flow
source venv/bin/activate

# Dev mode — edit and test quickly
python main.py

# Build .app after changes are stable
rm -rf build dist
python setup.py py2app
rm -rf "/Applications/Mac Flow.app"
cp -R "dist/Mac Flow.app" "/Applications/Mac Flow.app"

# Then re-grant Accessibility to Mac Flow.app if first time
```

### Next session priorities (user-stated)

1. **Refine the app** — general QA, fix rough edges
2. **Control notifications** — reduce/eliminate unwanted macOS notifications
3. **Dictionary feature** — discuss and design custom vocabulary support

---

## Session 3 — Notifications off by default + rename to MacFlow (2026-04-21)

### What was done

**Notifications audit (closes Session 2 issue #1).** Every
`rumps.notification()` call in `ui/app.py` now routes through a single
`_notify(title, subtitle, message)` helper on `MacFlowApp`. The helper
short-circuits when `cfg["ui"]["notify_on_result"]` is `False`, so
*nothing* hits `UNUserNotificationCenter` when the user has banners off.

- Added **Settings → Notifications: on/off** menu toggle (mirrors the
  existing auto-paste / auto-clipboard / save-history toggles).
- Flipped the in-code default `notify_on_result` from `True` → `False`
  in `config._DEFAULTS`. Also flipped the shipped `mac_flow.toml` and
  the live user copy in `~/Library/Application Support/MacFlow/` so the
  change took effect without a reinstall.
- `_on_error` still prints to stderr unconditionally — errors are never
  lost even with banners off.

Confirmed: the paste happens inside `engine._process()` *before* the
`on_result` callback fires, so gating `rumps.notification()` never
blocks the paste. If paste ever fails, it's not the notifications
(Session 2 issue #6 is still open — clipboard save/restore race in
`adapters/macos.py`).

**Renamed `Mac Flow.app` → `MacFlow.app`.** The space in the bundle
name was causing friction (shell quoting, TCC grant churn on every
rebuild). User decision: "Mac Flow" is the marketing name (README,
docs, prose); `MacFlow` is the code-level name everywhere the OS sees
it.

Changes:
- `setup.py`: `CFBundleName`, `CFBundleDisplayName`, `setup(name=…)`,
  permission usage descriptions.
- `ui/app.py`: `rumps.App(name=…)`, all dialog/notification titles,
  "Quit MacFlow" menu item.
- `paths.py`: `~/Library/Application Support/MacFlow/`.
- `main.py`, `core/engine.py`: docstring/comment references.
- `install.sh`: console echo strings (LaunchAgent plist label
  `com.genearnold.mac_flow` left as-is — reverse-DNS, not display).
- `CLAUDE.md`: build commands and path table.

Bundle identifier `com.genearnold.macflow` was already space-free, so
TCC identity didn't change on that axis. But the adhoc signature hash
changes on every py2app run, which invalidates prior Accessibility /
Input Monitoring grants — the user had to remove stale entries and
re-grant once.

**User data migration.**
```bash
mv "~/Library/Application Support/Mac Flow" "~/Library/Application Support/MacFlow"
```
Preserved `history.db` (49KB of transcripts), `.env`, and
`mac_flow.toml` with user's Ctrl+Space + raw-mode settings.

**Rebuild workflow (updated, no space):**
```bash
cd ~/WorkSpace/mac_flow
source venv/bin/activate
find . -name '__pycache__' -not -path './venv/*' -exec rm -rf {} +
rm -rf build dist
python setup.py py2app
rm -rf /Applications/MacFlow.app
cp -R dist/MacFlow.app /Applications/MacFlow.app
```

**Docs overhaul for sharing.** After the rename and QA pass, user asked
to get the app ready to hand to someone else. Three docs touched:

- `CLAUDE.md` — added "Name vs. branding" section at the top (code =
  `MacFlow`, prose = `Mac Flow`, bundle ID = `com.genearnold.macflow`
  and never changes), a "Notifications — route through `_notify()`"
  section, and a "TCC grants and rebuilds" section documenting why
  permissions break on every `py2app` rebuild. Fixed the default-hotkey
  line (in-code default is Option+Space; user's on-disk TOML is
  Ctrl+Space). Pruned "Known open items" of the notifications bullet.
- `NOTES.md` — this file; Session 3 entry.
- `README.md` — rewritten for a non-developer audience who just
  receives a prebuilt `.app`. Structure:
  1. Requirements (macOS + Groq key only — no Python for end users).
  2. **Install — if someone sent you `MacFlow.app`**: six numbered
     steps covering drag-to-Applications, Control-click → Open
     Gatekeeper bypass, Groq account signup with key creation (explicit
     that the developer does **not** share their key), pasting the key
     via Settings → Set Groq API Key, granting Microphone +
     Accessibility + Input Monitoring, and a first-test cycle.
  3. Usage table (hotkey, microphone selection, AI modes,
     notifications toggle, auto-paste toggle).
  4. Configuration (full TOML reference, including the `auto_clipboard`
     and `notify_on_result` keys that were missing from the old README).
  5. Troubleshooting — seven common failure modes with fixes:
     permissions, "damaged app" Gatekeeper reaction (including
     `xattr -cr` nuclear option), stuck-red mic icon, never-got-prompted
     recovery, wrong-content paste (flagged as known bug), banner
     annoyance, post-grant relaunch requirement.
  6. Architecture (short version) — kept mostly as-is.
  7. **For developers — build from source** — the clone + `py2app`
     flow moved here, below the end-user path. Also covers
     `python main.py` dev mode, the rebuild re-grant dance, and
     `__pycache__` cleanup.

**Distribution recommendation noted for the user:** zip the bundle
with `ditto -c -k --sequesterRsrc --keepParent /Applications/MacFlow.app
MacFlow.app.zip` rather than Finder's compress. Finder's compress can
mangle the bundle resource structure and amplify "damaged" errors on
the recipient's Mac.

### What was confirmed working

- End-to-end dictation into a text field: `This is a test of Macflow.`
  pasted cleanly on first try after re-granting permissions.
- Silent operation — no notification banners with `notify_on_result =
  false` and macOS-level notifications re-enabled for the app.
- User data migration preserved history — nothing lost in the rename.
- README, CLAUDE.md, and NOTES.md all consistent on the `MacFlow` vs.
  "Mac Flow" split and the permission flow.

### TCC grant lifecycle (documented for future sessions)

macOS keys Accessibility / Input Monitoring grants to the
`(bundle path, adhoc codesign hash)` tuple. **Every `py2app` rebuild
changes the hash**, so the OS treats the freshly-copied `.app` as a
different program and silently ignores old grants — keystrokes stop
reaching the hotkey listener with no error surfaced.

**Workaround after each rebuild:**
1. Kill all running instances (`pkill -9 -f "MacFlow.app/Contents/MacOS"`).
2. System Settings → Privacy & Security → Accessibility → remove any
   "MacFlow" entry with the `−` button.
3. Same in Input Monitoring.
4. Relaunch `/Applications/MacFlow.app`. macOS prompts on first
   hotkey press → toggle on in both panes.
5. Quit and relaunch — macOS only re-reads grants at process start.

**Real fix** (not yet done): Apple Developer ID + proper codesign +
notarisation. Then the signed identity is stable across rebuilds and
TCC honours the existing grant. Out of scope for v0.

### Still open

1. **Wrong-content paste** (Session 2 #6). Not reproduced this session
   but the 400ms clipboard-restore delay is still a suspect.
2. **Dictionary / custom vocabulary** (Session 2 #5). Not designed.
3. **About dialog polish** (Session 2 #4).
4. **One-shot repeating Timer** for API-key prompt (Session 2
   pointers). Still there; low priority.
5. **Developer ID signing + notarisation.** Would eliminate the
   Gatekeeper "damaged" warning on recipients' Macs *and* stop TCC
   grants from invalidating on every rebuild (both consequences of
   ad-hoc signing). Requires a paid Apple Developer account. Out of
   scope for v0.
6. **Validating the install flow with a real recipient.** The README
   is written from a reasonable guess at what a first-time recipient
   needs — but it hasn't been tested by anyone except the author.
   See next-session priorities.

### Next session priorities (user-stated)

**Primary task: first real external test of the sharing workflow.**
The next time we pick this up, the user plans to hand the `.app` to
someone else (friend / colleague), watch them try to install it using
only the README, and capture what goes wrong or feels unclear.

Specific things to validate on a second Mac:

- Does `ditto -c -k --sequesterRsrc --keepParent` produce a zip that
  unpacks cleanly on the recipient's machine? (vs. Finder's compress,
  which has historically caused trouble.)
- Does the **Step 2 Gatekeeper bypass** as written actually work on
  the recipient's macOS version? macOS 15+ moved the "Open Anyway"
  button behavior around — the README covers both the context-menu
  path and the System Settings fallback, but real-world behavior may
  need a clearer branching explanation.
- Do **Steps 3–4 (Groq signup + API key)** feel obvious to someone
  who has never used Groq? Is the path through console.groq.com →
  API Keys → Create still what the UI actually shows today (Groq
  updates their console; screenshots may age fast)?
- Does the **three-permission prompt sequence** actually fire in
  order as described? macOS's prompting order can vary depending on
  which system API is hit first — if the recipient sees a different
  order than the README describes they may panic.
- Does **Step 6 "Try it out"** produce a paste on the first attempt?
  If not, which troubleshooting entry do they reach for, and does it
  resolve their specific symptom? The wording of each troubleshooting
  heading is meant to match how a user would search — worth
  validating.
- Notifications default: does the recipient ever want to turn them
  on? If yes, the README doesn't currently explain *why* they're off
  by default — might be worth a sentence.

**After that test, the docs get a revision pass based on actual
feedback** (unclear steps rewritten, screenshots added for the places
words aren't enough, missing troubleshooting entries added). Only
*then* do we consider returning to the open-items list above
(dictionary, About polish, clipboard race).

**Deferred indefinitely until distribution matters more:** Developer
ID signing. Worth doing only if sharing broadens past a small circle
of testers.

---

## Session 4 — Hang fix, audio preservation, Ctrl+Space/Sublime collision diagnosed (2026-04-24)

### The user's report

After using MacFlow successfully for several dictations, the app
*appeared* to hang and auto-paste stopped working. Worst of all, when
pressing the hotkey in Sublime Text, **Lorem Ipsum** started appearing
in their document. User was concerned about losing long dictations
("I may go on a rant and talk into macflow thinking it is going to
work…") and wanted safeguards before any more testing.

### Root causes (two separate issues, only the second is a real bug)

**1. No timeout on the Groq client → genuine hang risk.** Both
`Transcriber` and `Enhancer` constructed `Groq(api_key=…)` with no
`timeout` argument. When an HTTP request stalled (flaky network,
stale TCP keepalive, slow server), the worker thread blocked on the
socket read indefinitely. The engine's `except` clause never fired
because no exception was raised. The sparkle icon stayed on forever
and the only recovery was force-quit.

**Fix:** `Groq(api_key=api_key, timeout=30.0)` in both
`core/transcriber.py` and `core/enhancer.py`. On timeout the SDK
raises, the engine catches, `on_error` fires, icon resets.

**2. Ctrl+Space collides with Sublime Text's autocomplete — the
actual source of the "Lorem Ipsum" and the "app is hung" perception.**
pynput on macOS is a *passive* hotkey listener — the OS delivers
every Ctrl+Space press to MacFlow *and* to the focused app in
parallel. In Sublime Text, Ctrl+Space opens the Auto Complete menu;
Sublime ships a built-in `lorem` snippet that's alphabetically first
in many syntaxes, so holding Ctrl+Space:

- MacFlow starts recording.
- Sublime silently opens autocomplete with `lorem` highlighted.
- User speaks, releases the keys.
- Sublime commits the completion → Lorem Ipsum appears in the document.
- MacFlow finishes transcribing, does ⌘V → pastes the real transcript
  *on top of* the Lorem Ipsum.
- Result: a messy document and the impression that MacFlow pasted
  garbage or didn't work.

No code was broken. `history.db` rows 60–62 from this session show
every one of those "failed" dictations was actually transcribed
correctly and saved to history — the paste just landed somewhere
weird because of the Sublime collision.

**Fix:** switched the user's hotkey from Ctrl+Space to Option+Space
in both `~/Library/Application Support/MacFlow/mac_flow.toml` and
the repo template `mac_flow.toml`. Option+Space is unbound on stock
macOS and doesn't collide with Sublime's autocomplete. Matches the
in-code default in `config._DEFAULTS` — we're now consistent.

### Features added while pursuing the hang (kept, even though the
actual hang turned out to be a hotkey collision)

These are all real improvements regardless of the Sublime red
herring, and belong in the codebase:

**Audio preservation on transcribe failure.** When the Groq
Whisper call raises (now possible because of the 30s timeout, or
any other transcribe error), the WAV is saved to
`~/Library/Application Support/MacFlow/pending/recording-YYYYMMDD-HHMMSS.wav`
before `on_error` fires. Pending files survive app restarts and
reboots — nothing is lost.

- `paths.py` grew a `pending_dir()` helper.
- `core/engine.py` — refactored `_process()` into two stages:
  `_process()` handles mic-stop, `_process_wav(wav, duration, is_retry)`
  is the shared pipeline for fresh recordings and retries. Added
  `has_pending_wav()` and `retry_last()` public methods, plus
  `_save_pending_wav`, `_newest_pending_wav`, `_delete_pending_wav`
  helpers.
- `ui/app.py` — **Settings → Retry Last Recording** menu item.
  If there's a pending WAV on disk, it re-runs the pipeline through
  `_process_wav(wav, 0.0, is_retry=True)`. On success the WAV is
  deleted; on failure it stays so the user can try again later. If
  no pending WAV exists, the menubar status shows
  "No failed recording to retry" for 4 seconds then clears.

**Visible errors in the status bar.** Since notifications default to
off, `_on_error` now sets `self._status_item.title = "Status: Error —
<message>"` (truncated to 80 chars), then a one-shot
`threading.Timer(6.0, self._clear_error_status)` reverts it to
"Status: Ready" unless a new recording is already in progress.
`_clear_error_status` checks against a list of busy-state prefixes
(Ready, Recording, Processing, Retrying, Verifying) so it doesn't
clobber an in-flight status.

**Clear All History confirmation.** Added `_front_confirm(title,
message, ok, cancel)` helper in `ui/app.py` (twin of `_front_alert`,
but with two buttons and a bool return). Reused the
`NSFloatingWindowLevel` fix so the dialog can't hide behind other
windows. `_clear_history` now pops
"Clear All History? This permanently deletes every saved transcript.
This cannot be undone." with Clear All / Cancel buttons. Nothing
happens unless the user explicitly clicks Clear All.

### Debug tools used

Key diagnostic move was reading `history.db` directly:
```bash
python -c "
import sqlite3, pathlib
db = pathlib.Path.home() / 'Library/Application Support/MacFlow/history.db'
for row in sqlite3.connect(db).execute(
    'SELECT created_at, SUBSTR(final_text, 1, 80) FROM history ORDER BY id DESC LIMIT 5'
):
    print(row)
"
```
Showed three successful transcriptions while the user thought the
app was hung — instantly disproving "hang" and pointing at the
paste-destination problem.

Also `pbpaste | head -c 200` to confirm system clipboard was clean
(no MacFlow residue). And `ls ~/Library/Application Support/MacFlow/pending/`
to confirm no WAVs were stashed — meaning transcription never actually
failed in this session.

**Lesson for next session:** before assuming the pipeline broke,
check `history.db` for a fresh row. If the row is there and the
user didn't see the text, the problem is in *where the paste
landed*, not *whether the paste happened*.

### Permission re-grant toll

This session required re-granting Accessibility + Input Monitoring
**twice** (once per rebuild: the timeout fix, then the preservation
+ error visibility + confirm dialog bundle). Each rebuild invalidates
the adhoc signature hash and TCC treats the new `.app` as a stranger.
The README documents this workflow for end users; the cost is real
friction on every active dev cycle. Developer ID signing remains the
right long-term fix and remains deferred.

### Still open

1. **Wrong-content paste** (Session 2 #6, Session 3 #1). Intermittent
   issue where MacFlow pastes something other than the current
   transcript. Could be the 400ms clipboard-restore race in
   `adapters/macos.py`. Not reproduced reliably this session —
   every paste in history.db matched what was spoken. Keep watching.
2. **Dictionary / custom vocabulary** (Session 2 #5). Not designed.
3. **About dialog polish** (Session 2 #4).
4. **One-shot repeating Timer** for API-key prompt. Still there;
   low priority.
5. **Developer ID signing + notarisation.** Would eliminate TCC
   regrant pain AND Gatekeeper friction for recipients. Paid Apple
   Dev account required. Still out of scope for v0.
6. **Validating the install flow with a real recipient** (carried
   over from Session 3). README covers the end-user flow but has
   never been tested by someone who isn't the author.
7. **Hotkey-collision warnings.** Session 1 gotcha #4 flagged this
   for Ctrl+Space vs macOS "previous input source." Now we've also
   hit it for Ctrl+Space vs Sublime Text autocomplete. Detecting
   common collisions at startup (or offering a curated picker in
   Settings) would save future confusion. Low priority while the
   current Option+Space default works.

### Next session priorities

Same as Session 3 priorities carry forward — **get the app into a
real user's hands and collect feedback**. One addition: document
the Ctrl+Space / Sublime collision in the README's Troubleshooting
section if the tester reports paste-to-wrong-place behavior. The
current README suggests Ctrl+Space as the default; it's now
Option+Space in the shipped config, and the usage table needs to be
updated to match.

---

### Session 4 continuation — the hotkey saga + Accessibility
silently flipped off

After documenting the "notes" above, the next hour was spent
iterating the hotkey *six times* because every candidate except the
last one collided with something on the user's system. Then the
real culprit of the final breakdown turned out not to be the hotkey
at all — it was an **Accessibility permission that had silently
flipped off** during all the kill/relaunch cycles. Documenting both
so future-us doesn't walk the same path.

#### The hotkey iteration cascade

| # | Combo | Why it failed |
|---|---|---|
| 1 | `Ctrl+Space` (original) | Sublime Text's Auto Complete shortcut — pastes landed "on top of" Sublime's `lorem` snippet firing in parallel. |
| 2 | `Option+Space` | **Raycast** claims this as its launcher shortcut. Every hotkey press opened Raycast. |
| 3 | `Ctrl+Option+Space` | Untested — user's next feedback came after next combo. Theoretical good candidate. |
| 4 | `Ctrl+Shift+D` (like Wispr Flow) | **Character-key bug.** With Shift held, pynput delivers the key as `KeyCode(char='D')` (capital), but the config stores `char='d'` (lowercase). `_keys_match()` in `adapters/macos.py:218` compares `.char` literally, so press detection was inconsistent, the listener got stuck in `_hotkey_active=True`, key-repeat events fired, and macOS showed a "key held down" sound/alert. Lesson: character keys with Shift need case-insensitive matching, or just don't use character keys as triggers. |
| 5 | `Ctrl+Shift+Space` | User reported the cursor visibly jumping on press. Something (terminal emulator? Claude Code TUI? a background app?) claims this shortcut and steals focus. MacFlow's paste arrived ~1s later into whichever focus macOS had landed on. |
| 6 | **`Option+Shift+Space`** ✅ | No collisions. No cursor jump. This is the new shipped default. |

**Core reason the cascade was even possible**: pynput on macOS is a
*passive* listener. Every hotkey press fires in MacFlow *and* in
whatever app is focused, simultaneously. Any shortcut that's claimed
anywhere in the system causes this class of bug. The only permanent
fix is moving to a native `CGEventTap` in filter mode so the event
gets intercepted before reaching other apps. Deferred — reasonable
hotkeys are plentiful enough that iteration is cheaper than the
rewrite.

#### The Accessibility-flipped-off red herring

After landing on Option+Shift+Space, pastes *still* didn't land —
transcriptions saved to `history.db` (rows 67/68/69 confirmed via
direct SQLite query) but ⌘V did nothing visible. User's first
intuition was that the hotkey combo was still stealing focus.

**Actual root cause:** at some point during the day's multiple
rebuilds, relaunches, and `pkill -9` cycles, **MacFlow's entry in
System Settings → Privacy & Security → Accessibility had been
toggled off.** The toggle appears to have cleared silently — likely
from one of the "remove the stale MacFlow entry before re-granting"
steps I walked the user through after rebuild #2. At some step the
re-add-and-toggle-on wasn't completed, and nothing in subsequent
testing caught it.

**Failure signature when Accessibility is missing for MacFlow:**

1. Hotkey capture still works (Input Monitoring is separate and was
   still granted).
2. Mic permission still works; audio recording succeeds.
3. Transcription + enhancement succeed; row lands in `history.db`.
4. `MacInjector.inject()` runs without exception because CGEvent
   creation doesn't require Accessibility — only CGEvent *delivery*
   does. With Accessibility off, `CGEventPost(kCGHIDEventTap, …)`
   silently drops the event. No error raised, `inject()` returns
   `True`, `on_result` fires, app reports success.
5. User sees: mic icon cycle, transcript in history menu, nothing
   pasted into their focused field.

This is the cleanest "working but silently broken" failure mode
MacFlow has. It will absolutely happen to other users after rebuilds.

#### The diagnostic that cracked it

Cascading through the hotkey possibilities would have gone on
forever. What actually moved the investigation forward:

1. **Read `history.db` directly** (same move as the first hang
   diagnostic earlier in Session 4): transcripts 67/68/69 were all
   there, clean. That ruled out "pipeline broken" and refocused the
   search to "paste target."
2. **Ask the user to test paste into Notes.app / TextEdit instead
   of the terminal.** Splits "is the paste event not firing" vs. "is
   the paste event firing but going somewhere unexpected."
3. **Simultaneously, open Privacy & Security → Accessibility** and
   have the user eyeball whether MacFlow is listed AND toggled on.
   Takes 5 seconds. Answered the question before Notes.app even
   needed to be tested.

**Keep this checklist for next time.** In order, for any "transcribe
works but paste doesn't land" report:

1. Is there a new row in `history.db` matching the dictation? (If
   no: pipeline issue, not a paste issue.)
2. Is **MacFlow** listed AND toggled on in Accessibility?
3. Is **MacFlow** listed AND toggled on in Input Monitoring?
4. Does paste land in a neutral target app (Notes.app, TextEdit)?
5. If Notes works but your real target doesn't: target app is
   intercepting ⌘V or the hotkey is stealing focus from it.

#### Config and doc changes made while chasing this

- Live user config (`~/Library/Application Support/MacFlow/mac_flow.toml`)
  and repo template (`mac_flow.toml`) hotkey: `alt+shift` + `space`.
- `config._DEFAULTS["hotkey"]` was already `alt+space`; still needs
  to be bumped to match the shipped template. (Low priority — on-disk
  TOML wins, so the user and any fresh install both get
  `alt+shift+space` today. But fixing the default is the right
  hygiene move.)
- README.md had `Ctrl+Space` → `Option+Space` swept earlier in this
  session. Still needs one more sweep to `Option+Shift+Space`, plus
  a new Troubleshooting entry: **"I can see the transcript in
  History, but nothing pasted into my cursor."** Sequence: verify
  Accessibility toggle, then check target-app collision.
- This NOTES.md continuation.

#### Carried-forward open items (updated)

- Item 7 (hotkey-collision warnings) gets escalated a notch.
  Detecting common collisions at startup would have saved an hour
  today. Still low priority, but less low.
- **New item 8: character-key + Shift modifier bug in
  `MacHotkeyListener._keys_match()`.** If we ever want character-key
  triggers to work, normalize both sides to lowercase before
  comparison. See Session 4 continuation hotkey table row 4.
- **New item 9: post-rebuild Accessibility-verification step.**
  The README's "re-grant after rebuild" instructions don't actually
  close the loop on verifying the toggle is ON after the user
  re-adds MacFlow. A terminal one-liner like
  `python -c "from ApplicationServices import AXIsProcessTrusted; print(AXIsProcessTrusted())"`
  won't work (it tests the calling process, not MacFlow). A better
  fix is an **in-app "Verify Accessibility" menu item** that runs
  `AXIsProcessTrusted()` from inside MacFlow and shows a dialog with
  the result. Cheap to add, would have caught today's issue in 30
  seconds.

---

## Session 5 — The "works twice then hangs" mystery solved (2026-04-27)

### The breakthrough that ended four sessions of guessing

After three days of speculating about timeouts, network flakiness,
permission flaps, and hotkey collisions, the user came back with the
same symptom **and the app still hung in front of us**: "works once
or twice, then just hangs." User asked the right question this time:
*should we switch LLM providers? Try Ollama? Where is it actually
hanging?*

Instead of guessing again, attached `sample` (the macOS built-in
profiler) to the live process. Three seconds of sampling produced
the answer in the first stack trace.

### The actual root cause: PortAudio ↔ CoreAudio deadlock in `recorder.stop()`

```
Thread A (Python worker calling Recorder.stop)         Thread B (CoreAudio I/O thread)
  └─ sounddevice.InputStream.stop()                       └─ CoreAudio's IOWorkLoop
     └─ libportaudio: FinishStoppingStream                   └─ libportaudio: startStopCallback
        └─ AudioOutputUnitStop                                  └─ AudioUnitGetProperty
           └─ HALC_ProxyIOContext::StopIOProc                      └─ std::recursive_mutex::lock()
              └─ HALB_Mutex::Lock()       ← waits forever              └─ pthread_mutex_wait  ← waits forever
```

Both threads sleeping in `__psynch_mutexwait`. Each one holds a lock
the other one needs to make progress. Classic two-thread deadlock,
fully inside Apple's CoreAudio HAL and PortAudio's interaction with
it. The Python pipeline never gets past the `stop the mic` step —
no transcription request is ever made, no Groq call, no ⌘V, nothing.

**Implications for everything we'd been guessing:**

| Hypothesis we were chasing | What `sample` revealed |
|---|---|
| Groq API timeout / network stall | Innocent. Audio stop never returns, so transcribe is never called. |
| Switch to a different LLM provider | Wouldn't help. The bug is upstream of any LLM. |
| Switch to local models / Ollama | Wouldn't help. Same reason. |
| Process accumulating stuck state over time | Partially right (state can degrade) but not the mechanism. The mechanism is one specific deadlock pattern. |
| Hotkey collision with focused app | Unrelated. We confirmed that fix; this is a different bug. |
| Accessibility flipped off | Unrelated. We confirmed that fix; this is a different bug. |

### The contributing condition: device-routing churn from concurrent mic-aware apps

`sample` told us *where* it deadlocks. To understand *why it triggers
sometimes and not others*, listed mic-touching processes on the
user's machine at the moment of the hang:

```
zoom.us, caphost, ZoomClips           (Zoom client, helpers always running)
Slack, Slack Helper                    (mic stays watched even outside calls)
Loom, loom-recorder-production         (active recording engine in background)
Granola                                (meeting transcription — full-time mic listener)
Google Chrome + 1Password              (extensions with mic permission)
Claude.app                             (Electron, has audio code paths)
+ Logitech BRIO webcam plugged in      (second input device, presents on default-device list)
```

Default input was `MacBook Pro Microphone`, but `Logitech BRIO` was
also enumerated. Every one of those other apps periodically opens,
queries, or closes a CoreAudio device. Each operation is a chance
for the system default device to be re-routed mid-flight while
MacFlow's PortAudio stream is in the middle of starting or stopping.
PortAudio's macOS backend is known-fragile under that kind of
device-state churn — under specific timings the StopIOProc path
deadlocks against CoreAudio's I/O thread.

User's config had `[audio] device_index = -1` ("System Default"),
which means MacFlow was binding to whichever device was the default
*at stream-open time* and re-resolving on every recording — putting
it directly in the path of every other app's device shuffles.

### The diagnostic moves (keep these for next time)

1. **Don't kill the hung process. Diagnose first.** Every kill we
   did in Sessions 3 and 4 destroyed evidence of where the hang was.
   The state we needed was the live process's thread stacks.
2. **`sample <pid> 3 -mayDie`** — built into macOS, no install, no
   sudo required. Sampling for 3 seconds against a hung process
   produces a complete call graph for every thread. If two or more
   threads are stuck in `__psynch_mutexwait` or `semaphore_wait_trap`
   with related call frames, that's a deadlock. Done.
3. **Read it bottom-up.** The leaves of the call graph (kernel-level
   wait calls) tell you nothing on their own. The frames *above*
   them — the library code that asked the kernel to wait — are
   where the actual logic lives. In our case: `HALC_ProxyIOContext::
   StopIOProc` and `AudioUnitGetProperty` — both PortAudio /
   CoreAudio. That instantly localized the bug to the audio layer.
4. **`py-spy dump --pid <pid>`** as a follow-up if you want
   Python-level stacks to see *exactly which line of `recorder.py`*
   is on the stack. Requires `sudo` on macOS. We didn't need it
   this time because `sample`'s C frames were specific enough.
5. **List the system context.** `ps aux | grep -iE "zoom|slack|...|
   blackhole"` to inventory mic-touching processes. `system_profiler
   SPAudioDataType` to list input devices and the current default.
   These two together explain the *trigger*, even when `sample`
   has already told you the *mechanism*.

The full stack trace from this session was saved to
`/tmp/MacFlow_2026-04-27_095201_Gf9x.sample.txt`. That file is
`/tmp` and will be GC'd; if we want a permanent reference, copy it
into the repo under `docs/debug/` next session.

### The fix path

**Workaround being tested as of this session (no code change):**
in MacFlow's menubar **Microphone** submenu, pick a *specific*
device — `MacBook Pro Microphone` or `Logitech BRIO` or a USB
headset if plugged in — instead of `System Default`. This pins
PortAudio to one device by index, so other apps' default-device
shuffles can't reach the in-flight stream. Doesn't eliminate the
PortAudio deadlock as a possibility, but eliminates the most common
trigger.

USB-wired headphones (with explicit device selection) would be
even more isolated than the built-in mic — fewer state changes per
unit of system activity, and no codec-renegotiation cycles like
Bluetooth. The user is testing this combination.

**Real fix (when the workaround proves insufficient or a recipient
hits this without the same setup knobs):** rewrite `core/recorder.py`
to use **AVAudioEngine** via pyobjc instead of `sounddevice` /
PortAudio. AVAudioEngine is Apple's recommended high-level audio
capture API, talks directly to AVFoundation, and doesn't go through
the HAL mutex path that's deadlocking. Estimated half a day; only
the macOS adapter changes, Linux Flow's recorder stays as-is.
Linux Flow keeps using `sounddevice` because PortAudio + ALSA
doesn't have this bug.

### Open items added by this session

- **#10 — Replace `sounddevice` with AVAudioEngine on macOS.**
  Real fix for the deadlock. Half-day rewrite of `core/recorder.py`
  scoped to the Mac path. Defer until the device-pinning workaround
  proves insufficient.
- **#11 — Subprocess-isolate the recorder as a fallback.** If
  rewriting to AVAudioEngine is too much yak-shaving for v0, an
  alternative is spawning the recorder in a subprocess. When it
  deadlocks, the parent kills the subprocess and recovers. Uglier
  than #10 but cheaper.
- **#12 — Default `device_index` to a concrete pick after first
  successful recording.** Once the user picks a specific device
  via the menubar, persist that. Pre-emptively avoid the
  System-Default trap on fresh installs by writing the first
  successful device into `mac_flow.toml`.
- **#13 — Document the `sample` diagnostic in
  CLAUDE.md / README troubleshooting.** Anyone hitting "MacFlow
  hangs randomly" should know to run
  `sample $(pgrep -f MacFlow.app/Contents/MacOS) 3 -mayDie` before
  killing the process. Bury the lesson where the next debugger
  will find it.

### Permanent lesson (write this into CLAUDE.md too)

**When MacFlow hangs, get a stack trace before you do anything
else.** The cost of `sample <pid> 3` is three seconds of waiting.
The cost of guessing is days. We spent four sessions on this app
treating "it hangs sometimes" as a fuzzy reliability problem and
proposing architectural rewrites (different LLM, local models)
when the actual bug was a 30-frame deep deadlock between two of
Apple's own threads that took one terminal command to find.

### Session 5 closeout — bugs fixed, decisions made

After landing the diagnostic above, two more issues surfaced and
were resolved before the user moved into all-day soak-testing:

**Bug: "no-speak press" left the icon stuck on the sparkle.** When
the user pressed the hotkey and released without speaking, the
processing icon never reverted. Root cause was two silent-exit
paths in `core/engine.py` that returned without firing any UI
callback: (1) `_process()` when `recorder.stop()` returned empty
bytes, (2) `_process_wav()` when the transcript was empty or
matched `_WHISPER_HALLUCINATIONS`. Fix: added a new `on_idle`
callback to the Engine, fired in both early-return paths.
`MacFlowApp._on_idle()` resets the icon to `_ICON_IDLE` and the
status item to `Status: Ready`. Confirmed working: three rapid
empty presses returned to idle each time, mid-soak-test.

**Trap: stale install via `cp -R` over an existing directory.**
After rebuilding the bundle, the `cp -R dist/MacFlow.app
/Applications/MacFlow.app` command (run *without* a leading
`rm -rf`) didn't replace the bundled `python311.zip`. Result: I
told the user "the fix is in" while the running app actually had
three-day-old bytecode. Spent ~30 minutes "debugging" a fix that
wasn't installed. The smoking gun was the timestamp:
`ls -la /Applications/MacFlow.app/Contents/Resources/lib/python311.zip`
showed Apr 24 13:15 instead of today.

**Lesson:** every install must `rm -rf /Applications/MacFlow.app`
*first*, then `cp -R`. The README and CLAUDE.md both say this; my
single-line install command in this session skipped the `rm -rf`
and BSD `cp` quietly merged some files but not others. The python
zip in particular has the same name across builds and may
preserve old contents under merge-mode copy.

**Verification step to add to every install:**
```bash
unzip -p /Applications/MacFlow.app/Contents/Resources/lib/python311.zip \
    core/engine.pyc | strings | grep -q on_idle && echo "fix is installed" \
    || echo "INSTALL FAILED — bundle is stale"
```
Substitute `on_idle` for whatever symbol the latest fix introduces.

**Decision: remove "System Default" from the Microphone submenu.**
The PortAudio deadlock from earlier is triggered most often when
`device_index = -1` (System Default), because that re-resolves on
every recording and is the variable other apps' device-shuffling
affects. User asked to remove the option entirely. Implemented
this session — `_refresh_mic_menu()` no longer adds a "System
Default" entry. Existing on-disk configs with `device_index = -1`
continue to work via sounddevice's own default handling, but the
UI no longer offers a way to opt back into that trap. Users now
must pick a specific device by index. Item #12 (auto-persist
first-good device) becomes more important if we ship to others
who start with `-1`.

**Cleanup performed at session end:** removed `dist/` and `build/`
from the working tree. They're py2app outputs and regenerated by
the next build. Removing them prevents the "two MacFlow.apps
appearing in System Settings pickers" confusion the user hit when
trying to re-grant Accessibility.

### Status going into all-day soak test

- Hotkey: `Option+F5` (user's choice — single-modifier function-key
  combo, no collisions reported)
- Mic device: index 2 (specific device, not System Default)
- on_idle fix: confirmed live in installed bundle
- Real PortAudio deadlock: latent but unreached as long as the user
  stays on a pinned device. Item #10 (AVAudioEngine rewrite) remains
  the principled fix; deferred until the workaround proves
  insufficient.
- Open-item count from this session: items #10–#13 from earlier
  remain. No new ones added at closeout.

### Hardcore runtime requirements for the bundled `.app`

User asked to document this before sharing the app. Captured here
so future-us (and recipients) know exactly what's negotiable and
what isn't.

**Strict requirements — `MacFlow.app` literally will not run without these:**

| Requirement | Why | Verifiable how |
|---|---|---|
| **Apple Silicon Mac** (M1, M2, M3, M4) | Bundle is built `Mach-O thin (arm64)`. Intel Macs cannot load arm64 binaries — period. Not a performance issue, the loader refuses. | `codesign -dvv /Applications/MacFlow.app` → look for `Format=app bundle with Mach-O thin (arm64)` |
| **macOS 12.0 (Monterey) or later** | `LSMinimumSystemVersion: "12.0"` in Info.plist. macOS refuses to launch on older releases. | About This Mac → version |
| **Internet connection** | Audio is sent to Groq's Whisper API for transcription, plus the Llama LLM for cleanup if mode != "raw". No offline fallback exists. | Any working network |
| **A Groq API key** (per user) | Stored in `~/Library/Application Support/MacFlow/.env`. Free tier at console.groq.com is sufficient for daily use. The author does not share their key. | Settings → Verify API Connection |
| **Microphone permission** for MacFlow | macOS prompts on first record attempt. | System Settings → Privacy & Security → Microphone |
| **Accessibility permission** for MacFlow | Required for the synthetic ⌘V CGEvent that pastes the transcript. Without it, transcripts save to history but never paste. | System Settings → Privacy & Security → Accessibility |
| **Input Monitoring permission** for MacFlow | Required for global hotkey capture (pynput). Without it, the hotkey does nothing. | System Settings → Privacy & Security → Input Monitoring |
| **A working input audio device** | Any built-in mic, USB headset, or USB webcam mic works. Bluetooth devices are technically allowed but trigger PortAudio↔CoreAudio deadlocks far more often (Session 5). | Microphone submenu in MacFlow shows your devices |

**Soft requirements — bundle works without them but UX degrades:**

- A Gatekeeper bypass on first launch (Control-click → Open → Open Anyway). Required because the bundle is ad-hoc signed, not Developer ID signed. One-time thing per install.
- A specific (non-System-Default) device pinned in the Microphone submenu. As of Session 5, the System Default option was removed from the UI to force this — the deadlock risk is too high otherwise.

**Sharing the `.app` file with a colleague:**

If the recipient is on another Apple Silicon Mac running macOS 12+, **yes — handing them the `.app` file will work**, with these caveats:

1. **Gatekeeper warning on first launch.** They Control-click → Open → Open Anyway. Also flagged in the README "Install" section.
2. **They need their own Groq API key** — yours never leaves your machine (it's in `~/Library/Application Support/MacFlow/.env`, not in the bundle).
3. **They grant the three permissions on their machine** — Microphone, Accessibility, Input Monitoring — fresh, just like you did.
4. **They pick a specific microphone** in the Microphone submenu after first launch.
5. **Zip it with `ditto`, not Finder's compress.** Finder's compress can mangle the bundle's resource forks, which then trips Gatekeeper into "damaged" mode on the recipient's Mac:
   ```bash
   ditto -c -k --sequesterRsrc --keepParent /Applications/MacFlow.app MacFlow.app.zip
   ```

**What blocks shipping to non-Apple-Silicon Macs (Intel Mac Mini, older MacBooks):**

The bundle is arm64-only. Three options for fixing this, in order of effort:

1. **Universal2 build** (target: `arm64 + x86_64` in one bundle). Add `--arch universal2` to the `py2app` command and ensure every dependency in `requirements.txt` has universal wheels available. Some pyobjc components and the bundled `libportaudio.dylib` may need to be rebuilt or sourced as universal binaries. Estimated half a day.
2. **Two separate builds** — one arm64 build on Apple Silicon, one x86_64 build on an Intel Mac. Requires access to an Intel Mac. Annoying to maintain two artifacts.
3. **Stay arm64-only and document it.** Cheap. Loses the Intel Mac Mini target.

This becomes **Open item #14: Universal2 build for Intel Mac compatibility.** Deferred until there's a confirmed Intel-Mac recipient who wants it. Don't pre-build for hypothetical demand.
