import json
import os
import sys
import threading
import time

import boto3
from PySide6.QtCore import QObject, Signal
from botocore.config import Config


DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# Absolute wall-clock guard: some streams can misbehave without a proper end iteration.
STREAM_MAX_SECONDS = 50


def _build_engineer_prompt(mode: str, race_json: str) -> str:
    schema = (
        "Data is compact JSON: "
        "x{md=live|strategy}; "
        "s{st=session_state,tr=time_remain,lt=laps_total,ot=is_on_track,ig=is_in_garage}; "
        "m{l=lap,lr=laps_remain,p=pos,fu=fuel,fph=fuel/hr,fpl=fuel/lap_est,fl=fuel_laps_left,"
        "mk=can_make,ls=laps_short,lp=last_pit_lap,t=lap_times,pc=pace,fg=flags,fs=flag_state,"
        "pr=on_pit_road,sl=stint_laps,ga=gap_ahead_s,gb=gap_behind_s,bl=best_lap_s,fo=falloff_s,"
        "pb=pit_payback_laps,pw=pit_window_open,pwu=laps_until_window,tw,tws,twsl,twr}; "
        "r{pl=pit_loss,ts=tire_sets_avail,fc=fuel_capacity,ftl=laps_per_full_tank_fuel_est}; "
        "rv=rivals; f=field. "
    )
    base = (
        "You are a Lead Race Engineer. "
        + schema
        + "Use x.md exactly: if strategy, pre-race plan only; if live, in-race advice only. "
    )

    if mode == "strategy":
        return (
            base
            + "CONTEXT: DRIVER IS OFF TRACK (garage/grid/prep). Build a PRE-RACE plan. "
            + "Goal: approximate laps BETWEEN pit stops optimizing fuel tank (r.ftl, r.fc, m.fpl) "
            + "and tire life using r.ts (available sets); mention when to take FUEL ONLY vs 2 vs 4 tires if helpful. "
            + "Assume green-flag racing unless s says otherwise; note estimates are approximate. "
            + "OUTPUT (single line, EXACT separators): "
            "FUEL STINT — TIRE STINT — STOPS EST — NOTE <TAGS>. "
            "- FUEL STINT: e.g. PIT EVERY N LAPS FOR FUEL (integer N from r.ftl/m.fpl/s.lt). "
            "- TIRE STINT: e.g. EVERY M LAPS or ALIGN WITH FUEL (use r.ts). "
            "- STOPS EST: e.g. ~K STOPS. "
            "- NOTE: <=8 words caveat. "
            "TAGS: [strategy|fuel|tires] and [H|M|L]. "
            "Keep total <= 32 words. "
            f"{race_json}"
        )

    return (
        base
        + "Primary goal: Optimize track position vs fuel/tire life (live race). "
        + "Use m.fs (GREEN/CAUTION/UNKNOWN) not m.fg. "
        + "If flag_state is CAUTION: default to STAY OUT unless fuel requires a stop or pitting gains clear track position. "
        + "If flag_state is GREEN or UNKNOWN: do NOT recommend pitting unless we are inside the pit window or fuel requires it. "
        + "Use tires: tire_wear_last_known is a baseline from the last pit; project next-stop wear using "
        + "twsl and twr along with stint m.sl (stale wear when m.tws). "
        + "Compare m.ga/m.gb to r.pl for undercut/overcut; crossover m.fo vs m.pb. "
        + "OUTPUT FORMAT (single line, EXACT): "
        + "<ACTION> — <TIMING> — <SERVICE> — <REASON> <TAGS>. "
        + "ACTION: STAY OUT | PIT | PIT NOW. TIMING: THIS LAP | PIT IN N LAPS | RECHECK IN N LAPS. "
        + "SERVICE: FUEL ONLY | 2 TIRES | 4 TIRES (PIT/PIT NOW only; STAY OUT = NONE). "
        + "TAGS: [fuel|tires|track|flags] and [H|M|L]. <= 20 words. "
        + f"{race_json}"
    )


