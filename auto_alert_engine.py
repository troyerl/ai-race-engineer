"""
Auto pit-alert orchestration (CALCULATIONS.md §10–§11, ui._check_auto_strategy).

Pure logic for lap/caution transitions, should_auto_alert gating, and call dedup.
The Qt overlay calls this from ui.py; unit tests exercise it without QWidget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy_engine import advice_call_line, incident_push_advice, resolve_live_advice, run_strategy, should_auto_alert


@dataclass
class AutoMonitorState:
    last_lap: int | None = None
    last_caution: bool = False
    caution_announced: bool = False


@dataclass(frozen=True)
class AutoAlertDecision:
    deliver: bool
    advice: str | None = None
    call_line: str | None = None
    reason: str = ""


def reset_auto_monitor_state() -> AutoMonitorState:
    return AutoMonitorState()


def evaluate_auto_alert_tick(
    state: AutoMonitorState,
    *,
    lap: int | None,
    is_caution: bool,
    telemetry: dict[str, Any],
    last_delivered_call_line: str = "",
) -> tuple[AutoMonitorState, AutoAlertDecision]:
    """
    One monitor tick: react to lap or caution transitions only.

    Returns updated monitor state and whether to deliver advice to the overlay/voice.
    """
    if lap is None:
        return state, AutoAlertDecision(deliver=False, reason="no_lap")

    lap_changed = state.last_lap is None or lap != state.last_lap
    caution_changed = is_caution != state.last_caution
    if not lap_changed and not caution_changed:
        return state, AutoAlertDecision(deliver=False, reason="no_transition")

    caution_started = caution_changed and is_caution
    caution_ended = caution_changed and not is_caution

    new_state = AutoMonitorState(
        last_lap=lap,
        last_caution=is_caution,
        caution_announced=state.caution_announced,
    )
    if not is_caution:
        new_state.caution_announced = False

    incident = incident_push_advice(telemetry)
    advice = incident or resolve_live_advice(telemetry, mode="live")

    if not should_auto_alert(
        telemetry,
        advice,
        caution_started=caution_started,
        caution_ended=caution_ended,
    ):
        return new_state, AutoAlertDecision(
            deliver=False,
            advice=advice,
            call_line=advice_call_line(advice),
            reason="should_not_alert",
        )

    call_line = advice_call_line(advice)

    if caution_started:
        if new_state.caution_announced:
            return new_state, AutoAlertDecision(
                deliver=False,
                advice=advice,
                call_line=call_line,
                reason="caution_already_announced",
            )
        if call_line and call_line == last_delivered_call_line:
            new_state.caution_announced = True
            return new_state, AutoAlertDecision(
                deliver=False,
                advice=advice,
                call_line=call_line,
                reason="duplicate_caution_call",
            )
        new_state.caution_announced = True
    elif call_line and call_line == last_delivered_call_line:
        return new_state, AutoAlertDecision(
            deliver=False,
            advice=advice,
            call_line=call_line,
            reason="duplicate_call",
        )

    return new_state, AutoAlertDecision(
        deliver=True,
        advice=advice,
        call_line=call_line,
        reason="deliver",
    )
