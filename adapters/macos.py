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
            CGEventSourceCreate,
            kCGEventFlagMaskCommand,
            kCGEventSourceStateHIDSystemState,
            kCGHIDEventTap,
        )

        self._NSPasteboard = NSPasteboard
        self._NSStringPboardType = NSStringPboardType
        self._CGEventCreateKeyboardEvent = CGEventCreateKeyboardEvent
        self._CGEventPost = CGEventPost
        self._CGEventSetFlags = CGEventSetFlags
        self._kCGEventFlagMaskCommand = kCGEventFlagMaskCommand
        self._kCGHIDEventTap = kCGHIDEventTap

        # Events posted with a NULL source are unreliable — some applications
        # ignore them outright. A real HID-state source makes the synthetic
        # keystroke look like it came from the keyboard.
        self._event_source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)

    # -- public API -------------------------------------------------------

    def inject(self, text: str, restore_clipboard: bool = True) -> bool:
        """Paste text at the cursor via the clipboard + ⌘V.

        Returns True if the keystroke was posted without error. Note this is
        NOT proof the paste landed: CGEventPost returns nothing and does not
        raise when the event is discarded, so a missing Accessibility grant
        looks identical to success from here.

        When *restore_clipboard* is False the transcript is deliberately left
        on the clipboard instead of the previous contents being put back, so
        the user can paste manually if the synthetic keystroke was dropped.
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
        if prior is not None and restore_clipboard:
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
        src = self._event_source  # may be None if creation failed; still usable

        # Key down
        down = self._CGEventCreateKeyboardEvent(src, 9, True)
        self._CGEventSetFlags(down, self._kCGEventFlagMaskCommand)
        self._CGEventPost(self._kCGHIDEventTap, down)

        # Give the receiving app a moment to process the key-down before the
        # key-up arrives. Posting both in the same instant is a common reason a
        # synthetic shortcut is dropped.
        time.sleep(0.01)

        # Key up
        up = self._CGEventCreateKeyboardEvent(src, 9, False)
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
        self._key_vk = self._trigger_vk(self._key)
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

    @staticmethod
    def _trigger_vk(key):
        """macOS virtual keycode for the trigger key, or None if unknown.

        Named keys (Key.space -> 49) carry a vk on their .value; a KeyCode
        built from a plain character does not, and returns None. Suppression
        is skipped in that case rather than guessing.
        """
        vk = getattr(key, "vk", None)
        if vk is None:
            vk = getattr(getattr(key, "value", None), "vk", None)
        return vk

    def _darwin_intercept(self, event_type, event):
        """Stop the trigger key from reaching the focused application.

        pynput listens passively by default, so the hotkey is delivered to
        whatever has focus as well as to us — holding Option+Shift+Space types
        a space into the document, and key-repeat turns it into a run of them.
        This is the "hotkey passthrough" limitation noted in the README.

        darwin_intercept is pynput's macOS hook for selective suppression:
        return the event to pass it through, or None to drop it system-wide.
        pynput calls this *after* dispatching to on_press/on_release (see
        _util/darwin.py: _handle_message runs first), so suppressing here does
        not stop the hotkey from firing.

        Only the configured trigger key is dropped, and only while every
        required modifier is held, so ordinary typing is untouched. The
        _hotkey_active check keeps the matching key-up suppressed too, since
        modifiers are often released a few milliseconds before the key.
        """
        if self._key_vk is None:
            return event

        from Quartz import (
            CGEventGetIntegerValueField,
            kCGEventKeyDown,
            kCGEventKeyUp,
            kCGKeyboardEventKeycode,
        )

        if event_type not in (kCGEventKeyDown, kCGEventKeyUp):
            return event
        if CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode) != self._key_vk:
            return event

        with self._lock:
            combo_held = self._held_mods == self._required_mods
        if combo_held or self._hotkey_active:
            return None
        return event

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
        # NOTE: deliberately a passive (listen-only) tap. Passing
        # darwin_intercept here makes pynput create an ACTIVE tap
        # (kCGEventTapOptionDefault), which can suppress events — and if this
        # process stalls or dies while holding one, system-wide keyboard input
        # can be lost until reboot. Not worth it: the trigger is a modifier
        # key, which generates no character, so there is nothing to suppress.
        self._listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
