"""
Simulated green-flag fuel EMA for offline race simulation.

Mirrors ``TelemetryTracker._tick_fuel_per_lap_ema`` / ``_reset_fuel_ema_for_green_restart``
so sim packets include ``m.ful``, ``m.fpe``, and ``m.fpl`` — the same fields live
telemetry uses for ``green_flag_fuel_laps_from_telemetry()``.
"""

from __future__ import annotations

from engineer.race_constants import FUEL_EMA_ALPHA, _L_TO_US_GAL

# Nominal liters burned per full green-flag lap at scenario fuel_tank_laps capacity.
SIM_LITERS_PER_GREEN_LAP = 2.0


def _liters_to_us_gal(liters: float) -> float:
    return float(liters) * _L_TO_US_GAL


class SimFuelTracker:
    """Track fuel liters and green-flag burn EMA across simulated laps."""

    def __init__(
        self,
        *,
        tank_laps: float,
        fuel_laps_left: float,
        liters_per_green_lap: float = SIM_LITERS_PER_GREEN_LAP,
    ) -> None:
        self._lit_per_green_lap = max(0.01, float(liters_per_green_lap))
        self._tank_liters = max(0.0, float(tank_laps)) * self._lit_per_green_lap
        self._fuel_liters = max(0.0, float(fuel_laps_left)) * self._lit_per_green_lap
        self._fuel_per_lap_ema_L: float | None = None
        self._fuel_prev_lap: int | None = None
        self._fuel_prev_level_L: float | None = None

    @property
    def fuel_liters(self) -> float:
        return self._fuel_liters

    def sync_from_fuel_laps(self, fuel_laps_left: float) -> None:
        """Align tank liters with abstract sim fuel-lap counter after physics."""
        self._fuel_liters = max(0.0, float(fuel_laps_left)) * self._lit_per_green_lap

    def reset_for_green_restart(self, lap: int) -> None:
        """Purge caution-skewed burn history when yellow lifts (§2.3)."""
        self._fuel_per_lap_ema_L = None
        self._fuel_prev_lap = lap
        self._fuel_prev_level_L = self._fuel_liters

    def on_lap_boundary(
        self,
        lap: int,
        *,
        exclude_sample: bool = False,
    ) -> None:
        """Update EMA from lap-boundary fuel delta (call after physics for this lap)."""
        if lap < 1:
            return
        pl = self._fuel_prev_lap
        pf = self._fuel_prev_level_L
        fuel_level = self._fuel_liters

        if pl is not None and pf is not None:
            if lap < pl:
                self._fuel_per_lap_ema_L = None
            elif lap > pl and not exclude_sample:
                dl = lap - pl
                df = pf - fuel_level
                if dl >= 1 and df > 0.02:
                    sample = df / float(dl)
                    cap = self._tank_liters if self._tank_liters > 0 else None
                    if cap is None or sample <= cap * 0.98:
                        alpha = FUEL_EMA_ALPHA
                        ema = self._fuel_per_lap_ema_L
                        self._fuel_per_lap_ema_L = (
                            sample if ema is None else (alpha * sample + (1.0 - alpha) * ema)
                        )

        self._fuel_prev_lap = lap
        self._fuel_prev_level_L = fuel_level

    def packet_m_fields(self) -> dict[str, float | None]:
        """Compact ``m`` keys matching live telemetry packet shape."""
        inst = self._lit_per_green_lap
        ema = self._fuel_per_lap_ema_L
        return {
            "ful": round(self._fuel_liters, 4),
            "fpl": round(_liters_to_us_gal(inst), 5),
            "fpe": round(_liters_to_us_gal(float(ema)), 5) if ema is not None else None,
        }
