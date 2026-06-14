# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AI Race Engineer (Windows folder build)."""

import sys

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# icon.png must be bundled for Qt window icon at runtime.
datas = [("icon.png", ".")]
binaries = []
hiddenimports = [
    "irsdk",
    "app_config",
    "pre_race_strategy",
    "strategy_engine",
    "strategy_worker",
    "broadcaster_ui",
    "hotkey",
    "lan_discovery",
    "race_link",
    "race_memory",
    "receiver_theme",
    "remote_telemetry",
    "role_picker",
    "speech",
    "telemetry",
    "ui",
]

for pkg in ("PySide6",):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI Race Engineer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico" if sys.platform.startswith("win") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AI Race Engineer",
)