class BedrockWorker(QObject):
    """
    Runs Bedrock calls off the UI thread and streams partial output.

    Design notes:
    - Uses a request_id so the UI can ignore late results from cancelled/timeouts.
    - "Cancel" is cooperative: we can't reliably abort an in-flight HTTPS request,
      but we stop emitting updates and the UI immediately resets.
    - Reuses the boto3 Bedrock client to reduce per-click overhead.
    """

    partial = Signal(int, str)
    finished = Signal(int, str)

    def __init__(self, model_id: str = DEFAULT_MODEL_ID, region_name: str = "us-east-2"):
        super().__init__()
        self._model_id = model_id
        self._region_name = region_name

        self._lock = threading.Lock()
        self._active_request_id = 0
        self._cancelled_request_ids = set()
        self._client = None

    def set_active(self, request_id: int) -> None:
        with self._lock:
            self._active_request_id = request_id

    def cancel_active(self) -> int:
        with self._lock:
            req_id = self._active_request_id
            if req_id:
                # Mark cancelled so streaming loop stops emitting updates.
                self._cancelled_request_ids.add(req_id)
                self._active_request_id = 0
            return req_id

    def _clear_cancelled(self, request_id: int) -> None:
        with self._lock:
            self._cancelled_request_ids.discard(request_id)

    def _get_client(self):
        with self._lock:
            if self._client is None:
                # Explicit timeouts prevent "wait forever" if the network/service stalls.
                self._client = boto3.client(
                    "bedrock-runtime",
                    region_name=self._region_name,
                    config=Config(
                        connect_timeout=5,
                        read_timeout=25,
                        retries={"max_attempts": 1, "mode": "standard"},
                    ),
                )
            return self._client

    def _log_enabled(self) -> bool:
        """
        Log to console only when running from source (python main.py).

        PyInstaller packaged builds (windowed) should not emit console logs.
        """
        if getattr(sys, "frozen", False):
            return False
        return True

    def invoke_ai(self, request_id: int, race_json: str, mode: str = "live") -> None:
        def run():
            try:
                token = os.getenv("IRACING_BEDROCK_TOKEN")
                if not token:
                    msg = "Error: IRACING_BEDROCK_TOKEN not found in environment."
                    if self._log_enabled():
                        print(f"[ERROR] {msg}")
                    self.finished.emit(request_id, msg)
                    return

                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        return

                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
                client = self._get_client()

                m = (mode or "live").lower()
                if m not in ("live", "strategy"):
                    m = "live"
                prompt = _build_engineer_prompt(m, f"Data: {race_json}")
                max_out = 220 if m == "strategy" else 100

                body = json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": max_out,
                        "messages": [{"role": "user", "content": prompt}],
                    }
                )

                if self._log_enabled():
                    print(f"[DEBUG] Sending data to AI: {race_json[:120]}...")

                response = client.invoke_model_with_response_stream(body=body, modelId=self._model_id)
                stream = response.get("body")

                advice_parts = []

                def is_cancelled() -> bool:
                    with self._lock:
                        return request_id in self._cancelled_request_ids

                stream_started = time.monotonic()
                stream_hard_stop = False

                def should_stop_iteration() -> bool:
                    nonlocal stream_hard_stop
                    if time.monotonic() - stream_started > STREAM_MAX_SECONDS:
                        stream_hard_stop = True
                        return True
                    return is_cancelled()

                if stream is not None:
                    for event in stream:
                        if should_stop_iteration():
                            break
                        if not isinstance(event, dict) or "chunk" not in event:
                            continue
                        chunk = event.get("chunk") or {}
                        b = chunk.get("bytes")
                        if not b:
                            continue
                        try:
                            payload = json.loads(b.decode("utf-8"))
                        except Exception:
                            continue

                        if isinstance(payload, dict):
                            etype = str(payload.get("type") or "").lower()
                            if etype == "message_stop":
                                break

                        # Bedrock/Anthropic streaming sends many event types; we only
                        # extract incremental text deltas to build the user-visible advice.
                        text = None
                        if isinstance(payload, dict):
                            delta = payload.get("delta")
                            if isinstance(delta, dict):
                                text = delta.get("text")
                            if text is None and "text" in payload:
                                text = payload.get("text")

                        if text:
                            advice_parts.append(text)
                            current = "".join(advice_parts).strip()
                            if current:
                                self.partial.emit(request_id, current)

                    if is_cancelled():
                        self._clear_cancelled(request_id)
                        return

                advice = "".join(advice_parts).strip()
                if stream_hard_stop and advice:
                    advice = advice + " [stream cap]"
                elif stream_hard_stop and not advice:
                    advice = "AI Error: Stream timed out."

                if not advice:
                    advice = "AI Error: Empty response."

                if self._log_enabled():
                    print(f"[AI] {advice}")
                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        self._clear_cancelled(request_id)
                        return

                self.finished.emit(request_id, advice)
                self._clear_cancelled(request_id)
            except Exception as e:
                if self._log_enabled():
                    print(f"[ERROR] {str(e)}")
                with self._lock:
                    if request_id in self._cancelled_request_ids:
                        self._clear_cancelled(request_id)
                        return
                self.finished.emit(request_id, f"AI Error: {str(e)}")
                self._clear_cancelled(request_id)

        threading.Thread(target=run, daemon=True).start()

