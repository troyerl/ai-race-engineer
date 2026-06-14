"""
TCP newline-JSON stream between iRacing (broadcaster / sim PC) and engineer overlay (receiver).

Sim PC: streams telemetry, speaks final engineer calls (no advice UI).
Engineer PC: connects, displays AI advice, sends final calls back for voice.

Protocol v1 (one JSON object per line):
  Server → client:
    {"t":"welcome","v":1}
    {"t":"snap","mode":"live|strategy","iracing":true,"track":"...","track_mi":2.5,"packet":{...}}
  Client → server:
    {"t":"advice","text":"...","partial":false}
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

PROTOCOL_VERSION = 1
DEFAULT_RACE_LINK_PORT = 8765
SNAP_INTERVAL_MS = 250


def _encode_line(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class _AdviceBridge(QObject):
    received = Signal(str, bool)


class _ClientPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._socks: list[socket.socket] = []

    def add(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.append(sock)

    def remove(self, sock: socket.socket) -> None:
        with self._lock:
            if sock in self._socks:
                self._socks.remove(sock)
        try:
            sock.close()
        except Exception:
            pass

    def count(self) -> int:
        with self._lock:
            return len(self._socks)

    def broadcast(self, payload: bytes) -> None:
        dead: list[socket.socket] = []
        with self._lock:
            socks = list(self._socks)
        for s in socks:
            try:
                s.sendall(payload)
            except Exception:
                dead.append(s)
        for s in dead:
            self.remove(s)


class BroadcasterService(QObject):
    """Runs on the iRacing machine; pushes telemetry snapshots to LAN clients."""

    clients_changed = Signal(int)
    iracing_changed = Signal(bool)
    advice_received = Signal(str, bool, bool, bool)  # text, partial, speak, include_why

    def __init__(
        self,
        packet_builder: Callable[[], dict | None],
        bind_host: str = "0.0.0.0",
        port: int = DEFAULT_RACE_LINK_PORT,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._packet_builder = packet_builder
        self._bind_host = bind_host
        self._port = int(port)
        self._pool = _ClientPool()
        self._stop = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._server_sock: socket.socket | None = None
        self._last_iracing = False
        self._advice_bridge = _AdviceBridge()
        self._advice_bridge.received.connect(self.advice_received.emit)

        self._tick = QTimer(self)
        self._tick.setInterval(SNAP_INTERVAL_MS)
        self._tick.timeout.connect(self._on_tick)

    def start(self) -> None:
        if self._accept_thread is not None:
            return
        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        self._tick.start()

    def stop(self) -> None:
        self._tick.stop()
        self._stop.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.5)
            self._accept_thread = None

    def client_count(self) -> int:
        return self._pool.count()

    def _accept_loop(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock = srv
        try:
            srv.bind((self._bind_host, self._port))
            srv.listen(8)
            srv.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    client, _addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    client.sendall(_encode_line({"t": "welcome", "v": PROTOCOL_VERSION}))
                except Exception:
                    client.close()
                    continue
                self._pool.add(client)
                self.clients_changed.emit(self._pool.count())
                threading.Thread(target=self._client_read_loop, args=(client,), daemon=True).start()
        finally:
            try:
                srv.close()
            except Exception:
                pass

    def _client_read_loop(self, sock: socket.socket) -> None:
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    line = buf[:nl]
                    buf = buf[nl + 1 :]
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    if isinstance(msg, dict) and msg.get("t") == "advice":
                        text = str(msg.get("text") or "")
                        partial = bool(msg.get("partial"))
                        speak = bool(msg.get("speak", True))
                        include_why = bool(msg.get("why", True))
                        self._advice_bridge.received.emit(text, partial, speak, include_why)
        finally:
            self._pool.remove(sock)
            self.clients_changed.emit(self._pool.count())

    def _on_tick(self) -> None:
        built = self._packet_builder()
        if built is None:
            if self._last_iracing:
                self._last_iracing = False
                self.iracing_changed.emit(False)
            return

        iracing = bool(built.get("iracing"))
        if iracing != self._last_iracing:
            self._last_iracing = iracing
            self.iracing_changed.emit(iracing)

        msg = {
            "t": "snap",
            "mode": built.get("mode", "live"),
            "iracing": iracing,
            "track": built.get("track"),
            "track_mi": built.get("track_mi"),
            "packet": built.get("packet"),
        }
        self._pool.broadcast(_encode_line(msg))


class ReceiverClient(QObject):
    """Connects to a broadcaster and emits parsed snap messages."""

    snapshot = Signal(dict)
    link_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._sock = QTcpSocket(self)
        self._buf = bytearray()
        self._host = ""
        self._port = DEFAULT_RACE_LINK_PORT

        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.errorOccurred.connect(self._on_error)

        self._reconnect = QTimer(self)
        self._reconnect.setInterval(3000)
        self._reconnect.timeout.connect(self._try_connect)
        self._want_link = False

    def connect_to(self, host: str, port: int = DEFAULT_RACE_LINK_PORT) -> None:
        self._host = (host or "").strip()
        self._port = int(port)
        self._want_link = bool(self._host)
        self._reconnect.stop()
        if self._sock.state() != QAbstractSocket.UnconnectedState:
            self._sock.abort()
        if self._want_link:
            self._try_connect()
            self._reconnect.start()

    def disconnect_link(self) -> None:
        self._want_link = False
        self._reconnect.stop()
        self._sock.abort()
        self.link_changed.emit(False)

    def is_linked(self) -> bool:
        return self._sock.state() == QAbstractSocket.ConnectedState

    def send_advice(
        self,
        text: str,
        *,
        partial: bool = False,
        speak: bool = True,
        include_why: bool = True,
    ) -> bool:
        """Send finished advice to the sim PC. Returns False if not linked or socket write fails."""
        if not self.is_linked():
            return False
        msg = _encode_line(
            {
                "t": "advice",
                "text": str(text or ""),
                "partial": bool(partial),
                "speak": bool(speak),
                "why": bool(include_why),
            }
        )
        try:
            pending = self._sock.bytesToWrite()
            if pending > 65536:
                print(f"[WARN] race_link advice queue large ({pending} bytes); dropping partial={partial}")
            n = self._sock.write(msg)
            if n < 0:
                return False
            self._sock.flush()
            if not self._sock.waitForBytesWritten(3000):
                print("[WARN] race_link advice write timed out")
                return False
            return True
        except Exception as exc:
            print(f"[WARN] race_link send_advice failed: {exc}")
            return False

    def _try_connect(self) -> None:
        if not self._want_link or not self._host:
            return
        if self._sock.state() == QAbstractSocket.ConnectedState:
            return
        if self._sock.state() == QAbstractSocket.ConnectingState:
            return
        self._sock.connectToHost(self._host, self._port)

    def _on_connected(self) -> None:
        self._buf.clear()
        self.link_changed.emit(True)

    def _on_disconnected(self) -> None:
        self._buf.clear()
        self.link_changed.emit(False)

    def _on_error(self, err: QAbstractSocket.SocketError) -> None:
        if err == QAbstractSocket.SocketError.ConnectionRefusedError:
            self.error.emit(f"Connection refused ({self._host}:{self._port})")
        elif err != QAbstractSocket.SocketError.RemoteHostClosedError:
            self.error.emit(self._sock.errorString())

    def _on_ready_read(self) -> None:
        self._buf.extend(self._sock.readAll().data())
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("t") == "snap":
                self.snapshot.emit(msg)
