import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

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
    # Helps Windows taskbar grouping/icon for packaged apps.
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

    if sys.platform.startswith("win"):
        _set_windows_app_user_model_id("ai-race-engineer.overlay")

    app = QApplication(sys.argv)
    icon_path = _resource_path("icon.png")
    icon = QIcon(icon_path)
    app.setWindowIcon(icon)
    window = AIRaceEngineer()
    window.setWindowIcon(icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()