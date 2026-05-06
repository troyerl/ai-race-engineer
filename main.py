import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from ui import AIRaceEngineer


def main():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon.png"))
    window = AIRaceEngineer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()