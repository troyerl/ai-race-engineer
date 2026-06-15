#!/usr/bin/env bash
# Builds a macOS .app bundle using PyInstaller.
# Run from the project folder (or via ./build_mac.sh at repo root).

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: Mac builds must run on macOS."
  exit 1
fi

echo "Installing app dependencies..."
python3 -m pip install -U pip
python3 -m pip install -r requirements.txt

echo "Installing build tools..."
python3 -m pip install -r requirements-build.txt

if [[ ! -f assets/icon.png ]]; then
  echo "ERROR: assets/icon.png not found in project folder."
  exit 1
fi

echo "Generating assets/icon.icns..."
python3 scripts/make_icon_icns.py

if [[ ! -f assets/icon.icns ]]; then
  echo "ERROR: assets/icon.icns was not created."
  exit 1
fi

echo "Building application bundle..."
python3 -m PyInstaller --noconfirm --clean ai_race_engineer.spec

echo
echo "Build complete:"
echo "  dist/AI Race Engineer.app"
echo
echo "One-click launch:"
echo "  open \"dist/AI Race Engineer.app\""
echo "  Or drag the .app to Applications / the Dock."
echo
