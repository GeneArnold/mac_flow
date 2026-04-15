"""macOS platform implementations.

MacInjector:
  Pastes via NSPasteboard + a simulated ⌘V CGEvent.
  This is the same trick used by most Mac dictation tools (e.g. Wispr Flow):
  setting each character with pynput.type() is unreliable with Unicode and
  extremely slow for long transcripts. Clipboard-paste is instant, preserves
  Unicode, and works in every text field macOS recognises.

  We save the previous clipboard contents before overwriting so the user's
  copy buffer isn't destroyed. Restoration happens on a background timer
  after the paste is complete.

MacHotkeyListener:
  Uses pynput (same listener class that works on Linux). On macOS, pynput
  requires:
    - Accessibility permission (for simulating keys and listening globally)
    - Input Monitoring permission (for global key capture on 10.15+)

  The first time the app runs, macOS will prompt to grant these permissions
  to whatever terminal / launcher is running main.py. The README explains
  how to grant them in System Settings.

  Unlike X11, macOS does not let a passive listener block keys from reaching
  the focused app — the same "hotkey passthrough" limitation applies. If
  your hotkey collides with the focused app's shortcuts, pick a different combo.
"""

import threading
import time
from typing import Callable

from pynput import keyboard

from adapters.base import HotkeyListener, TextInjector

# Maps config modifier names → pynput canonical Key constants.
# On macOS, "cmd" and "super" both refer to the ⌘ key (Key.cmd).
_MOD_MAP = {
    "cmd": keyboard.Key.cmd,
    "super": keyboard.Key.cmd,
    "ctrl": keyboard.Key.ctrl,
    "alt": keyboard.Key.alt,
    "option": keyboard.Key.alt,
    "shift": keyboard.Key.shift,
}


# ---------------------------------------------------------------------------
# Text injection
# ---------------------------------------------------------------------------


class MacInjector(TextInjector):
    """Paste text into the active window via NSPasteboard + synthetic ⌘V."""

    # Delay after paste before restoring the previous clipboard contents.
    # Too short → the paste may grab the restored content instead of our text.
    # Too long → user notices their copy buffer was momentarily missing.
    _CLIPBOARD_RESTORE_DELAY_S = 0.4

    def __init__(self):
        # Import lazily so the module imports on non-mac for testing
        from AppKit import NSPasteboard, NSStringPboardType
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGEventFlagMaskCommand,
            kCGHIDEventTap,
        )

        self._NSPasteboard = NSPasteboard
        self._NSStringPboardType = NSStringPboardType
        self._CGEventCreateKeyboardEvent = CGEventCreateKeyboardEvent
        self._CGEventPost = CGEventPost
        self._CGEventSetFlags = CGEventSetFlags
        self._kCGEventFlagMaskCommand = kCGEventFlagMaskCommand
        self._kCGHIDEventTap = kCGHIDEventTap

    # -- public API -------------------------------------------------------

    def inject(self, text: str) -> bool:
        """Paste text at the cursor via the clipboard + ⌘V.
        Returns True on success; False if we couldn't simulate the keystroke.
        """
        if not text:
            return True

        pb = self._NSPasteboard.generalPasteboard()

        # Preserve whatever the user had copied so we can restore it
        prior = pb.stringForType_(self._NSStringPboardType)

        if not self._set_clipboard(text):
            return False

        # Tiny delay so the pasteboard change is committed before ⌘V fires
        time.sleep(0.02)

        try:
            self._send_cmd_v()
        except Exception:
            return False

        # Restore the previous clipboard after the paste has had time to land.
        # Runs on a daemon thread so we don't block the caller.
        if prior is not None:
            threading.Thread(
                target=self._restore_clipboard_later,
                args=(prior,),
                daemon=True,
            ).start()

        return True

    def copy_to_clipboard(self, text: str) -> bool:
        """Place text on the system clipboard without pasting."""
        return self._set_clipboard(text)

    # -- internals --------------------------------------------------------

    def _set_clipboard(self, text: str) -> bool:
        pb = self._NSPasteboard.generalPasteboard()
        pb.clearContents()
        ok = pb.setString_forType_(text, self._NSStringPboardType)
        return bool(ok)

    def _send_cmd_v(self) -> None:
        """Post a synthetic ⌘V keystroke via CGEvent.
        Virtual keycode 9 is 'v' on Mac. Flags set to Command.
        """
        # Key down
        down = self._CGEventCreateKeyboardEvent(None, 9, True)
        self._CGEventSetFlags(down, self._kCGEventFlagMaskCommand)
        self._CGEventPost(self._kCGHIDEventTap, down)

        # Key up
        up = self._CGEventCreateKeyboardEvent(None, 9, False)
        self._CGEventSetFlags(up, self._kCGEventFlagMaskCommand)
        self._CGEventPost(self._kCGHIDEventTap, up)

    def _restore_clipboard_later(self, prior_text: str) -> None:
        time.sleep(self._CLIPBOARD_RESTORE_DELAY_S)
        try:
            self._set_clipboard(prior_text)
        except Exception:
            pass  # best effort — if it fails the user still has their text pasted


