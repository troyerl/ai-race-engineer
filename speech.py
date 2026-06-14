"""Text-to-speech helper (macOS say, Windows SAPI, Linux espeak)."""

from __future__ import annotations

import json
import subprocess
import sys
import time


def _first_nonempty_line(text: str) -> str:
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s:
            return s
    return ""


def _action_from_call_line(head: str) -> str:
    """First segment of engineer line 1 (ACTION — TIMING — SERVICE)."""
    for sep in ("—", " - ", " – "):
        if sep in head:
            return head.split(sep, 1)[0].strip()
    return head.strip()


def speak_text(text: str) -> bool:
    """Speak a short phrase. Returns True when the platform TTS ran without error."""
    a = (text or "").strip()
    if not a:
        return False
    try:
        if sys.platform.startswith("darwin"):
            r = subprocess.run(["say", a], check=False, capture_output=True, text=True, timeout=90)
            return r.returncode == 0
        if sys.platform.startswith("win"):
            safe = a.replace("\n", " ").replace("\r", " ")
            ps = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Speak({json.dumps(safe)})"
            )
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-STA",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()
                print(f"[WARN] Windows TTS failed (exit {r.returncode}): {err}")
                return False
            return True
        r = subprocess.run(["espeak", a], check=False, capture_output=True, text=True, timeout=90)
        return r.returncode == 0
    except Exception as exc:
        print(f"[WARN] TTS error: {exc}")
        return False


def speak_engineer_advice(full: str, *, include_why: bool = True) -> bool:
    """Read the call line aloud, then optionally the WHY line. Returns True if any speech ran."""
    head = _first_nonempty_line(full)
    spoke = False
    if head:
        action = _action_from_call_line(head)
        if action:
            spoke = speak_text(action) or spoke
    if not include_why:
        return spoke
    why = ""
    for line in (full or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.upper().startswith("WHY:"):
            why = s[4:].strip()
            break
    if why:
        time.sleep(0.3)
        spoke = speak_text(why) or spoke
    return spoke
