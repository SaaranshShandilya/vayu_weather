"""Normalise the partner logos in logos/ into web assets in public/logos/.

Source logos arrive with wildly different canvas padding and resolution: one
is a 3833px wordmark, another a 187px emblem, another a JPEG with a wide white
margin baked in. Rendering those directly makes even sizing impossible, since
a logo's apparent size depends on its artwork, not its canvas.

This trims each canvas to the artwork, downscales to roughly three times the
size it is displayed at, and writes a palette PNG. Re-run it after adding or
replacing a file in logos/, then re-solve the heights in src/LogoStrip.tsx.

    python scripts/prepare_logos.py
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PROJECT_ROOT / "logos"
TARGET_DIR = PROJECT_ROOT / "public" / "logos"

# Displayed at up to ~77 CSS px tall; 3x covers high-density screens. A
# source smaller than this is left at its native size rather than upscaled.
MAX_HEIGHT = 240
# A pixel this close to white is treated as canvas, not artwork.
WHITE = 238
ALPHA = 20

TARGET_NAMES = {
    "Blue_Skies_Logo.jpg": "blue-skies.png",
    "CCAC-Logo.png": "ccac.png",
    "CEEW_Logo.png": "ceew.png",
    "Coefficient_Giving_Logo.png": "coefficient-giving.png",
    "GMH Logo.png": "global-methane-hub.png",
    "IITGn_Logo.png": "iit-gandhinagar.png",
}


def artwork_box(image: Image.Image) -> tuple[int, int, int, int]:
    """The bounding box of everything that is neither white nor transparent."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda value: 255 if value > ALPHA else 0)
    grey = rgba.convert("L").point(lambda value: 0 if value >= WHITE else 255)
    ink = Image.new("L", rgba.size, 0)
    ink.paste(grey, mask=alpha)
    return ink.getbbox() or (0, 0, *rgba.size)


def prepare(source: Path, target: Path) -> tuple[int, int]:
    image = Image.open(source)
    trimmed = image.convert("RGBA").crop(artwork_box(image))
    width, height = trimmed.size
    if height > MAX_HEIGHT:
        width = round(width * MAX_HEIGHT / height)
        trimmed = trimmed.resize((width, MAX_HEIGHT), Image.LANCZOS)
        height = MAX_HEIGHT
    target.parent.mkdir(parents=True, exist_ok=True)
    # Logos are flat art, so a 256-colour palette is visually identical and
    # roughly a quarter of the bytes. Keep whichever encoding is smaller.
    full = io.BytesIO()
    trimmed.save(full, "PNG", optimize=True)
    palette = io.BytesIO()
    trimmed.quantize(colors=256, method=Image.FASTOCTREE).save(
        palette, "PNG", optimize=True
    )
    target.write_bytes(
        (palette if palette.tell() < full.tell() else full).getvalue()
    )
    return trimmed.size


def main() -> int:
    missing = [name for name in TARGET_NAMES if not (SOURCE_DIR / name).exists()]
    unlisted = [
        item.name
        for item in sorted(SOURCE_DIR.iterdir())
        if item.is_file() and item.name not in TARGET_NAMES
    ]
    if missing:
        print("Missing from logos/: " + ", ".join(missing))
    if unlisted:
        print("Not listed in TARGET_NAMES: " + ", ".join(unlisted))

    print(f"{'source':<30} {'output':<26} {'size':>11} {'aspect':>7} {'kB':>6}")
    for name, target_name in TARGET_NAMES.items():
        source = SOURCE_DIR / name
        if not source.exists():
            continue
        target = TARGET_DIR / target_name
        width, height = prepare(source, target)
        size_kb = target.stat().st_size / 1024
        print(
            f"{name:<30} {target_name:<26} {width:>5}x{height:<5} "
            f"{width / height:>7.2f} {size_kb:>6.1f}"
        )
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
