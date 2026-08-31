"""MacFlow menubar app built on rumps.

rumps wraps NSStatusItem, giving us a native menubar presence without any of
the GTK complexity the Linux version needed (no subprocess, no widget threading,
no GLib.idle_add).

Menubar layout:
    [mic icon]  (white mic → red mic → white sparkle → white mic)
    ─────
    Status: Ready / Recording... / Processing...
    Copy Last Transcript
    ─────
    Mode ▸  Raw / Clean / Rewrite   (active one shown with ●)
    Hotkey... (informational — edit via config)
    Mic ▸   list of available input devices
    ─────
    Settings ▸
        Set Groq API Key...
        Verify API Connection
        Toggle auto-paste (currently: on)
        Toggle save history (currently: on)
    Show History
    ─────
    Open Config File
    About
    Quit

All menu callbacks run on the main thread (rumps handles that). Engine
callbacks (on_recording_start, on_result, etc.) fire from worker threads —
we call rumps APIs from them, which are thread-safe enough for title
updates and rumps.notification().
"""

import subprocess
import sys
import threading
from pathlib import Path

import rumps
from AppKit import (
    NSAlert,
    NSApp,
    NSFloatingWindowLevel,
    NSInformationalAlertStyle,
    NSTextField,
)

import config
import paths
from core.engine import Engine
from db import history as db


def _front_alert(title, message=""):
    """Show a modal alert that always floats above all windows.

    rumps.alert uses NSAlert but doesn't set the window level, so on
    multi-monitor setups the dialog can appear behind other windows and
    freeze the menubar app. We build the NSAlert directly and force
    NSFloatingWindowLevel so it's always visible.
    """
    NSApp.activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message or "")
    alert.setAlertStyle_(NSInformationalAlertStyle)
    alert.window().setLevel_(NSFloatingWindowLevel)
    return alert.runModal()


def _front_confirm(title, message="", ok="OK", cancel="Cancel"):
    """Show a two-button confirmation dialog. Returns True if the user
    clicked the `ok` button, False for `cancel`. Same floating-level
    fix as `_front_alert`.
    """
    NSApp.activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message or "")
    alert.setAlertStyle_(NSInformationalAlertStyle)
    alert.addButtonWithTitle_(ok)
    alert.addButtonWithTitle_(cancel)
    alert.window().setLevel_(NSFloatingWindowLevel)
    # NSAlertFirstButtonReturn = 1000 (OK), NSAlertSecondButtonReturn = 1001 (Cancel)
    return alert.runModal() == 1000


class _FrontWindowResult:
    """Mimic rumps.Window response: .clicked (bool) and .text (str)."""
    def __init__(self, clicked, text):
        self.clicked = clicked
        self.text = text


def _front_window(title, message="", default_text="", ok="Save", cancel="Cancel",
                   dimensions=(320, 24)):
    """Show a text-input dialog that always floats above all windows."""
    NSApp.activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message or "")
    alert.setAlertStyle_(NSInformationalAlertStyle)
    alert.addButtonWithTitle_(ok)
    alert.addButtonWithTitle_(cancel)

    # Add a text field as the accessory view
    width, height = dimensions
    text_field = NSTextField.alloc().initWithFrame_(((0, 0), (width, height)))
    text_field.setStringValue_(default_text)
    alert.setAccessoryView_(text_field)

    alert.window().setLevel_(NSFloatingWindowLevel)
    alert.window().setInitialFirstResponder_(text_field)

    # NSAlertFirstButtonReturn = 1000 (OK), NSAlertSecondButtonReturn = 1001 (Cancel)
    clicked = alert.runModal() == 1000
    return _FrontWindowResult(clicked=clicked, text=str(text_field.stringValue()))

_ASSETS = paths.assets_dir()
_ICON_IDLE = str(_ASSETS / "mic_idleTemplate.png")
_ICON_RECORDING = str(_ASSETS / "mic_recording.png")
_ICON_PROCESSING = str(_ASSETS / "sparkleTemplate.png")


class MacFlowApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="MacFlow", title=None, icon=_ICON_IDLE,
            template=True, quit_button=None,
        )
        self._engine = Engine()
        self._build_menu()
        self._wire_engine()

    # ---------------------------------------------------------------
    # Menu construction
    # ---------------------------------------------------------------

    def _build_menu(self) -> None:
        cfg = self._engine.cfg

        # --- Info labels (non-interactive, grouped at top) ---
        self._status_item = rumps.MenuItem("Status: Ready")
        self._status_item.set_callback(None)
        self.menu.add(self._status_item)

        self._hotkey_item = rumps.MenuItem(self._hotkey_label())
        self._hotkey_item.set_callback(None)
        self.menu.add(self._hotkey_item)

        self.menu.add(None)

        self.menu.add(rumps.MenuItem("Copy Last Transcript", callback=self._copy_last))
        self.menu.add(None)

        # Mode submenu
        self._mode_item = rumps.MenuItem("Mode")
        for m in ("raw", "clean", "rewrite"):
            item = rumps.MenuItem(m.capitalize(), callback=self._make_mode_cb(m))
            item.state = 1 if cfg["enhancement"]["mode"] == m else 0
            self._mode_item.add(item)
        self.menu.add(self._mode_item)

        # Mic submenu
        self._mic_item = rumps.MenuItem("Microphone")
        self._refresh_mic_menu()
        self.menu.add(self._mic_item)

        self.menu.add(None)

        # Settings submenu
        settings = rumps.MenuItem("Settings")
        settings.add(
            rumps.MenuItem("Set API Key...", callback=self._set_api_key)
        )
        settings.add(
            rumps.MenuItem("Verify Providers", callback=self._verify_api)
        )
        self._auto_paste_item = rumps.MenuItem(
            self._auto_paste_label(), callback=self._toggle_auto_paste
        )
        settings.add(self._auto_paste_item)
        self._auto_clipboard_item = rumps.MenuItem(
            self._auto_clipboard_label(), callback=self._toggle_auto_clipboard
        )
        settings.add(self._auto_clipboard_item)
        self._save_history_item = rumps.MenuItem(
            self._save_history_label(), callback=self._toggle_save_history
        )
        settings.add(self._save_history_item)
        self._notifications_item = rumps.MenuItem(
            self._notifications_label(), callback=self._toggle_notifications
        )
        settings.add(self._notifications_item)
        settings.add(None)  # separator
        self._retry_item = rumps.MenuItem(
            "Retry Last Recording", callback=self._retry_last_recording
        )
        settings.add(self._retry_item)
        self.menu.add(settings)

        self._history_item = rumps.MenuItem("History")
        self._refresh_history_menu()
        self.menu.add(self._history_item)
        self.menu.add(None)
        self.menu.add(
            rumps.MenuItem("Open Config File", callback=self._open_config_file)
        )
        self.menu.add(rumps.MenuItem("About", callback=self._show_about))
        self.menu.add(rumps.MenuItem("Quit MacFlow", callback=self._quit))

    def _wire_engine(self) -> None:
        self._engine.on_recording_start = self._on_recording_start
        self._engine.on_recording_stop = self._on_recording_stop
        self._engine.on_result = self._on_result
        self._engine.on_error = self._on_error
        self._engine.on_idle = self._on_idle

        # Start the engine. If the API key is missing, the engine itself
        # will still run; transcription errors surface on first use.
        try:
            self._engine.start()
        except Exception as e:
            _front_alert(
                "MacFlow — Startup Error",
                f"{e}\n\nCheck microphone and Accessibility permissions in "
                "System Settings → Privacy & Security.",
            )

        # If the selected providers need a cloud API key that isn't set,
        # prompt immediately so the user isn't confused by silent failures on
        # first hotkey press. Fully-local setups (mlx + Ollama) need no key,
        # so this never fires for the default configuration.
        if self._active_key_requirement():
            rumps.Timer(self._prompt_for_api_key, 0.5).start()

    # ---------------------------------------------------------------
    # Notifications
    # ---------------------------------------------------------------

    def _notify(self, title: str, subtitle: str = "", message: str = "") -> None:
        """Single gate for every macOS notification. Respects the
        `ui.notify_on_result` config flag — when False, no banners fire.
        """
        if not self._engine.cfg["ui"]["notify_on_result"]:
            return
        try:
            rumps.notification(title=title, subtitle=subtitle, message=message)
        except Exception:
            pass

    # ---------------------------------------------------------------
    # Engine callbacks (fire from worker threads)
    # ---------------------------------------------------------------

    def _on_recording_start(self) -> None:
        self.icon = _ICON_RECORDING
        self.template = False  # keep the red colour
        self._status_item.title = "Status: Recording..."

    def _on_recording_stop(self) -> None:
        self.icon = _ICON_PROCESSING
        self.template = True
        self._status_item.title = "Status: Processing..."

    def _on_result(self, raw: str, final: str, injected: bool) -> None:
        self.icon = _ICON_IDLE
        self.template = True
        self._status_item.title = "Status: Ready"
        self._refresh_history_menu()
        preview = (final[:80] + "…") if len(final) > 80 else final
        subtitle = "Pasted" if injected else "Copied to clipboard"
        self._notify(title="MacFlow", subtitle=subtitle, message=preview)

    def _on_idle(self) -> None:
        """Pipeline finished but produced no transcript (empty audio,
        hallucination filter hit). Reset the menubar to ready state
        so the sparkle doesn't get stuck."""
        self.icon = _ICON_IDLE
        self.template = True
        self._status_item.title = "Status: Ready"

    def _on_error(self, msg: str) -> None:
        self.icon = _ICON_IDLE
        self.template = True
        # Surface the error in the status item so the user notices it
        # even with notifications turned off. Auto-clear after a few
        # seconds so the menubar returns to a clean state.
        short = msg if len(msg) <= 80 else msg[:77] + "…"
        self._status_item.title = f"Status: Error — {short}"
        print(f"[mac_flow] error: {msg}", file=sys.stderr)
        self._notify(title="MacFlow — error", subtitle="", message=msg)
        t = threading.Timer(6.0, self._clear_error_status)
        t.daemon = True
        t.start()

    def _clear_error_status(self) -> None:
        """Revert the status item to Ready unless a new recording is
        already in progress. Called by a one-shot timer after transient
        messages (errors, retry-not-available hints)."""
        busy_prefixes = (
            "Status: Ready",
            "Status: Recording",
            "Status: Processing",
            "Status: Retrying",
            "Status: Verifying",
        )
        if not self._status_item.title.startswith(busy_prefixes):
            self._status_item.title = "Status: Ready"

    # ---------------------------------------------------------------
    # Menu callbacks (fire on main thread)
    # ---------------------------------------------------------------

    def _copy_last(self, _):
        text = self._engine.copy_last_transcript()
        if not text:
            self._notify("MacFlow", "", "No transcripts yet — hold the hotkey and speak.")
        else:
            preview = (text[:80] + "…") if len(text) > 80 else text
            self._notify(
                title="MacFlow", subtitle="Copied last transcript", message=preview
            )

    def _make_mode_cb(self, mode: str):
        def cb(sender):
            config.set_value("enhancement", "mode", mode)
            for item in self._mode_item.values():
                item.state = 1 if item.title.lower() == mode else 0
            self._engine.reload()

        return cb

    def _refresh_mic_menu(self) -> None:
        from core.recorder import Recorder

        # Clear old entries
        for key in list(self._mic_item.keys()):
            del self._mic_item[key]

        current = self._engine.cfg["audio"]["device_index"]

        # "System Default" (-1) is intentionally NOT exposed here.
        # It re-resolves on every recording and ends up sharing the
        # default device with whatever else on the system is using
        # the mic (Zoom, Slack, Loom, Granola, browsers, etc.). Each
        # of those apps' device-shuffles is a chance to trigger the
        # PortAudio↔CoreAudio deadlock in `Recorder.stop()` — see
        # NOTES.md Session 5 for the stack trace. Pinning a specific
        # device avoids the trigger entirely.

        try:
            devs = Recorder.list_devices()
        except Exception as e:
            err = rumps.MenuItem(f"(device list error: {e})")
            err.set_callback(None)
            self._mic_item.add(err)
            return

        if not devs:
            none_item = rumps.MenuItem("(no input devices found)")
            none_item.set_callback(None)
            self._mic_item.add(none_item)
            return

        for dev in devs:
            # Keep names short enough to fit in the menubar dropdown
            label = f"[{dev['index']}] {dev['name'][:40]}"
            item = rumps.MenuItem(label, callback=self._make_mic_cb(dev["index"]))
            item.state = 1 if current == dev["index"] else 0
            self._mic_item.add(item)

        self._mic_item.add(None)
        self._mic_item.add(
            rumps.MenuItem("Refresh Device List", callback=self._refresh_mics_clicked)
        )

    def _make_mic_cb(self, index: int):
        def cb(sender):
            config.set_value("audio", "device_index", index)
            self._engine.reload()
            self._refresh_mic_menu()

        return cb

    def _refresh_mics_clicked(self, _):
        self._refresh_mic_menu()

    def _target_key_env(self):
        """Which (env_name, label) API key the current provider setup uses,
        or None when everything selected runs locally (no key needed)."""
        cfg = self._engine.cfg
        if cfg["transcription"]["provider"] == "groq":
            return ("GROQ_API_KEY", "Groq (transcription)")
        prov = cfg["enhancement"]["provider"]
        ep = config.ENHANCE_PROVIDERS[prov]
        if ep["api_key_env"]:
            return (ep["api_key_env"], f"{prov} (enhancement)")
        return None

    def _active_key_requirement(self):
        """Return (env_name, label) for a required-but-missing key, else None."""
        target = self._target_key_env()
        if target and not self._engine.cfg["keys"].get(target[0]):
            return target
        return None

    def _set_api_key(self, _):
        self._prompt_for_api_key(None)

    def _prompt_for_api_key(self, _timer) -> None:
        target = self._target_key_env()
        if target is None:
            _front_alert(
                "MacFlow",
                "You're running fully local (mlx + Ollama) — no API key needed.\n\n"
                "Switch a provider to a cloud service in the config file to use a key.",
            )
            return

        env_name, label = target
        current = self._engine.cfg["keys"].get(env_name, "")
        msg = (
            f"Paste your {label} API key.\n\n"
            "It's stored locally in .env — never in the config file or git."
        )
        if current:
            msg += f"\n\nCurrent: {current[:6]}…{current[-4:]}"
        response = _front_window(
            title=f"MacFlow — {label} API Key",
            message=msg,
            ok="Save",
            cancel="Cancel",
        )

        if response.clicked and response.text.strip():
            config.set_api_key(env_name, response.text.strip())
            self._engine.reload()
            self._notify(title="MacFlow", subtitle="API key saved", message="")

    def _verify_api(self, _):
        cfg = self._engine.cfg
        tp = cfg["transcription"]["provider"]
        t_model = (
            cfg["transcription"]["mlx_model"]
            if tp == "mlx"
            else cfg["transcription"]["groq_model"]
        )
        prov = cfg["enhancement"]["provider"]
        ep = config.ENHANCE_PROVIDERS[prov]
        e_model = cfg["enhancement"]["model"] or ep["model"]

        # Check the enhancement endpoint — every backend is OpenAI-compatible,
        # so a models.list() is a uniform reachability probe. Run synchronously
        # on the main thread (short call) to avoid the NSWindow-from-background
        # crash. mlx transcription is local and verifies itself on first use.
        self._status_item.title = "Status: Verifying..."
        key_env = ep["api_key_env"]
        key = cfg["keys"].get(key_env, "") if key_env else "local"
        try:
            from openai import OpenAI

            client = OpenAI(base_url=ep["base_url"], api_key=key or "missing", timeout=10.0)
            models = client.models.list()
            n = len(models.data) if hasattr(models, "data") else 0
            enh_status = f"✓ reachable ({n} models)"
        except Exception as e:
            enh_status = f"✗ {str(e)[:160]}"
        self._status_item.title = "Status: Ready"

        trans_note = "local — verified on first transcription" if tp == "mlx" else "cloud"
        _front_alert(
            "MacFlow — Providers",
            f"Transcription:  {tp}\n{t_model}\n({trans_note})\n\n"
            f"Enhancement:  {prov}\n{e_model}\n{enh_status}",
        )

    def _auto_paste_label(self) -> str:
        val = "on" if self._engine.cfg["output"]["auto_paste"] else "off"
        return f"Auto-paste: {val}"

    def _toggle_auto_paste(self, _):
        current = self._engine.cfg["output"]["auto_paste"]
        config.set_value("output", "auto_paste", not current)
        self._engine.reload()
        self._auto_paste_item.title = self._auto_paste_label()

    def _auto_clipboard_label(self) -> str:
        val = "on" if self._engine.cfg["output"]["auto_clipboard"] else "off"
        return f"Auto-clipboard: {val}"

    def _toggle_auto_clipboard(self, _):
        current = self._engine.cfg["output"]["auto_clipboard"]
        config.set_value("output", "auto_clipboard", not current)
        self._engine.reload()
        self._auto_clipboard_item.title = self._auto_clipboard_label()

    def _save_history_label(self) -> str:
        val = "on" if self._engine.cfg["output"]["save_history"] else "off"
        return f"Save history: {val}"

    def _toggle_save_history(self, _):
        current = self._engine.cfg["output"]["save_history"]
        config.set_value("output", "save_history", not current)
        self._engine.reload()
        self._save_history_item.title = self._save_history_label()

    def _notifications_label(self) -> str:
        val = "on" if self._engine.cfg["ui"]["notify_on_result"] else "off"
        return f"Notifications: {val}"

    def _toggle_notifications(self, _):
        current = self._engine.cfg["ui"]["notify_on_result"]
        config.set_value("ui", "notify_on_result", not current)
        self._engine.reload()
        self._notifications_item.title = self._notifications_label()

    def _retry_last_recording(self, _):
        """Re-run transcription on the most recently failed WAV, if any."""
        if not self._engine.has_pending_wav():
            self._status_item.title = "Status: No failed recording to retry"
            t = threading.Timer(4.0, self._clear_error_status)
            t.daemon = True
            t.start()
            return
        self._status_item.title = "Status: Retrying..."
        self.icon = _ICON_PROCESSING
        self.template = True
        self._engine.retry_last()

    def _refresh_history_menu(self) -> None:
        """Rebuild the History submenu with the latest transcriptions."""
        for key in list(self._history_item.keys()):
            del self._history_item[key]

        rows = db.get_recent(30)

        if not rows:
            empty = rumps.MenuItem("(no history yet)")
            empty.set_callback(None)
            self._history_item.add(empty)
            return

        for r in rows:
            # Truncate to fit the menu; show timestamp + preview
            ts = r["created_at"][5:16].replace("T", " ")  # "MM-DD HH:MM"
            preview = r["final_text"][:60].replace("\n", " ")
            if len(r["final_text"]) > 60:
                preview += "…"
            label = f"{ts}  {preview}"
            item = rumps.MenuItem(label, callback=self._make_history_copy_cb(r["final_text"]))
            self._history_item.add(item)

        self._history_item.add(None)  # separator
        self._history_item.add(
            rumps.MenuItem("Clear All History", callback=self._clear_history)
        )

    def _make_history_copy_cb(self, text: str):
        """Return a callback that copies the given text to the clipboard."""
        def cb(_):
            if self._engine._injector:
                self._engine._injector.copy_to_clipboard(text)
            preview = (text[:60] + "…") if len(text) > 60 else text
            self._notify("MacFlow", "Copied to clipboard", preview)
        return cb

    def _clear_history(self, _):
        confirmed = _front_confirm(
            "Clear All History?",
            "This permanently deletes every saved transcript. This cannot be undone.",
            ok="Clear All",
            cancel="Cancel",
        )
        if not confirmed:
            return
        db.clear_all()
        self._refresh_history_menu()
        self._notify("MacFlow", "", "History cleared.")

    def _open_config_file(self, _):
        subprocess.run(["open", "-R", str(config.CONFIG_PATH)])

    def _show_about(self, _):
        cfg = self._engine.cfg
        tp = cfg["transcription"]["provider"]
        whisper = (
            cfg["transcription"]["mlx_model"]
            if tp == "mlx"
            else cfg["transcription"]["groq_model"]
        )
        prov = cfg["enhancement"]["provider"]
        llm = cfg["enhancement"]["model"] or config.ENHANCE_PROVIDERS[prov]["model"]
        version = cfg["app"]["version"]
        _front_alert(
            f"MacFlow  v{version}",
            "Voice dictation for macOS.\n\n"
            "Hold your hotkey, speak, release — your words are pasted "
            "at the cursor.\n\n"
            f"Transcription ({tp}):  {whisper}\n\n"
            f"Enhancement ({prov}):  {llm}\n\n"
            "Ported from Linux Flow\n"
            "github.com/GeneArnold/linux_flow",
        )

    def _quit(self, _):
        try:
            self._engine.stop()
        finally:
            rumps.quit_application()

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------

    def _hotkey_label(self) -> str:
        cfg = self._engine.cfg
        combo = "+".join(cfg["hotkey"]["modifiers"] + [cfg["hotkey"]["key"]])
        return f"Hotkey: {combo}"


def run() -> None:
    MacFlowApp().run()
