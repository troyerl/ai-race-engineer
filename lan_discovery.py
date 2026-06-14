"""UDP LAN discovery for AI Race Engineer broadcaster ↔ receiver pairing."""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

DISCOVERY_PORT = 8767
BEACON_INTERVAL_MS = 2000
DEVICE_STALE_SEC = 10.0
BEACON_TYPE = "ai_race_engineer_beacon"


def local_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@dataclass
class LanDevice:
    host: str
    port: int
    name: str
    last_seen: float = field(default_factory=time.monotonic)
    iracing: bool = False
    track: str | None = None

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "iracing": self.iracing,
            "track": self.track,
        }


class BroadcasterBeacon(QObject):
    """Periodically advertises this sim PC on the LAN (run with --role broadcaster)."""

    def __init__(self, tcp_port: int, parent: QObject | None = None):
        super().__init__(parent)
        self._tcp_port = int(tcp_port)
        self._status_fn: Callable[[], dict[str, Any]] = lambda: {}
        self._name_fn: Callable[[], str] | None = None
        self._sock = QUdpSocket(self)
        self._timer = QTimer(self)
        self._timer.setInterval(BEACON_INTERVAL_MS)
        self._timer.timeout.connect(self._send_beacon)

    def set_status_provider(self, fn: Callable[[], dict[str, Any]]) -> None:
        self._status_fn = fn

    def set_name_provider(self, fn: Callable[[], str]) -> None:
        self._name_fn = fn

    def start(self) -> None:
        self._send_beacon()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _send_beacon(self) -> None:
        status = {}
        try:
            status = self._status_fn() or {}
        except Exception:
            status = {}
        host_ip = local_lan_ip()
        try:
            name = socket.gethostname() or "Sim PC"
        except Exception:
            name = "Sim PC"
        if self._name_fn is not None:
            try:
                custom = str(self._name_fn() or "").strip()
                if custom:
                    name = custom
            except Exception:
                pass
        msg = {
            "t": BEACON_TYPE,
            "v": 1,
            "host": host_ip,
            "port": self._tcp_port,
            "name": name,
            "iracing": bool(status.get("iracing")),
            "track": status.get("track"),
        }
        data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        try:
            self._sock.writeDatagram(data, QHostAddress(QHostAddress.SpecialAddress.Broadcast), DISCOVERY_PORT)
        except Exception:
            pass
        if host_ip and host_ip != "127.0.0.1":
            try:
                self._sock.writeDatagram(data, QHostAddress(host_ip), DISCOVERY_PORT)
            except Exception:
                pass


class LanDeviceDiscovery(QObject):
    """Listens for broadcaster beacons on the engineer (receiver) PC."""

    devices_changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._devices: dict[str, LanDevice] = {}
        self._sock = QUdpSocket(self)
        self._sock.readyRead.connect(self._on_ready_read)
        self._expire_timer = QTimer(self)
        self._expire_timer.setInterval(2000)
        self._expire_timer.timeout.connect(self._expire_stale)

    def start(self) -> None:
        try:
            self._sock.bind(
                QHostAddress(QHostAddress.SpecialAddress.Any),
                DISCOVERY_PORT,
                QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
            )
        except Exception:
            pass
        self._expire_timer.start()

    def stop(self) -> None:
        self._expire_timer.stop()
        self._sock.close()

    def devices(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        alive = [d for d in self._devices.values() if (now - d.last_seen) <= DEVICE_STALE_SEC]
        alive.sort(key=lambda d: (not d.iracing, d.name.lower(), d.host))
        return [d.to_dict() for d in alive]

    def _on_ready_read(self) -> None:
        changed = False
        while self._sock.hasPendingDatagrams():
            datagram = self._sock.receiveDatagram()
            payload = bytes(datagram.data())
            sender = datagram.senderAddress().toString()
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("t") != BEACON_TYPE:
                continue
            host = str(msg.get("host") or "").strip()
            if not host or host.startswith("127."):
                if sender and not sender.startswith("127."):
                    host = sender
                else:
                    continue
            try:
                port = int(msg.get("port"))
            except (TypeError, ValueError):
                continue
            name = str(msg.get("name") or host).strip() or host
            track = msg.get("track")
            track_s = track if isinstance(track, str) and track.strip() else None
            key = f"{host}:{port}"
            dev = LanDevice(
                host=host,
                port=port,
                name=name,
                last_seen=time.monotonic(),
                iracing=bool(msg.get("iracing")),
                track=track_s,
            )
            prev = self._devices.get(key)
            if prev is None or prev.iracing != dev.iracing or prev.track != dev.track or prev.name != dev.name:
                changed = True
            self._devices[key] = dev
        if changed:
            self.devices_changed.emit()

    def _expire_stale(self) -> None:
        now = time.monotonic()
        dead = [k for k, d in self._devices.items() if (now - d.last_seen) > DEVICE_STALE_SEC]
        if not dead:
            return
        for k in dead:
            self._devices.pop(k, None)
        self.devices_changed.emit()
