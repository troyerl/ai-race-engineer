"""Text-to-speech helper — fluid engineer calls on macOS, Windows, and Linux."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.sax.saxutils as saxutils

# Neural Microsoft voices (online). Falls back to local SAPI if unavailable.
_EDGE_VOICES = (
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-US-ChristopherNeural",
)

_MAC_VOICES = ("Samantha", "Alex", "Daniel", "Karen", "Moira", "Tessa")
_WIN_SAPI_VOICES = (
    "Microsoft Zira Desktop",
    "Microsoft Zira",
    "Microsoft David Desktop",
    "Microsoft David",
    "Microsoft Hazel Desktop",
)


def _soften_caps(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return s
    letters = [c for c in s if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.65:
        return s.title()
    return s


def _tts_phrase(line: str) -> str:
    """Normalize engineer text for speech."""
    s = (line or "").strip()
    if re.search(r"[—–]|(?:\s-\s)", s):
        segments = re.split(r"\s*[—–]\s*|\s+-\s+", s)
        parts = [_soften_caps(seg) for seg in segments if seg.strip()]
        return ". ".join(parts)
    return _soften_caps(s)


def _speech_lines(full: str, *, include_why: bool) -> list[str]:
    """Content lines to read aloud, in display order."""
    out: list[str] = []
    for line in (full or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        upper = s.upper()
        if upper.startswith("TRIGGER:"):
            continue
        if upper.startswith("WHY:"):
            if include_why:
                why = s[4:].strip()
                if why:
                    out.append(_tts_phrase(why))
            continue
        out.append(_tts_phrase(s))
    return out


def _paragraph_from_parts(parts: list[str]) -> str:
    """One flowing paragraph — avoids choppy per-line subprocess calls."""
    bits = [p.rstrip(".,; ") for p in parts if p and p.strip()]
    if not bits:
        return ""
    return ". ".join(bits) + "."


def _ssml_from_parts(parts: list[str]) -> str:
    """SSML with short pauses between call segments."""
    chunks: list[str] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i > 0:
            chunks.append('<break time="520ms"/>')
        chunks.append(saxutils.escape(part))
    body = "".join(chunks)
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xml:lang='en-US'>{body}</speak>"
    )


def _macos_voice_args() -> list[str]:
    try:
        r = subprocess.run(
            ["say", "-v", "?"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        listing = r.stdout or ""
    except Exception:
        return []
    for name in _MAC_VOICES:
        if re.search(rf"^{re.escape(name)}\b", listing, re.MULTILINE):
            return ["-v", name]
    return []


def _edge_tts_available() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:
        return False


def _play_mp3(path: str) -> bool:
    if sys.platform.startswith("win"):
        uri = "file:///" + os.path.abspath(path).replace("\\", "/")
        ps = (
            "Add-Type -AssemblyName presentationCore; "
            "$p = New-Object System.Windows.Media.MediaPlayer; "
            f"$p.Open([uri]::new({json.dumps(uri)})); "
            "$p.Play(); "
            "$deadline = (Get-Date).AddSeconds(120); "
            "Start-Sleep -Milliseconds 300; "
            "while (-not $p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) { "
            "  Start-Sleep -Milliseconds 60 "
            "}; "
            "while ($p.NaturalDuration.HasTimeSpan -and $p.Position -lt $p.NaturalDuration.TimeSpan "
            "-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 80 }"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            capture_output=True,
            text=True,
            timeout=130,
        )
        return r.returncode == 0
    if sys.platform.startswith("darwin"):
        r = subprocess.run(["afplay", path], check=False, capture_output=True, text=True, timeout=130)
        return r.returncode == 0
    for player in (["mpv", "--no-video", "--really-quiet"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        try:
            r = subprocess.run([*player, path], check=False, capture_output=True, text=True, timeout=130)
            if r.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


def _speak_edge_tts(text: str) -> bool:
    import edge_tts

    async def _synthesize(voice: str, out_path: str) -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(out_path)

    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        last_err: Exception | None = None
        for voice in _EDGE_VOICES:
            try:
                asyncio.run(_synthesize(voice, path))
                if os.path.getsize(path) > 0 and _play_mp3(path):
                    return True
            except Exception as exc:
                last_err = exc
                continue
        if last_err is not None:
            print(f"[WARN] edge-tts failed: {last_err}")
        return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _speak_windows_sapi(parts: list[str]) -> bool:
    ssml = _ssml_from_parts(parts)
    voice_pick = "; ".join(
        f"try {{ $s.SelectVoice({json.dumps(name)}); $picked=$true; break }} catch {{}}"
        for name in _WIN_SAPI_VOICES
    )
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$picked=$false; "
        f"{voice_pick}; "
        "$s.Rate = -1; "
        "$s.Volume = 100; "
        f"$ssml = {json.dumps(ssml)}; "
        "try { $s.SpeakSsml($ssml) } catch { $s.Speak($ssml) }"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        capture_output=True,
        text=True,
        timeout=130,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        print(f"[WARN] Windows SAPI TTS failed (exit {r.returncode}): {err}")
        return False
    return True


def _speak_macos(parts: list[str]) -> bool:
    text = _paragraph_from_parts(parts)
    if not text:
        return False
    args = ["say", *_macos_voice_args(), "-r", "178", text]
    r = subprocess.run(args, check=False, capture_output=True, text=True, timeout=130)
    return r.returncode == 0


def _speak_linux(parts: list[str]) -> bool:
    text = _paragraph_from_parts(parts)
    if not text:
        return False
    r = subprocess.run(
        ["espeak", "-s", "155", "-g", "12", "-p", "48", text],
        check=False,
        capture_output=True,
        text=True,
        timeout=130,
    )
    return r.returncode == 0


def speak_text(text: str) -> bool:
    """Speak a short phrase. Returns True when the platform TTS ran without error."""
    a = (text or "").strip()
    if not a:
        return False
    return speak_engineer_advice(a, include_why=False)


def speak_engineer_advice(full: str, *, include_why: bool = True) -> bool:
    """Read engineer advice as one fluid utterance. Returns True if speech ran."""
    parts = _speech_lines(full, include_why=include_why)
    if not parts:
        return False
    try:
        if sys.platform.startswith("win"):
            paragraph = _paragraph_from_parts(parts)
            if _edge_tts_available() and paragraph:
                if _speak_edge_tts(paragraph):
                    return True
            return _speak_windows_sapi(parts)
        if sys.platform.startswith("darwin"):
            return _speak_macos(parts)
        return _speak_linux(parts)
    except Exception as exc:
        print(f"[WARN] TTS error: {exc}")
        return False
