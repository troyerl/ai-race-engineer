"""
Generate assets/icon.icns from assets/icon.png (macOS, uses iconutil).

Run on a Mac before PyInstaller packaging so the .app bundle gets a proper icon.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if sys.platform != "darwin":
        print("make_icon_icns.py must run on macOS (iconutil is required).")
        sys.exit(1)

    from PIL import Image

    root = Path(__file__).resolve().parent.parent
    src = root / "assets" / "icon.png"
    dst = root / "assets" / "icon.icns"
    iconset = root / "assets" / "icon.iconset"

    img = Image.open(src).convert("RGBA")

    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()

    # Standard macOS iconset sizes (1x and @2x where applicable).
    entries = [
        (16, "icon_16x16.png"),
        (32, "icon_16x32.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x64.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x256.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x512.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x1024.png"),
    ]
    for size, name in entries:
        resized = img.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(iconset / name)

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(dst)],
        check=True,
    )
    shutil.rmtree(iconset)
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
