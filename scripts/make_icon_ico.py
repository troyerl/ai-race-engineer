"""
Generate a Windows .ico from assets/icon.png with multiple embedded sizes.

Windows Explorer is picky: a single-size ICO can show as a generic icon.
This script generates a multi-size ICO that PyInstaller can embed reliably.
"""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    from PIL import Image

    root = Path(__file__).resolve().parent.parent
    src = root / "assets" / "icon.png"
    dst = root / "assets" / "icon.ico"

    img = Image.open(src).convert("RGBA")
    # Common Windows icon sizes; embedding multiple sizes improves compatibility.
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(dst, format="ICO", sizes=sizes)
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
