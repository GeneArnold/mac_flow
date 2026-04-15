"""Mac Flow menubar app built on rumps.

rumps wraps NSStatusItem, giving us a native menubar presence without any of
the GTK complexity the Linux version needed (no subprocess, no widget threading,
no GLib.idle_add).

Menubar layout:
    [🎙]  (title flips while recording)
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

import config
from core.engine import Engine
from db import history as db

_PROJECT_ROOT = Path(__file__).parent.parent
_IDLE_TITLE = "🎙"
_RECORDING_TITLE = "🔴"
_PROCESSING_TITLE = "✨"


class MacFlowApp(rumps.App):
    def __init__(self):
        super().__init__(name="Mac Flow", title=_IDLE_TITLE, quit_button=None)
        self._engine = Engine()
        self._build_menu()
        self._wire_engine()

    # ---------------------------------------------------------------
    # Menu construction
    # ---------------------------------------------------------------

    def _build_menu(self) -> None:
        cfg = self._engine.cfg

        self._status_item = rumps.MenuItem("Status: Ready")
        self._status_item.set_callback(None)  # non-interactive label
        self.menu.add(self._status_item)

        self.menu.add(rumps.MenuItem("Copy Last Transcript", callback=self._copy_last))
        self.menu.add(None)

        # Mode submenu
        self._mode_item = rumps.MenuItem("Mode")
        for m in ("raw", "clean", "rewrite"):
            item = rumps.MenuItem(m.capitalize(), callback=self._make_mode_cb(m))
            item.state = 1 if cfg["enhancement"]["mode"] == m else 0
            self._mode_item.add(item)
        self.menu.add(self._mode_item)

        self.menu.add(
            rumps.MenuItem(self._hotkey_label(), callback=self._show_hotkey_help)
        )

        # Mic submenu — populated on show
        self._mic_item = rumps.MenuItem("Microphone")
        self._refresh_mic_menu()
        self.menu.add(self._mic_item)

        self.menu.add(None)

        # Settings submenu
        settings = rumps.MenuItem("Settings")
        settings.add(
            rumps.MenuItem("Set Groq API Key...", callback=self._set_api_key)
        )
        settings.add(
            rumps.MenuItem("Verify API Connection", callback=self._verify_api)
        )
        self._auto_paste_item = rumps.MenuItem(
            self._auto_paste_label(), callback=self._toggle_auto_paste
        )
        settings.add(self._auto_paste_item)
        self._save_history_item = rumps.MenuItem(
            self._save_history_label(), callback=self._toggle_save_history
        )
        settings.add(self._save_history_item)
        self.menu.add(settings)

        self.menu.add(
            rumps.MenuItem("Show Recent History", callback=self._show_history)
        )
        self.menu.add(None)
        self.menu.add(
            rumps.MenuItem("Open Config File", callback=self._open_config_file)
        )
        self.menu.add(rumps.MenuItem("About", callback=self._show_about))
        self.menu.add(rumps.MenuItem("Quit Mac Flow", callback=self._quit))

    def _wire_engine(self) -> None:
        self._engine.on_recording_start = self._on_recording_start
        self._engine.on_recording_stop = self._on_recording_stop
        self._engine.on_result = self._on_result
        self._engine.on_error = self._on_error

        # Start the engine. If the API key is missing, the engine itself
        # will still run; transcription errors surface on first use.
        try:
            self._engine.start()
        except Exception as e:
            rumps.alert(
                "Mac Flow — Startup Error",
                f"{e}\n\nCheck microphone and Accessibility permissions in "
                "System Settings → Privacy & Security.",
            )

        # If no API key is set, prompt immediately so the user can't be confused
        # by silent failures on first hotkey press.
        if not self._engine.cfg["groq"]["api_key"]:
            rumps.Timer(self._prompt_for_api_key, 0.5).start()

    # ---------------------------------------------------------------
    # Engine callbacks (fire from worker threads)
    # ---------------------------------------------------------------

    def _on_recording_start(self) -> None:
        self.title = _RECORDING_TITLE
        self._status_item.title = "Status: Recording..."

    def _on_recording_stop(self) -> None:
        self.title = _PROCESSING_TITLE
        self._status_item.title = "Status: Processing..."

    def _on_result(self, raw: str, final: str, injected: bool) -> None:
        self.title = _IDLE_TITLE
        self._status_item.title = "Status: Ready"
        if self._engine.cfg["ui"]["notify_on_result"]:
            preview = (final[:80] + "…") if len(final) > 80 else final
            subtitle = "Pasted" if injected else "Copied to clipboard"
            # rumps.notification wraps UNUserNotificationCenter — macOS will
            # silently drop this if the user hasn't granted notification
            # permission to the Python launcher. That's fine.
            try:
                rumps.notification(
                    title="Mac Flow", subtitle=subtitle, message=preview
                )
            except Exception:
                pass

    def _on_error(self, msg: str) -> None:
        self.title = _IDLE_TITLE
        self._status_item.title = "Status: Ready"
        try:
            rumps.notification(title="Mac Flow — error", subtitle="", message=msg)
        except Exception:
            print(f"[mac_flow] error: {msg}", file=sys.stderr)

    # ---------------------------------------------------------------
    # Menu callbacks (fire on main thread)
    # ---------------------------------------------------------------

    def _copy_last(self, _):
        text = self._engine.copy_last_transcript()
        if not text:
            rumps.alert("Mac Flow", "No transcripts yet — hold the hotkey and speak.")
        else:
            preview = (text[:80] + "…") if len(text) > 80 else text
            try:
                rumps.notification(
                    title="Mac Flow", subtitle="Copied last transcript", message=preview
                )
            except Exception:
                pass

    def _make_mode_cb(self, mode: str):
        def cb(sender):
            config.set_value("enhancement", "mode", mode)
            for item in self._mode_item.values():
                item.state = 1 if item.title.lower() == mode else 0
            self._engine.reload()

        return cb

    def _show_hotkey_help(self, _):
        cfg = self._engine.cfg
        combo = "+".join(cfg["hotkey"]["modifiers"] + [cfg["hotkey"]["key"]])
        rumps.alert(
            "Mac Flow — Hotkey",
            f"Current hotkey: {combo}\n\n"
            "Hold to record, release to transcribe.\n\n"
            "To change, edit mac_flow.toml and restart the app. "
            "Valid modifiers: cmd, ctrl, alt (option), shift.",
        )

    def _refresh_mic_menu(self) -> None:
        from core.recorder import Recorder

        # Clear old entries
        for key in list(self._mic_item.keys()):
            del self._mic_item[key]

        current = self._engine.cfg["audio"]["device_index"]

        default_item = rumps.MenuItem(
            "System Default", callback=self._make_mic_cb(-1)
        )
        default_item.state = 1 if current == -1 else 0
        self._mic_item.add(default_item)

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

    def _set_api_key(self, _):
        self._prompt_for_api_key(None)

    def _prompt_for_api_key(self, _timer) -> None:
        current = self._engine.cfg["groq"]["api_key"]
        placeholder = (current[:6] + "…" + current[-4:]) if current else "gsk_..."
        response = rumps.Window(
            title="Mac Flow — Groq API Key",
            message=(
                "Paste your Groq API key (starts with gsk_).\n\n"
                "Get one at console.groq.com — it's free.\n"
                f"Current: {placeholder}" if current else
                "Get one at console.groq.com — it's free."
            ),
            default_text="",
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 24),
        ).run()

        if response.clicked and response.text.strip():
            config.set_value("groq", "api_key", response.text.strip())
            self._engine.reload()
            rumps.notification(
                title="Mac Flow", subtitle="API key saved", message=""
            )

    def _verify_api(self, _):
        from core.transcriber import Transcriber

        key = self._engine.cfg["groq"]["api_key"]
        if not key:
            rumps.alert("Mac Flow", "No API key set yet. Settings → Set Groq API Key.")
            return

        def _do():
            try:
                # Minimal sanity check: list models (cheap, authenticated call)
                from groq import Groq

                client = Groq(api_key=key)
                models = client.models.list()
                count = len(models.data) if hasattr(models, "data") else 0
                rumps.notification(
                    title="Mac Flow",
                    subtitle="Groq connection OK",
                    message=f"{count} models available",
                )
            except Exception as e:
                rumps.notification(
                    title="Mac Flow — API check failed",
                    subtitle="",
                    message=str(e)[:200],
                )

        threading.Thread(target=_do, daemon=True).start()

    def _auto_paste_label(self) -> str:
        val = "on" if self._engine.cfg["output"]["auto_paste"] else "off"
        return f"Auto-paste: {val}"

    def _toggle_auto_paste(self, _):
        current = self._engine.cfg["output"]["auto_paste"]
        config.set_value("output", "auto_paste", not current)
        self._engine.reload()
        self._auto_paste_item.title = self._auto_paste_label()

    def _save_history_label(self) -> str:
        val = "on" if self._engine.cfg["output"]["save_history"] else "off"
        return f"Save history: {val}"

    def _toggle_save_history(self, _):
        current = self._engine.cfg["output"]["save_history"]
        config.set_value("output", "save_history", not current)
        self._engine.reload()
        self._save_history_item.title = self._save_history_label()

    def _show_history(self, _):
        rows = db.get_recent(20)
        if not rows:
            rumps.alert("Mac Flow", "No history yet.")
            return
        body = "\n\n".join(
            f"• {r['created_at'][:19]}  ({r['mode']})\n  {r['final_text'][:180]}"
            for r in rows
        )
        rumps.alert("Mac Flow — Last 20 transcripts", body)

    def _open_config_file(self, _):
        subprocess.run(["open", "-R", str(config.CONFIG_PATH)])

    def _show_about(self, _):
        rumps.alert(
            "Mac Flow",
            "Voice dictation for macOS, powered by Groq.\n\n"
            "Hold your hotkey, speak, release — your words are pasted at the cursor.\n\n"
            "Ported from Linux Flow (github.com/GeneArnold/linux_flow).",
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
