"""
Predictive tire wear helpers: oval stagger and effective pace-falloff estimate.

Deterministic only — feeds ``strategy_engine._tire_pit_worth_it`` and context notes.
"""

from __future__ import annotations

from typing import Any

from .race_constants import (
    STEER_STD_ELEVATED,
    STEER_STD_HIGH,
    TRACK_TEMP_SHIFT_THRESHOLD_C,
    clamp_positive_rate,
)

# Left vs right tread delta (percent points) that suggests oval handling loss.
OVAL_STAGGER_WARN_DELTA_PCT = 3.0
OVAL_STAGGER_SEVERE_DELTA_PCT = 6.0

STAGGER_FALLOFF_MULT = 1.12
STAGGER_SEVERE_FALLOFF_MULT = 1.22
THERMAL_GREASY_FALLOFF_MULT = 1.15
STEER_HIGH_FALLOFF_MULT = 1.10
STEER_ELEVATED_FALLOFF_MULT = 1.05
TRACK_HOT_FALLOFF_MULT = 1.08
WEAR_RATE_FALLOFF_MULT = 1.10


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_stagger_from_corners(corner_wear: dict[str, float] | None) -> dict[str, float] | None:
    """
    Oval stagger proxy: average left-side vs right-side tread remaining (%).

    Returns ``{la, ra, dg}`` (left avg, right avg, left-minus-right delta) or None.
    """
    if not isinstance(corner_wear, dict):
        return None
    left_vals = [corner_wear[k] for k in ("LF", "LR") if k in corner_wear]
    right_vals = [corner_wear[k] for k in ("RF", "RR") if k in corner_wear]
    if not left_vals or not right_vals:
        return None
    la = sum(left_vals) / len(left_vals)
    ra = sum(right_vals) / len(right_vals)
    return {
        "la": round(la, 3),
        "ra": round(ra, 3),
        "dg": round(la - ra, 3),
    }


def effective_tire_falloff_s(telemetry: dict[str, Any]) -> float:
    """
    Adjust ``m.fo`` using thermal stress, steering fatigue, track temp drift,
    measured wear rates, and oval stagger when present.
    """
    m = telemetry.get("m") if isinstance(telemetry.get("m"), dict) else {}
    s = telemetry.get("s") if isinstance(telemetry.get("s"), dict) else {}
    base = _safe_float(m.get("fo"), 0.0)
    if base <= 0:
        base = 0.08

    mult = 1.0

    drv = m.get("drv") if isinstance(m.get("drv"), dict) else {}
    tsn = str(drv.get("tsn") or "").upper()
    if tsn == "GREASY":
        mult *= THERMAL_GREASY_FALLOFF_MULT
    ssr = _safe_float(drv.get("ssr"), 0.0)
    if ssr >= STEER_STD_HIGH:
        mult *= STEER_HIGH_FALLOFF_MULT
    elif ssr >= STEER_STD_ELEVATED:
        mult *= STEER_ELEVATED_FALLOFF_MULT

    tenv = s.get("tenv") if isinstance(s.get("tenv"), dict) else {}
    if _safe_float(tenv.get("dt"), 0.0) >= TRACK_TEMP_SHIFT_THRESHOLD_C:
        mult *= TRACK_HOT_FALLOFF_MULT

    twr = m.get("twr") if isinstance(m.get("twr"), dict) else {}
    if twr:
        rates = [_safe_float(v) for v in twr.values() if _safe_float(v) > 0]
        if rates:
            avg_rate = sum(rates) / len(rates)
            if avg_rate >= 0.35:
                mult *= WEAR_RATE_FALLOFF_MULT

    stg = m.get("stg") if isinstance(m.get("stg"), dict) else {}
    dg = abs(_safe_float(stg.get("dg"), 0.0))
    if dg >= OVAL_STAGGER_SEVERE_DELTA_PCT:
        mult *= STAGGER_SEVERE_FALLOFF_MULT
    elif dg >= OVAL_STAGGER_WARN_DELTA_PCT:
        mult *= STAGGER_FALLOFF_MULT

    return round(clamp_positive_rate(base * mult, minimum=0.01, default=0.08), 4)
