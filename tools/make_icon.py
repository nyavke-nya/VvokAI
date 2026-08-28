"""Draw the VvokAI mark as an icon file.

    venv\\Scripts\\python.exe tools\\make_icon.py

The mark in the corner of the panel has never been a picture - it is a styled
letter, .brand-mark in static/css/vvok.css, drawn by the browser. That is fine
inside the panel and no use at all to Windows, which wants an .ico for the
executable, the taskbar and the window corner. images/logo.png is the upstream
PylaAI "P", which is somebody else's mark.

So this redraws the same thing Pillow can hand to Windows, from the same
numbers the stylesheet uses: the gradient, the corner radius and the colours
are copied from there, so changing one and not the other shows up as the icon
drifting away from the interface rather than as two designs nobody notices.
"""

import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Straight from .brand-mark. Sizes are proportions of the tile rather than the
# stylesheet's pixels, because this is drawn at 1024 and scaled down.
RADIUS = 11 / 40          # border-radius 11px on a 40px tile
LETTER = "V"
INK = (11, 11, 13)        # color: #0b0b0d

# background: linear-gradient(145deg, ...) - stop position, colour
GRADIENT = [
    (0.00, (255, 255, 255)),
    (0.38, (220, 220, 220)),
    (0.68, (166, 166, 166)),
    (1.00, (110, 110, 110)),
]

# The .ico carries every size Windows picks from: the small one for the
# taskbar and Explorer's list view, 256 for the large icons and the Alt-Tab
# switcher. Leaving sizes out makes Windows scale one down badly.
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# Bahnschrift ships with Windows and is what the stylesheet falls back to when
# Space Grotesk cannot be fetched, so it is the closest thing on the machine.
FONTS = ["bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf", "calibrib.ttf"]

SIZE = 1024


def gradient_at(position):
    """Colour at 0..1 along the gradient, interpolating between the stops."""
    for (left, low), (right, high) in zip(GRADIENT, GRADIENT[1:]):
        if position <= right:
            span = right - left
            share = 0.0 if span == 0 else (position - left) / span
            return tuple(round(a + (b - a) * share) for a, b in zip(low, high))
    return GRADIENT[-1][1]


def draw_gradient(size):
    """The 145deg diagonal, top-left bright to bottom-right dark.

    CSS measures the angle clockwise from "up", so 145deg points down and to
    the right; projecting each pixel onto that axis gives the same sweep.
    """
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            # Normalised projection onto the diagonal.
            position = (x + y) / (2 * (size - 1))
            pixels[x, y] = gradient_at(position)
    return image


def pick_font(size):
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build(size=SIZE):
    tile = draw_gradient(size).convert("RGBA")

    # Everything outside the rounded square is transparent, so the icon has
    # the tile's shape rather than sitting in a square block of colour.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * RADIUS), fill=255)
    tile.putalpha(mask)

    canvas = ImageDraw.Draw(tile)

    # inset 0 1.5px 0 rgba(255,255,255,1) - the lit top edge that makes it
    # read as a physical key rather than a flat square.
    edge = max(2, size // 90)
    canvas.rounded_rectangle((0, 0, size - 1, size - 1),
                             radius=int(size * RADIUS),
                             outline=(255, 255, 255, 210), width=edge)

    font = pick_font(int(size * 0.56))
    left, top, right, bottom = canvas.textbbox((0, 0), LETTER, font=font)
    canvas.text(((size - (right - left)) / 2 - left,
                 (size - (bottom - top)) / 2 - top),
                LETTER, font=font, fill=(*INK, 255))
    return tile


def main():
    mark = build()
    images = ROOT / "assets" / "images"
    images.mkdir(parents=True, exist_ok=True)

    png = images / "vvokai.png"
    ico = images / "vvokai.ico"
    mark.save(png)
    mark.save(ico, sizes=ICON_SIZES)
    print(f"wrote {png.relative_to(ROOT)} and {ico.relative_to(ROOT)}")
    print("Look at them before building; the letter should sit dead centre.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
