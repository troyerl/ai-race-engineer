@echo off
setlocal

REM Builds a Windows executable using PyInstaller.
REM Run this on Windows from the project folder.

py -m pip install -U pip >nul
py -m pip install -U pyinstaller >nul

REM Folder build is usually most reliable for PySide6 apps.
py -m PyInstaller --noconfirm --windowed --name "AI Race Engineer" main.py

echo.
echo Build complete:
echo   dist\AI Race Engineer\AI Race Engineer.exe
echo.
endlocal
