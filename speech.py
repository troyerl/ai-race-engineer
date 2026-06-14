"""Text-to-speech helper (macOS say, Windows SAPI, Linux espeak)."""

from __future__ import annotations

import subprocess
import sys
import time


def _first_nonempty_line(text: str) -> str:
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


def speak_text(text: str) -> None:
    a = (text or "").strip()
    if not a:
        return
    try:
        if sys.platform.startswith("darwin"):
            subprocess.run(["say", a], check=False)
        elif sys.platform.startswith("win"):
            safe = a.replace("'", " ").replace("\n", " ").replace("\r", " ")
            ps = (
                "Add-Type -AssemblyName System.Speech; "
                "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("
                + repr(safe)
                + ")"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
        else:
            subprocess.run(["espeak", a], check=False)
    except Exception:
        pass


def speak_engineer_advice(full: str, *, include_why: bool = True) -> None:
    """Read the call line aloud, then optionally the WHY line."""
    head = _first_nonempty_line(full)
    if head:
        action = head.split("—", 1)[0].strip()
        if action:
            speak_text(action)
    if not include_why:
        return
    why = ""
    for line in (full or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.upper().startswith("WHY:"):
            why = s[4:].strip()
            break
    if why:
        time.sleep(0.3)
        speak_text(why)