# ---------------------------------------------------------------------------
# Hotkey listener
# ---------------------------------------------------------------------------


class MacHotkeyListener(HotkeyListener):
    def __init__(self, modifiers: list[str], key: str):
        # Set of canonical pynput Key objects that must all be held simultaneously
        self._required_mods = {_MOD_MAP[m] for m in modifiers if m in _MOD_MAP}
        self._key = self._parse_key(key)
        self._held_mods: set = set()
        self._hotkey_active = False
        self._listener = None
        self._on_press: Callable | None = None
        self._on_release: Callable | None = None
        self._lock = threading.Lock()

    def _parse_key(self, key: str):
        """Convert a config key string to a pynput Key or KeyCode."""
        if len(key) == 1:
            return keyboard.KeyCode.from_char(key)
        try:
            return getattr(keyboard.Key, key)
        except AttributeError:
            return keyboard.KeyCode.from_char(key)

    def _normalize_mod(self, key):
        """Map Key.ctrl_l / Key.ctrl_r → Key.ctrl (and similar for alt/shift/cmd)."""
        for canonical in self._required_mods:
            name = canonical.name.rstrip("_lr") if hasattr(canonical, "name") else None
            if (
                name
                and hasattr(keyboard.Key, f"{name}_l")
                and (
                    key == getattr(keyboard.Key, f"{name}_l")
                    or key == getattr(keyboard.Key, f"{name}_r")
                )
            ):
                return canonical
        return key

    def _on_key_press(self, key):
        """Track held modifiers; fire on_press when the full combo is active."""
        norm = self._normalize_mod(key)
        if norm in self._required_mods:
            with self._lock:
                self._held_mods.add(norm)
        elif self._held_mods == self._required_mods and self._keys_match(
            key, self._key
        ):
            if not self._hotkey_active:
                self._hotkey_active = True
                if self._on_press:
                    # Run in a separate thread so the pynput listener isn't blocked
                    threading.Thread(target=self._on_press, daemon=True).start()

    def _on_key_release(self, key):
        """Track modifier releases; fire on_release when the trigger key is released."""
        norm = self._normalize_mod(key)
        if norm in self._required_mods:
            with self._lock:
                self._held_mods.discard(norm)
        if self._keys_match(key, self._key) and self._hotkey_active:
            self._hotkey_active = False
            if self._on_release:
                threading.Thread(target=self._on_release, daemon=True).start()

    def _keys_match(self, a, b) -> bool:
        """Compare two pynput key objects, handling KeyCode.char equality."""
        if a == b:
            return True
        if hasattr(a, "char") and hasattr(b, "char"):
            return a.char == b.char
        return False

    def start(self, on_press: Callable, on_release: Callable) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
