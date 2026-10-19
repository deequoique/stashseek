"""Generate the packaged extension icons from the brand artwork.

Design: a dark (#171717) rounded tile with the logo centered. The 128 px icon
follows the Chrome Web Store guideline (96 px tile inside 16 px of transparent
margin); the smaller toolbar sizes use a tighter margin so the mark stays legible.

Pillow is not a project dependency. Run from the repository root:

    uv run --quiet --no-project --with pillow python extension/scripts/generate-icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "assets" / "stashseek-chat-logo.png"
OUT = ROOT / "public" / "icons"
BACKGROUND = (23, 23, 23, 255)
SUPERSAMPLE = 4

# size -> (tile edge, corner radius, logo padding as a share of the tile)
SPECS = {
    128: (96, 22, 0.18),
    48: (44, 10, 0.14),
    32: (30, 7, 0.12),
    16: (16, 4, 0.10),
}


def trimmed_artwork() -> Image.Image:
    source = Image.open(SOURCE).convert("RGBA")
    box = source.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    return source.crop(box)


def build(art: Image.Image, size: int, tile: int, radius: int, padding: float) -> Image.Image:
    scale = SUPERSAMPLE
    canvas_px, tile_px = size * scale, tile * scale
    tile_image = Image.new("RGBA", (tile_px, tile_px), (0, 0, 0, 0))
    ImageDraw.Draw(tile_image).rounded_rectangle(
        [0, 0, tile_px - 1, tile_px - 1], radius=radius * scale, fill=BACKGROUND
    )
    inner = int(tile_px * (1 - 2 * padding))
    ratio = inner / max(art.size)
    logo = art.resize((max(1, round(art.width * ratio)), max(1, round(art.height * ratio))), Image.LANCZOS)
    tile_image.alpha_composite(logo, ((tile_px - logo.width) // 2, (tile_px - logo.height) // 2))
    canvas = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    offset = (canvas_px - tile_px) // 2
    canvas.alpha_composite(tile_image, (offset, offset))
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    art = trimmed_artwork()
    for size, (tile, radius, padding) in sorted(SPECS.items()):
        build(art, size, tile, radius, padding).save(OUT / f"icon-{size}.png", optimize=True)
        print("wrote", OUT / f"icon-{size}.png")


if __name__ == "__main__":
    main()
