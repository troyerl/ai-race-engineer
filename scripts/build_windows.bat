@echo off
setlocal EnableExtensions

REM Builds a Windows executable using PyInstaller.
REM Run this on Windows from the project folder (or via scripts\build_windows.bat).

cd /d "%~dp0\.."

set "PY=py"
where py >nul 2>&1 || set "PY=python"

echo Installing app dependencies...
"%PY%" -m pip install -U pip
if errorlevel 1 goto :fail

"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo Installing build tools...
"%PY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :fail

if not exist assets\icon.png (
  echo ERROR: assets\icon.png not found in project folder.
  goto :fail
)

echo Generating assets\icon.ico...
"%PY%" scripts\make_icon_ico.py
if errorlevel 1 goto :fail

if not exist assets\icon.ico (
  echo ERROR: assets\icon.ico was not created.
  goto :fail
)

echo Building executable...
"%PY%" -m PyInstaller --noconfirm --clean ai_race_engineer.spec
if errorlevel 1 goto :fail

echo.
echo Build complete:
echo   dist\AI Race Engineer\AI Race Engineer.exe
echo.
echo One-click launch:
echo   Double-click the .exe above, or pin it to the taskbar.
echo   Optional Desktop shortcut:
echo     powershell -ExecutionPolicy Bypass -File scripts\create_windows_shortcut.ps1
echo.
endlocal
exit /b 0

:fail
echo.
echo BUILD FAILED. See errors above.
echo Common fixes:
echo   - pip install -r requirements.txt
echo   - Use pyirsdk not irsdk: pip install pyirsdk
echo   - Run this script on Windows from the project folder
echo.
endlocal
exit /b 1
