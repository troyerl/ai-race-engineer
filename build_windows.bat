@echo off
setlocal

REM Builds a Windows executable using PyInstaller.
REM Run this on Windows from the project folder.

py -m pip install -U pip >nul
py -m pip install -U pyinstaller >nul

REM Folder build is usually most reliable for PySide6 apps.
REM --icon expects a proper multi-size .ico on Windows.
py -m pip install -U pillow >nul
py make_icon_ico.py

REM Ensure icon.png is available at runtime inside the bundle.
py -m PyInstaller --noconfirm --windowed --name "AI Race Engineer" --icon icon.ico --add-data "icon.png;." main.py

echo.
echo Build complete:
echo   dist\AI Race Engineer\AI Race Engineer.exe
echo.
endlocal
