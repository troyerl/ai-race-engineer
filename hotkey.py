"""
Global analyze hotkey — works while iRacing (or another app) has focus.

Default: Space. You can also map a wheel button in iRacing to an F-key.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, Signal

# Virtual-key codes (Windows); also used as labels for macOS pynput fallback.
HOTKEY_CHOICES: dict[str, int] = {
    "SPACE": 0x20,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
}

DEFAULT_HOTKEY = "SPACE"
LEGACY_DEFAULT_HOTKEY = "F9"


def normalize_hotkey(key_name: str) -> str:
    """Resolve config/UI values to a known hotkey id."""
    k = (key_name or "").strip().upper()
    if k in ("SPACEBAR", "SPACE BAR"):
        return "SPACE"
    if not k or k == LEGACY_DEFAULT_HOTKEY or k not in HOTKEY_CHOICES:
        return DEFAULT_HOTKEY
    return k


def hotkey_display_label(key_name: str) -> str:
    k = normalize_hotkey(key_name)
    if k == "SPACE":
        return "Spacebar"
    return k


def hotkey_qt_sequence(key_name: str) -> str:
    """Map our hotkey label to a QKeySequence string."""
    k = (key_name or DEFAULT_HOTKEY).upper()
    if k == "SPACE":
        return "Space"
    return k


def mac_accessibility_trusted() -> bool:
    """macOS global hotkeys (pynput) require Accessibility permission."""
    if not sys.platform.startswith("darwin"):
        return True
    try:
        import ctypes
        import ctypes.util

        app_services = ctypes.util.find_library("ApplicationServices")
        if not app_services:
            return False
        lib = ctypes.CDLL(app_services)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return False


class HotkeyBridge(QObject):
    """Thread-safe bridge: background hotkey thread -> Qt main thread."""

    triggered = Signal()


class AnalyzeHotkey:
    """
    Registers a global hotkey when possible.

    - Windows: RegisterHotKey (no extra packages)
    - macOS/Linux: pynput if installed, else inactive (use in-app QShortcut)
    """

    def __init__(self, on_press: Callable[[], None]):
        self._on_press = on_press
        self._bridge = HotkeyBridge()
        self._bridge.triggered.connect(self._on_press)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active_key: str | None = None
        self._pynput_listener = None

    def start(self, key_name: str) -> bool:
        self.stop()
        key_name = (key_name or DEFAULT_HOTKEY).upper()
        if key_name not in HOTKEY_CHOICES:
            key_name = DEFAULT_HOTKEY
        self._stop.clear()
        self._active_key = key_name

        if sys.platform.startswith("win"):
            self._thread = threading.Thread(
                target=self._win_loop,
                args=(HOTKEY_CHOICES[key_name],),
                daemon=True,
            )
            self._thread.start()
            return True

        try:
            from pynput import keyboard as pynput_kb
        except ImportError:
            return False

        if sys.platform.startswith("darwin") and not mac_accessibility_trusted():
            return False

        key = getattr(pynput_kb.Key, key_name.lower(), None)
        if key is None:
            return False

        def on_activate():
            self._bridge.triggered.emit()

        try:
            self._pynput_listener = pynput_kb.GlobalHotKeys({f"<{key_name.lower()}>": on_activate})
            self._pynput_listener.start()
            return True
        except Exception:
            self._pynput_listener = None
            return False

    def stop(self) -> None:
        self._stop.set()
        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._active_key = None

    def _win_loop(self, vk: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WM_HOTKEY = 0x0312
        hotkey_id = 1
        if not user32.RegisterHotKey(None, hotkey_id, 0, vk):
            return
        try:
            msg = wintypes.MSG()
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == WM_HOTKEY:
                        self._bridge.triggered.emit()
                time.sleep(0.03)
        finally:
            user32.UnregisterHotKey(None, hotkey_id)
