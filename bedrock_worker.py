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


def _estimate_tokens_chars(text: str) -> int:
    """Rough tokenizer-free estimate (~4 chars/token for English)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _accum_usage_from_stream_payload(payload: dict, acc: dict) -> None:
    """
    Merge Anthropic/Bedrock streaming usage fields into acc (input_tokens / output_tokens).
    Values are cumulative maxima across chunks when the API reports running totals.
    """
    u = payload.get("usage")
    if isinstance(u, dict):
        it = u.get("input_tokens")
        ot = u.get("output_tokens")
        if isinstance(it, (int, float)):
            acc["input_tokens"] = max(acc["input_tokens"], int(it))
        if isinstance(ot, (int, float)):
            acc["output_tokens"] = max(acc["output_tokens"], int(ot))

    ut = str(payload.get("type") or "").lower()
    if ut == "message_start":
        msg = payload.get("message")
        if isinstance(msg, dict):
            mu = msg.get("usage")
            if isinstance(mu, dict):
                it = mu.get("input_tokens")
                ot = mu.get("output_tokens")
                if isinstance(it, (int, float)):
                    acc["input_tokens"] = max(acc["input_tokens"], int(it))
                if isinstance(ot, (int, float)):
                    acc["output_tokens"] = max(acc["output_tokens"], int(ot))

    meta = payload.get("amazon-bedrock-invocationMetrics")
    if isinstance(meta, dict):
        it = meta.get("inputTokenCount")
        ot = meta.get("outputTokenCount")
        if isinstance(it, (int, float)):
            acc["input_tokens"] = max(acc["input_tokens"], int(it))
        if isinstance(ot, (int, float)):
            acc["output_tokens"] = max(acc["output_tokens"], int(ot))


def _build_engineer_prompt(mode: str, race_json: str) -> str:
    schema = (
        "Data is compact JSON: "
        "x{md=live|strategy,u=us_fuel_gal_and_lb_hr}; "
        "s{st=session_state,tr=time_remain_s_omit_if_placeholder,lt=laps_total,ot=is_on_track,ig=is_in_garage}; "
        "m{l=lap,lr=laps_remain,p=pos,fu=fuel_USgal_remaining,fph=fuel_burn_lb_per_hr_SDK_scaled,"
        "fpe=fuel_USgal_per_lap_ema_if_present,fpl=fuel_USgal_per_lap_est,fcq=fuel_est_quality_hi|med|low,"
        "fl=fuel_laps_left_est,mk=can_make,ls=laps_short,lp=last_pit_lap,t=lap_times_s,pc=pace_s,fg=flags,fs=flag_state,"
        "pr=on_pit_road,ps=in_pit_stall,rr=req_repair_s_left,or=opt_repair_s_left,sl=stint_laps,ga=gap_ahead_s,gb=gap_behind_s,bl=best_lap_s,fo=falloff_s,"
        "pb=pit_payback_laps,pw=pit_window_open,pwu=laps_until_window,tw,tws,twsl,twr}; "
        "r{pl=pit_loss_sec,ts=tire_sets_avail,fc=fuel_tank_USgal_capacity,ftl=laps_per_full_tank_fuel_est}; "
        "rv=rivals; f=field. "
    )
    base = (
        "You are a Lead Race Engineer. "
        + schema
        + "Use x.md exactly: if strategy, pre-race plan only; if live, in-race advice only. "
        + "Audience: US fan/driver — American motorsports terms (pit road, yellow/caution, green flag, "
        + "\"pass-through\"/wave-around where apt); tires not tyres. When citing fuel amounts/rates use gal or lb/hr "
        + "to match x.u=us — avoid liters/kg unless repeating telemetry verbatim is unavoidable. "
    )

    if mode == "strategy":
        return (
            base
            + "CONTEXT: DRIVER IS OFF TRACK (garage/grid/prep). Build a PRE-RACE plan. "
            + "Goal: approximate laps BETWEEN pit stops optimizing fuel tank (r.ftl, r.fc, m.fpl/m.fpe) "
            + "and tire life using r.ts (available sets); mention when to take FUEL ONLY vs 2 vs 4 tires if helpful. "
            + "Fuel fields are already US customary (gal, lb/hr); prefer m.fpe when present; respect m.fcq. "
            + "Assume green-flag racing unless s says otherwise; note estimates are approximate. "
            + "OUTPUT FORMAT — exactly 5 lines (one newline between each line; no blank lines): "
            + "Line1: FUEL: <pit-every-N-laps style plan using r.ftl/m.fpl/s.lt> "
            + "Line2: TIRES: <every M laps or align with fuel; use r.ts> "
            + "Line3: STOPS: <~K stops> "
            + "Line4: NOTE: <one caveat, <=10 words> "
            + "Line5: TRIGGER: FUEL|TIRES|REPAIR  CONF: H|M|L "
            + "Keep lines short (labels FUEL/TIRES/STOPS/NOTE/TRIGGER exactly); <=40 words total. "
            f"{race_json}"
        )

    return (
        base
        + "Primary goal: Optimize track position vs fuel/tire life (live race). "
        + "Fuel: x.u=us — m.fph lb/hr (from SDK kg/h), m.fpl/m.fpe US gal/lap (liters converted + lap EMA); "
        + "m.fcq hi|med|low. Never cite absurd fuel laps vs m.lr "
        + "(e.g. fl many multiples of lr) unless fcq=hi and pr=false — when fcq low or m.pr true, treat fuel range as uncertain. "
        + "Repairs: if m.rr > 0 and m.ps true, you are stuck until it hits 0 — treat as mandatory service. "
        + "Use m.fs (GREEN/CAUTION/UNKNOWN) not m.fg. "
        + "If flag_state is CAUTION: default to STAY OUT unless fuel requires a stop or pitting gains clear track position. "
        + "If flag_state is GREEN or UNKNOWN: do NOT recommend pitting unless we are inside the pit window or fuel requires it. "
        + "Use tires: tire_wear_last_known is a baseline from the last pit; project next-stop wear using "
        + "twsl and twr along with stint m.sl (stale wear when m.tws). "
        + "Compare m.ga/m.gb to r.pl for undercut/overcut; crossover m.fo vs m.pb. "
        + "OUTPUT FORMAT — exactly 3 lines (one newline between lines; no blank lines; easy to read at speed): "
        + "Line1: <ACTION> — <TIMING> — <SERVICE> "
        + "  ACTION: STAY OUT | PIT | PIT NOW. TIMING: THIS LAP | PIT IN N LAPS | RECHECK IN N LAPS. "
        + "  SERVICE: FUEL ONLY | 2 TIRES | 4 TIRES (PIT/PIT NOW only; STAY OUT = NONE). "
        + "Line2: WHY: <plain English only — max ~14 words; short phrases separated by semicolons OK; "
        + "no JSON keys, no m./r. codes, no engineer shorthand>. "
        + "Line3: TRIGGER: FUEL|TIRES|TRACK|FLAGS|REPAIR  CONF: H|M|L "
        + "Do not put WHY text on line1; keep line1 to call + timing + service only; <=34 words lines 1–2. "
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
    # Reported when a request completes without cooperative cancel (includes AI Error text replies).
    usage_report = Signal(int, int)

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
                max_out = 220 if m == "strategy" else 130

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
                usage_acc = {"input_tokens": 0, "output_tokens": 0}

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
                            _accum_usage_from_stream_payload(payload, usage_acc)
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

                inp_u = int(usage_acc["input_tokens"])
                outp_u = int(usage_acc["output_tokens"])
                if inp_u <= 0:
                    inp_u = _estimate_tokens_chars(prompt)
                if outp_u <= 0 and advice and not advice.startswith("AI Error:"):
                    outp_u = _estimate_tokens_chars(advice)

                self.usage_report.emit(inp_u, outp_u)
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

