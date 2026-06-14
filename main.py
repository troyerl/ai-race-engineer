import argparse
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from broadcaster_ui import BroadcasterWindow
from race_link import DEFAULT_RACE_LINK_PORT
from role_picker import pick_startup_role
from ui import AIRaceEngineer


def _resource_path(relative_path: str) -> str:
    """
    Return an absolute path to a bundled resource.

    Works for:
    - running from source (relative to this file)
    - PyInstaller onefile/onedir (relative to sys._MEIPASS)
    """
    import os

    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def _set_windows_app_user_model_id(app_id: str) -> None:
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def main():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="AI Race Engineer overlay")
    parser.add_argument(
        "--role",
        choices=["local", "broadcaster", "receiver"],
        default=None,
        help=(
            "Skip the startup picker: local = iRacing + AI on same PC; "
            "broadcaster = sim PC streams telemetry and speaks calls; "
            "receiver = engineer PC runs AI and shows advice"
        ),
    )
    parser.add_argument("--bind", default="0.0.0.0", help="Broadcaster listen address (sim PC)")
    parser.add_argument("--connect", default=None, help="Broadcaster LAN IP (receiver / AI PC)")
    parser.add_argument("--port", type=int, default=None, help=f"TCP port (default {DEFAULT_RACE_LINK_PORT})")
    args = parser.parse_args()
    port = int(args.port) if args.port is not None else DEFAULT_RACE_LINK_PORT

    if sys.platform.startswith("win"):
        _set_windows_app_user_model_id("ai-race-engineer.overlay")

    app = QApplication(sys.argv)
    _f = app.font()
    if _f.pointSizeF() <= 0:
        _f.setPointSizeF(10.0)
    app.setFont(_f)
    icon_path = _resource_path("icon.png")
    icon = QIcon(icon_path)
    app.setWindowIcon(icon)

    role = args.role
    if role is None:
        role = pick_startup_role()
        if role is None:
            sys.exit(0)

    if role == "broadcaster":
        window = BroadcasterWindow(bind_host=args.bind, port=port)
    elif role == "receiver":
        window = AIRaceEngineer(role="receiver", link_host=args.connect, link_port=port)
    else:
        window = AIRaceEngineer(role="local")

    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
