"""Local strategy worker — same Qt signals as the old Bedrock worker, no network."""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, Signal

from strategy_engine import run_strategy


class StrategyWorker(QObject):
    partial = Signal(int, str)
    finished = Signal(int, str)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._active_request_id = 0
        self._cancelled_request_ids: set[int] = set()

    def set_active(self, request_id: int) -> None:
        with self._lock:
            self._active_request_id = request_id

    def cancel_active(self) -> int:
        with self._lock:
            req_id = self._active_request_id
            if req_id:
                self._cancelled_request_ids.add(req_id)
                self._active_request_id = 0
            return req_id

    def _is_cancelled(self, request_id: int) -> bool:
        with self._lock:
            return request_id in self._cancelled_request_ids

    def _clear_cancelled(self, request_id: int) -> None:
        with self._lock:
            self._cancelled_request_ids.discard(request_id)

    def invoke_ai(self, request_id: int, race_json: str, mode: str = "live") -> None:
        def run() -> None:
            try:
                if self._is_cancelled(request_id):
                    return
                try:
                    telemetry = json.loads(race_json)
                except json.JSONDecodeError:
                    self.finished.emit(request_id, "Error: invalid telemetry packet.")
                    return
                if not isinstance(telemetry, dict):
                    self.finished.emit(request_id, "Error: telemetry packet must be a JSON object.")
                    return

                mode = str(telemetry.get("x", {}).get("md") or mode)
                track = None
                if isinstance(telemetry.get("sy"), dict):
                    wi = telemetry["sy"].get("WeekendInfo")
                    if isinstance(wi, dict):
                        track = str(wi.get("TrackDisplayName") or wi.get("TrackName") or "") or None
                advice = run_strategy(telemetry, mode=mode, track_name=track)
                if self._is_cancelled(request_id):
                    return
                self.partial.emit(request_id, advice)
                self.finished.emit(request_id, advice)
            except Exception as exc:
                if not self._is_cancelled(request_id):
                    self.finished.emit(request_id, f"Error: strategy engine failed ({exc}).")
            finally:
                self._clear_cancelled(request_id)

        threading.Thread(target=run, daemon=True).start()
