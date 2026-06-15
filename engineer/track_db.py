"""
Local SQLite cache for per-track pit loss, pit lane metadata, and learned corner sectors.

Falls back to ``race_constants.get_default_pit_loss_seconds`` when no row exists.
Database path: ``~/.ai_race_engineer_tracks.db``
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .race_constants import (
    HIGH_LAT_SECTOR_END,
    HIGH_LAT_SECTOR_START,
    PIT_LOSS_SHORT_MAX_LENGTH_MI,
    get_default_pit_loss_seconds,
    resolve_track_length_miles,
)

DEFAULT_DB_PATH = Path.home() / ".ai_race_engineer_tracks.db"

# Curated seed rows: (display name, pit loss sec, length mi, is_oval).
_SEED_PIT_LOSS: dict[str, tuple[str, float, float | None, bool]] = {
    "bristol_motorspeedway": ("Bristol Motor Speedway", 42.0, 0.533, True),
    "charlotte_motor_speedway": ("Charlotte Motor Speedway", 46.0, 1.5, True),
    "daytona_international_speedway": ("Daytona International Speedway", 58.0, 2.5, True),
    "indianapolis_motor_speedway": ("Indianapolis Motor Speedway", 58.0, 2.5, True),
    "iowa_speedway": ("Iowa Speedway", 42.0, 0.875, True),
    "martinsville_speedway": ("Martinsville Speedway", 42.0, 0.526, True),
    "michigan_international_speedway": ("Michigan International Speedway", 58.0, 2.0, True),
    "phoenix_raceway": ("Phoenix Raceway", 46.0, 1.0, True),
    "richmond_raceway": ("Richmond Raceway", 46.0, 0.75, True),
    "talladega_superspeedway": ("Talladega Superspeedway", 58.0, 2.66, True),
    "watkins_glen_international": ("Watkins Glen International", 46.0, 2.45, False),
    "road_america": ("Road America", 52.0, 4.048, False),
    "laguna_seca": ("WeatherTech Raceway at Laguna Seca", 48.0, 2.238, False),
    "nurburgring_combined": ("Nürburgring Combined", 50.0, 3.2, False),
    "spa_francorchamps": ("Circuit de Spa-Francorchamps", 50.0, 4.352, False),
    "suzuka_international_racing_course": ("Suzuka International Racing Course", 48.0, 3.608, False),
}


def normalize_track_key(name: str | None) -> str:
    if not name or not str(name).strip():
        return "unknown"
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower())
    return slug.strip("_")[:80] or "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TrackProfile:
    track_key: str
    display_name: str
    length_mi: float | None
    pit_loss_sec: float | None
    pit_speed_limit_mph: float | None
    pit_lane_length_m: float | None
    high_lat_sector_start: float | None
    high_lat_sector_end: float | None
    sector_peak_lat_g: float | None
    is_oval: bool
    pit_loss_source: str
    sector_source: str

    def sector_bounds(self) -> tuple[float, float]:
        start = self.high_lat_sector_start
        end = self.high_lat_sector_end
        if (
            isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and 0.0 <= float(start) < float(end) <= 1.0
        ):
            return float(start), float(end)
        return HIGH_LAT_SECTOR_START, HIGH_LAT_SECTOR_END


class SectorLearner:
    """Accumulate peak lateral-G per lap-distance bin during practice laps."""

    BIN_COUNT = 40
    MIN_LAT_G = 1.2
    HALF_WIDTH_BINS = 2

    def __init__(self) -> None:
        self._bins = [0.0] * self.BIN_COUNT
        self._sample_count = 0

    def reset(self) -> None:
        self._bins = [0.0] * self.BIN_COUNT
        self._sample_count = 0

    @property
    def sample_count(self) -> int:
        return self._sample_count

    def observe(self, lap_dist_pct: float, lat_g: float) -> None:
        if not (0.0 <= lap_dist_pct <= 1.0):
            return
        if lat_g <= 0.0:
            return
        idx = min(self.BIN_COUNT - 1, int(lap_dist_pct * self.BIN_COUNT))
        if lat_g > self._bins[idx]:
            self._bins[idx] = lat_g
        self._sample_count += 1

    def compute_sector(self) -> tuple[float, float, float] | None:
        """
        Return (sector_start, sector_end, peak_lat_g) or None if insufficient data.

        Finds the strongest lateral-G bin and expands by ``HALF_WIDTH_BINS``.
        """
        if self._sample_count < 20:
            return None
        peak_idx = max(range(self.BIN_COUNT), key=lambda i: self._bins[i])
        peak = self._bins[peak_idx]
        if peak < self.MIN_LAT_G:
            return None
        lo = max(0, peak_idx - self.HALF_WIDTH_BINS)
        hi = min(self.BIN_COUNT - 1, peak_idx + self.HALF_WIDTH_BINS)
        bin_w = 1.0 / self.BIN_COUNT
        return lo * bin_w, (hi + 1) * bin_w, peak


class TrackDatabase:
    _lock = threading.Lock()

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH
        self._ensure_schema()
        self._seed_if_empty()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tracks (
                        track_key TEXT PRIMARY KEY,
                        display_name TEXT,
                        length_mi REAL,
                        pit_loss_sec REAL,
                        pit_speed_limit_mph REAL,
                        pit_lane_length_m REAL,
                        high_lat_sector_start REAL,
                        high_lat_sector_end REAL,
                        sector_peak_lat_g REAL,
                        sector_sample_count INTEGER DEFAULT 0,
                        pit_loss_source TEXT,
                        sector_source TEXT,
                        is_oval INTEGER DEFAULT 0,
                        updated_at TEXT
                    )
                    """
                )
                try:
                    conn.execute("ALTER TABLE tracks ADD COLUMN is_oval INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                conn.commit()
        self._patch_oval_seed_flags()

    def _patch_oval_seed_flags(self) -> None:
        """Ensure seeded oval tracks have is_oval=1 even on older DB files."""
        with self._lock:
            with self._connect() as conn:
                for key, (_display, _pit, _len_mi, is_oval) in _SEED_PIT_LOSS.items():
                    if is_oval:
                        conn.execute(
                            "UPDATE tracks SET is_oval = 1 WHERE track_key = ?",
                            (key,),
                        )
                conn.commit()

    def _seed_if_empty(self) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()
            if row and int(row["n"]) > 0:
                return
        for key, (display, pit_loss, length_mi, is_oval) in _SEED_PIT_LOSS.items():
            self.upsert_track(
                track_key=key,
                display_name=display,
                length_mi=length_mi,
                pit_loss_sec=pit_loss,
                pit_loss_source="seed",
                is_oval=is_oval,
            )

    def get_profile(self, track_key: str) -> TrackProfile | None:
        key = normalize_track_key(track_key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE track_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_profile(row)

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> TrackProfile:
        return TrackProfile(
            track_key=str(row["track_key"]),
            display_name=str(row["display_name"] or row["track_key"]),
            length_mi=row["length_mi"],
            pit_loss_sec=row["pit_loss_sec"],
            pit_speed_limit_mph=row["pit_speed_limit_mph"],
            pit_lane_length_m=row["pit_lane_length_m"],
            high_lat_sector_start=row["high_lat_sector_start"],
            high_lat_sector_end=row["high_lat_sector_end"],
            sector_peak_lat_g=row["sector_peak_lat_g"],
            is_oval=bool(row["is_oval"]) if "is_oval" in row.keys() else False,
            pit_loss_source=str(row["pit_loss_source"] or "unknown"),
            sector_source=str(row["sector_source"] or "unknown"),
        )

    def upsert_track(
        self,
        *,
        track_key: str,
        display_name: str | None = None,
        length_mi: float | None = None,
        pit_loss_sec: float | None = None,
        pit_speed_limit_mph: float | None = None,
        pit_lane_length_m: float | None = None,
        high_lat_sector_start: float | None = None,
        high_lat_sector_end: float | None = None,
        sector_peak_lat_g: float | None = None,
        sector_sample_count: int | None = None,
        pit_loss_source: str | None = None,
        sector_source: str | None = None,
        is_oval: bool | None = None,
    ) -> None:
        key = normalize_track_key(track_key)
        existing = self.get_profile(key)
        with self._lock:
            with self._connect() as conn:
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO tracks (
                            track_key, display_name, length_mi, pit_loss_sec,
                            pit_speed_limit_mph, pit_lane_length_m,
                            high_lat_sector_start, high_lat_sector_end,
                            sector_peak_lat_g, sector_sample_count,
                            pit_loss_source, sector_source, is_oval, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            key,
                            display_name or track_key,
                            length_mi,
                            pit_loss_sec,
                            pit_speed_limit_mph,
                            pit_lane_length_m,
                            high_lat_sector_start,
                            high_lat_sector_end,
                            sector_peak_lat_g,
                            sector_sample_count or 0,
                            pit_loss_source or "unknown",
                            sector_source or "unknown",
                            1 if is_oval else 0,
                            _utc_now(),
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE tracks SET
                            display_name = COALESCE(?, display_name),
                            length_mi = COALESCE(?, length_mi),
                            pit_loss_sec = COALESCE(?, pit_loss_sec),
                            pit_speed_limit_mph = COALESCE(?, pit_speed_limit_mph),
                            pit_lane_length_m = COALESCE(?, pit_lane_length_m),
                            high_lat_sector_start = COALESCE(?, high_lat_sector_start),
                            high_lat_sector_end = COALESCE(?, high_lat_sector_end),
                            sector_peak_lat_g = COALESCE(?, sector_peak_lat_g),
                            sector_sample_count = COALESCE(?, sector_sample_count),
                            pit_loss_source = COALESCE(?, pit_loss_source),
                            sector_source = COALESCE(?, sector_source),
                            is_oval = CASE WHEN ? IS NULL THEN is_oval ELSE ? END,
                            updated_at = ?
                        WHERE track_key = ?
                        """,
                        (
                            display_name,
                            length_mi,
                            pit_loss_sec,
                            pit_speed_limit_mph,
                            pit_lane_length_m,
                            high_lat_sector_start,
                            high_lat_sector_end,
                            sector_peak_lat_g,
                            sector_sample_count,
                            pit_loss_source,
                            sector_source,
                            (1 if is_oval else 0) if is_oval is not None else None,
                            (1 if is_oval else 0) if is_oval is not None else None,
                            _utc_now(),
                            key,
                        ),
                    )
                conn.commit()

    def record_measured_pit_loss(
        self,
        track_key: str,
        *,
        display_name: str | None,
        length_mi: float | None,
        pit_loss_sec: float,
    ) -> None:
        if pit_loss_sec < 5.0 or pit_loss_sec > 300.0:
            return
        self.upsert_track(
            track_key=track_key,
            display_name=display_name,
            length_mi=length_mi,
            pit_loss_sec=round(float(pit_loss_sec), 1),
            pit_loss_source="measured",
        )

    def record_learned_sector(
        self,
        track_key: str,
        *,
        display_name: str | None,
        sector_start: float,
        sector_end: float,
        peak_lat_g: float,
        sample_count: int,
    ) -> None:
        self.upsert_track(
            track_key=track_key,
            display_name=display_name,
            high_lat_sector_start=round(sector_start, 4),
            high_lat_sector_end=round(sector_end, 4),
            sector_peak_lat_g=round(peak_lat_g, 3),
            sector_sample_count=int(sample_count),
            sector_source="learned",
        )


_db_singleton: TrackDatabase | None = None
_db_singleton_lock = threading.Lock()


def get_track_database() -> TrackDatabase:
    global _db_singleton
    with _db_singleton_lock:
        if _db_singleton is None:
            _db_singleton = TrackDatabase()
        return _db_singleton


@dataclass(frozen=True)
class PitLossResolution:
    pit_loss_sec: float
    source: str
    heuristic_sec: float

    @property
    def uses_database(self) -> bool:
        return self.source in ("measured", "seed", "learned")

    def label(self) -> str:
        """Short UI label describing where pit loss came from."""
        src = self.source
        if src == "measured":
            base = f"measured {self.pit_loss_sec:.0f}s"
        elif src == "seed":
            base = f"track DB {self.pit_loss_sec:.0f}s (seed)"
        elif src == "learned":
            base = f"track DB {self.pit_loss_sec:.0f}s"
        else:
            base = f"heuristic {self.pit_loss_sec:.0f}s"
        if self.uses_database and abs(self.pit_loss_sec - self.heuristic_sec) >= 0.5:
            base += f" · was {self.heuristic_sec:.0f}s est."
        return base


def resolve_track_pit_loss_detail(
    track_name: str | None,
    track_length_miles: float | None,
    *,
    db: TrackDatabase | None = None,
) -> PitLossResolution:
    """Resolve pit loss with source metadata for UI and reports."""
    database = db or get_track_database()
    name = (track_name or "").strip()
    length_mi = resolve_track_length_miles(track_length_miles)
    heuristic = float(get_default_pit_loss_seconds(name, length_mi))
    if name:
        profile = database.get_profile(normalize_track_key(name))
        if profile is not None and profile.pit_loss_sec is not None:
            return PitLossResolution(
                pit_loss_sec=float(profile.pit_loss_sec),
                source=str(profile.pit_loss_source or "seed"),
                heuristic_sec=heuristic,
            )
    return PitLossResolution(
        pit_loss_sec=heuristic,
        source="heuristic",
        heuristic_sec=heuristic,
    )


def resolve_track_pit_loss_seconds(
    track_name: str | None,
    track_length_miles: float | None,
    *,
    db: TrackDatabase | None = None,
) -> float:
    """
    Resolve pit-road loss seconds: DB row → length/name heuristic fallback.
    """
    return resolve_track_pit_loss_detail(
        track_name, track_length_miles, db=db
    ).pit_loss_sec


def resolve_high_lat_sector(
    track_name: str | None,
    *,
    db: TrackDatabase | None = None,
) -> tuple[float, float]:
    """Return (start, end) lap-distance bounds for corner context sampling."""
    database = db or get_track_database()
    name = (track_name or "").strip()
    if not name:
        return HIGH_LAT_SECTOR_START, HIGH_LAT_SECTOR_END
    profile = database.get_profile(normalize_track_key(name))
    if profile is None:
        return HIGH_LAT_SECTOR_START, HIGH_LAT_SECTOR_END
    return profile.sector_bounds()


def is_oval_track(
    track_name: str | None,
    *,
    track_length_miles: float | None = None,
    track_type: str | None = None,
    db: TrackDatabase | None = None,
) -> bool:
    """True for oval layouts (DB flag, SDK type hint, or name/length heuristic)."""
    database = db or get_track_database()
    name = (track_name or "").strip()
    if name:
        profile = database.get_profile(normalize_track_key(name))
        if profile is not None and profile.is_oval:
            return True
    if track_type:
        tl = str(track_type).lower()
        if "oval" in tl or tl in ("2", "3"):
            return True
    if name:
        lower = name.lower()
        if any(k in lower for k in ("superspeedway", "daytona", "talladega")):
            return True
        if "speedway" in lower or "motorspeedway" in lower:
            length_mi = resolve_track_length_miles(track_length_miles)
            if length_mi <= PIT_LOSS_SHORT_MAX_LENGTH_MI + 0.15:
                return True
    return False
