@echo off
setlocal EnableExtensions

REM Builds a Windows executable using PyInstaller.
REM Run this on Windows from the project folder.

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

if not exist icon.png (
  echo ERROR: icon.png not found in project folder.
  goto :fail
)

echo Generating icon.ico...
"%PY%" make_icon_ico.py
if errorlevel 1 goto :fail

if not exist icon.ico (
  echo ERROR: icon.ico was not created.
  goto :fail
)

echo Building executable...
"%PY%" -m PyInstaller --noconfirm --clean ai_race_engineer.spec
if errorlevel 1 goto :fail

echo.
echo Build complete:
echo   dist\AI Race Engineer\AI Race Engineer.exe
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
