"""Abstract interfaces for platform-specific text injection and hotkey listening.

Concrete implementations:
  macOS → adapters/macos.py

Engine and UI code only ever touch these ABCs and the factory functions,
never the concrete classes directly.
"""

from abc import ABC, abstractmethod
from typing import Callable


class TextInjector(ABC):
    """Types text into the currently focused window and/or sets the clipboard."""

    @abstractmethod
    def inject(self, text: str, restore_clipboard: bool = True) -> bool:
        """Paste text into the active window. Returns True on success."""

    @abstractmethod
    def copy_to_clipboard(self, text: str) -> bool:
        """Place text on the system clipboard. Returns True on success."""


class HotkeyListener(ABC):
    """Listens globally for a configurable hotkey combination."""

    @abstractmethod
    def start(self, on_press: Callable, on_release: Callable) -> None:
        """Start the listener. Callbacks are fired from a background thread."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release any held resources."""


def get_injector() -> TextInjector:
    """Return the TextInjector for the current platform."""
    from adapters.macos import MacInjector

    return MacInjector()


def get_hotkey_listener(modifiers: list[str], key: str) -> HotkeyListener:
    """Return the HotkeyListener for the current platform."""
    from adapters.macos import MacHotkeyListener

    return MacHotkeyListener(modifiers, key)
