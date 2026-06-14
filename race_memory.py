"""Long-horizon race context accumulated on the engineer (receiver) PC."""

from __future__ import annotations

import copy
from collections import deque
from typing import Any


def _as_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        i = int(v)
        return i
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _trend_label(delta: float, *, eps: float, improving_negative: bool = False) -> str:
    if abs(delta) <= eps:
        return "stable"
    if improving_negative:
        return "improving" if delta < 0 else "degrading"
    return "rising" if delta > 0 else "falling"


class RaceMemory:
    """
    Builds a compact history block (packet key ``h``) from streamed snapshots.

    The sim PC keeps snapshots lightweight; this module merges ~250ms feeds into
    lap rollups, caution/pit events, extended pace history, and trend features.
    """

    MAX_LAP_ROLLUPS = 14
    MAX_EVENTS = 10
    MAX_YOU_LAPS = 32
    MAX_FIELD_LAPS = 16
    MAX_ADVICE = 6

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._track: str | None = None
        self._session_key: str | None = None

        self._last_lap: int | None = None
        self._last_fs: str | None = None
        self._last_pr = False
        self._last_ps = False
        self._last_pos: int | None = None
        self._last_fuel: float | None = None
        self._last_you_lap_count = 0

        self._you_laps: deque[float] = deque(maxlen=self.MAX_YOU_LAPS)
        self._field_laps: dict[str, deque[float]] = {}
        self._lap_rollups: deque[dict[str, Any]] = deque(maxlen=self.MAX_LAP_ROLLUPS)
        self._events: deque[dict[str, Any]] = deque(maxlen=self.MAX_EVENTS)
        self._advice_log: deque[dict[str, Any]] = deque(maxlen=self.MAX_ADVICE)

        self._caution_count = 0
        self._laps_since_caution = 0
        self._green_run_start_lap: int | None = None
        self._pit_count = 0
        self._best_pos: int | None = None
        self._worst_pos: int | None = None
        self._first_pos: int | None = None

        self._pos_by_lap: dict[int, int] = {}
        self._fuel_at_lap: dict[int, float] = {}
        self._ga_at_lap: dict[int, float] = {}
        self._gb_at_lap: dict[int, float] = {}
        self._fpl_samples: deque[float] = deque(maxlen=24)
        self._fuel_burn_hist: deque[float] = deque(maxlen=16)
        self._pit_tire_wear_hist: deque[dict[str, Any]] = deque(maxlen=10)
        self._rej_hist: deque[dict[str, Any]] = deque(maxlen=8)

    def _session_identity(self, msg: dict, pkt: dict) -> str:
        track = str(msg.get("track") or "").strip()
        s = pkt.get("s") if isinstance(pkt.get("s"), dict) else {}
        ty = str(s.get("ty") or s.get("st") or "")
        ses = str(s.get("ses") or "")
        return f"{track}|{ty}|{ses}"

    def _maybe_reset_session(self, msg: dict, pkt: dict) -> None:
        key = self._session_identity(msg, pkt)
        track = msg.get("track")
        track_s = track if isinstance(track, str) and track.strip() else None
        m = pkt.get("m") if isinstance(pkt.get("m"), dict) else {}
        lap = _as_int(m.get("l"))

        new_session = False
        if self._session_key is None:
            new_session = True
        elif key != self._session_key and track_s and track_s != self._track:
            new_session = True
        elif lap is not None and self._last_lap is not None and lap < self._last_lap - 1:
            new_session = True

        if new_session:
            self.reset()
            self._session_key = key
            self._track = track_s

    def _merge_lap_lists(self, target: deque[float], times: Any) -> None:
        if not isinstance(times, list):
            return
        clean: list[float] = []
        for t in times:
            f = _as_float(t)
            if f is not None and f > 0:
                clean.append(round(f, 3))
        if not clean:
            return

        existing = list(target)
        if not existing:
            target.extend(clean[-target.maxlen :])
            return

        for start in range(len(existing) - 1, -1, -1):
            suffix = existing[start:]
            if clean[: len(suffix)] == suffix:
                for lap_t in clean[len(suffix) :]:
                    target.append(lap_t)
                return

        if clean[-1] != existing[-1]:
            target.append(clean[-1])

    def _merge_field_history(self, pkt: dict) -> None:
        f = pkt.get("f")
        if not isinstance(f, dict):
            return
        for label, times in f.items():
            if not isinstance(label, str) or not isinstance(times, list):
                continue
            dq = self._field_laps.get(label)
            if dq is None:
                dq = deque(maxlen=self.MAX_FIELD_LAPS)
                self._field_laps[label] = dq
            self._merge_lap_lists(dq, times)

    def _record_event(self, kind: str, lap: int | None, **extra: Any) -> None:
        ev: dict[str, Any] = {"t": kind}
        if lap is not None:
            ev["l"] = lap
        ev.update({k: v for k, v in extra.items() if v is not None})
        if self._events and self._events[-1] == ev:
            return
        self._events.append(ev)

    def record_snapshot(self, msg: dict) -> None:
        pkt = msg.get("packet")
        if not isinstance(pkt, dict):
            return

        self._maybe_reset_session(msg, pkt)
        m = pkt.get("m") if isinstance(pkt.get("m"), dict) else {}

        lap = _as_int(m.get("l"))
        pos = _as_int(m.get("p"))
        fs = str(m.get("fs") or "")
        pr = bool(m.get("pr"))
        ps = bool(m.get("ps"))
        fuel = _as_float(m.get("fu"))
        ga = _as_float(m.get("ga"))
        gb = _as_float(m.get("gb"))
        fpl = _as_float(m.get("fpe")) or _as_float(m.get("fpl"))
        fbl = _as_float(m.get("fbl"))
        fup = _as_float(m.get("fup"))
        pc = m.get("pc") if isinstance(m.get("pc"), dict) else {}
        fi = pkt.get("fi") if isinstance(pkt.get("fi"), dict) else {}
        rej = fi.get("rej") if isinstance(fi.get("rej"), dict) else None
        hd = fi.get("hd") if isinstance(fi.get("hd"), dict) else None

        twl = pkt.get("twl")
        if isinstance(twl, list) and twl:
            for entry in twl:
                if isinstance(entry, dict) and entry not in list(self._pit_tire_wear_hist):
                    self._pit_tire_wear_hist.append(entry)

        if isinstance(rej, dict) and rej.get("v"):
            if not self._rej_hist or self._rej_hist[-1] != rej:
                self._rej_hist.append(dict(rej))

        you_times = m.get("t")
        if isinstance(you_times, list):
            prev_n = len(self._you_laps)
            self._merge_lap_lists(self._you_laps, you_times)
            if len(self._you_laps) > prev_n and lap is not None:
                self._last_you_lap_count = len(self._you_laps)

        self._merge_field_history(pkt)

        if pos is not None:
            if self._first_pos is None:
                self._first_pos = pos
            if self._best_pos is None or pos < self._best_pos:
                self._best_pos = pos
            if self._worst_pos is None or pos > self._worst_pos:
                self._worst_pos = pos

        if fs and self._last_fs and fs == "CAUTION" and self._last_fs != "CAUTION":
            self._caution_count += 1
            self._laps_since_caution = 0
            self._green_run_start_lap = lap
            self._record_event("caution", lap)

        if lap is not None and self._last_lap is not None and lap > self._last_lap:
            completed = self._last_lap
            rollup: dict[str, Any] = {"l": completed}
            if self._last_pos is not None:
                rollup["p"] = self._last_pos
            if self._last_fuel is not None:
                rollup["fu"] = round(self._last_fuel, 3)
            if self._last_fs:
                rollup["fs"] = self._last_fs
            avg3 = _as_float(pc.get("avg_last3_s")) if isinstance(pc, dict) else None
            if avg3 is not None:
                rollup["pc"] = round(avg3, 3)
            bl = _as_float(m.get("bl"))
            if bl is not None:
                rollup["bl"] = round(bl, 3)
            if ga is not None:
                rollup["ga"] = ga
            if gb is not None:
                rollup["gb"] = gb
            if fbl is not None:
                rollup["fbl"] = fbl
            if fup is not None:
                rollup["fup"] = fup
            if isinstance(hd, dict) and hd:
                rollup["hd"] = hd
            sl = _as_int(m.get("sl"))
            if sl is not None:
                rollup["sl"] = sl
            self._lap_rollups.append(rollup)

            if self._last_fuel is not None and fuel is not None and fuel < self._last_fuel:
                used = self._last_fuel - fuel
                if used > 0.02:
                    self._fpl_samples.append(round(used, 4))
            if fbl is not None:
                self._fuel_burn_hist.append(fbl)

            if fs != "CAUTION":
                self._laps_since_caution += lap - self._last_lap

            if self._last_pos is not None and pos is not None and pos != self._last_pos:
                self._record_event("pos", lap, from_p=self._last_pos, to_p=pos)

        if self._last_pr and not pr and lap is not None:
            self._pit_count += 1
            self._record_event("pit", lap, p=pos, fu=round(fuel, 3) if fuel is not None else None)

        if lap is not None:
            self._last_lap = lap
            if pos is not None:
                self._last_pos = pos
                self._pos_by_lap[lap] = pos
            if fuel is not None:
                self._last_fuel = fuel
                self._fuel_at_lap[lap] = fuel
            if ga is not None:
                self._ga_at_lap[lap] = ga
            if gb is not None:
                self._gb_at_lap[lap] = gb

        if fpl is not None and lap is not None and self._last_lap == lap:
            pass  # fpl samples taken on lap boundary via fuel delta

        if fs:
            self._last_fs = fs
        self._last_pr = pr
        self._last_ps = ps

    def record_advice(self, text: str, *, mode: str = "live") -> None:
        head = ""
        for line in (text or "").replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if s and not s.startswith("AI Error"):
                head = s[:120]
                break
        if not head:
            return
        entry = {"l": self._last_lap, "md": mode, "c": head}
        if self._advice_log and self._advice_log[-1].get("c") == head:
            return
        self._advice_log.append(entry)

    def _pace_trend(self, times: deque[float]) -> str | None:
        if len(times) < 6:
            return None
        arr = list(times)
        early = arr[:5]
        late = arr[-5:]
        e = sum(early) / len(early)
        l = sum(late) / len(late)
        return _trend_label(l - e, eps=0.15, improving_negative=True)

    def _gap_trend(self, by_lap: dict[int, float]) -> str | None:
        if len(by_lap) < 4:
            return None
        laps = sorted(by_lap.keys())[-6:]
        vals = [by_lap[x] for x in laps]
        if len(vals) < 4:
            return None
        delta = vals[-1] - vals[0]
        return _trend_label(delta, eps=0.25, improving_negative=True)

    def _fuel_trend(self) -> str | None:
        if len(self._fpl_samples) < 4:
            return None
        arr = list(self._fpl_samples)
        early = sum(arr[:4]) / 4
        late = sum(arr[-4:]) / 4
        return _trend_label(late - early, eps=0.03)

    def _pos_delta(self, n: int = 5) -> int | None:
        if self._last_pos is None or self._last_lap is None:
            return None
        old_lap = self._last_lap - n
        old_pos = self._pos_by_lap.get(old_lap)
        if old_pos is None:
            return None
        return old_pos - self._last_pos

    def build_history_block(self) -> dict[str, Any]:
        ss: dict[str, Any] = {}
        if self._last_lap is not None:
            ss["lc"] = max(0, self._last_lap - 1)
        if self._caution_count:
            ss["cc"] = self._caution_count
        if self._laps_since_caution:
            ss["gc"] = self._laps_since_caution
        if self._pit_count:
            ss["pc"] = self._pit_count
        if self._best_pos is not None:
            ss["bp"] = self._best_pos
        if self._worst_pos is not None:
            ss["wp"] = self._worst_pos
        pd5 = self._pos_delta(5)
        if pd5 is not None and pd5 != 0:
            ss["pd5"] = pd5

        tr: dict[str, Any] = {}
        pt = self._pace_trend(self._you_laps)
        if pt:
            tr["pc"] = pt
        ft = self._fuel_trend()
        if ft:
            tr["fpl"] = ft
        gat = self._gap_trend(self._ga_at_lap)
        if gat:
            tr["ga"] = "closing" if gat == "improving" else "opening" if gat == "degrading" else "stable"
        gbt = self._gap_trend(self._gb_at_lap)
        if gbt:
            tr["gb"] = "closing" if gbt == "improving" else "opening" if gbt == "degrading" else "stable"

        h: dict[str, Any] = {}
        if ss:
            h["ss"] = ss
        if self._you_laps:
            h["pl"] = list(self._you_laps)
        if self._field_laps:
            h["pf"] = {k: list(v) for k, v in self._field_laps.items() if v}
        if self._lap_rollups:
            h["lr"] = list(self._lap_rollups)
        if self._events:
            h["ev"] = list(self._events)
        if tr:
            h["tr"] = tr
        if self._advice_log:
            h["al"] = list(self._advice_log)
        if self._fuel_burn_hist:
            h["fbh"] = list(self._fuel_burn_hist)
        if self._pit_tire_wear_hist:
            h["twh"] = list(self._pit_tire_wear_hist)
        if self._rej_hist:
            h["rej"] = list(self._rej_hist)
        return h

    def enrich_packet(self, pkt: dict) -> dict:
        out = copy.deepcopy(pkt)
        block = self.build_history_block()
        if block:
            out["h"] = block
        return out
