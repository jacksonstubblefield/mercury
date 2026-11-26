"""Draw Mercury's app icon as 32x32 pixel art and export it at several sizes.

The planetary Mercury symbol (☿) in quicksilver, wrapped in a dark outline and
a coloured glow so it separates from whatever is behind it. It's drawn on a
32x32 grid and upscaled with nearest-neighbour, so the pixels stay crisp.

    backend/.venv/Scripts/python scripts/make_icon.py

Writes assets/icon/icon.png + icon.ico (Electron), frontend/public/favicon-*.png
and assets/icon/preview.png (the symbol on every background).
"""

import math
from pathlib import Path

from PIL import Image

N = 32
Cell = tuple[int, int]

SYMBOL = {
    "sym": (214, 222, 236, 255),
    "sym_hi": (255, 255, 255, 255),
    "sym_lo": (140, 150, 176, 255),
    "outline": (12, 12, 28, 255),
}

GLOWS = {
    "red": [(238, 52, 60, 255), (238, 52, 60, 90)],
}

# The shipped icon. The other backgrounds are only rendered into preview.png.
FINAL = ("none", "red")


def cells(pred) -> set[Cell]:
    return {(x, y) for x in range(N) for y in range(N) if pred(x + 0.5, y + 0.5)}


def grow(region: set[Cell], diagonal: bool = True) -> set[Cell]:
    """Cells touching region from outside (8- or 4-neighbourhood)."""
    steps = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx or dy) and (diagonal or not (dx and dy))]
    return {(x + dx, y + dy) for (x, y) in region for dx, dy in steps} - region


def flood_outside(walls: set[Cell]) -> set[Cell]:
    """Cells reachable from the image border without crossing walls."""
    seen = {(x, y) for x in range(N) for y in (0, N - 1)} | {(x, y) for x in (0, N - 1) for y in range(N)}
    seen -= walls
    stack = list(seen)
    while stack:
        x, y = stack.pop()
        for c in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= c[0] < N and 0 <= c[1] < N and c not in seen and c not in walls:
                seen.add(c)
                stack.append(c)
    return seen


# Hand-placed and deliberately a little off: the right horn reaches higher
# than the left, the circle leans left, and the cross sits right of centre with
# uneven arms.
SYMBOL_ART = [
    "............##",
    ".##.........##",
    ".##.........##",
    ".###.......###",
    "..###.....###.",
    "...#########..",
    "....######....",
    "..###....###..",
    ".##........##.",
    "##.........##.",
    "##.........##.",
    "##.........##.",
    ".##.......##..",
    ".###.....###..",
    "...#######....",
    ".......##.....",
    ".......##.....",
    ".......##.....",
    "....#########.",
    ".....#######..",
    ".......##.....",
    ".......##.....",
]
SYMBOL_ORIGIN = (9, 5)


def symbol() -> set[Cell]:
    ox, oy = SYMBOL_ORIGIN
    return {(ox + x, oy + y) for y, row in enumerate(SYMBOL_ART) for x, ch in enumerate(row) if ch == "#"}


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))


# --- backgrounds -----------------------------------------------------------


def bg_none(px):
    pass


def bg_planet(px):
    """A small grey, cratered Mercury lit from the upper left."""
    disc = cells(lambda x, y: math.hypot(x - 16, y - 16) <= 15.2)
    craters = [(9, 9, 2.2), (22, 21, 2.8), (10, 23, 1.6), (24, 10, 1.4), (17, 27, 1.3), (6, 16, 1.2)]
    for (x, y) in disc:
        cx, cy = x + 0.5, y + 0.5
        light = 1 - (math.hypot(cx - 10, cy - 10) / 24)
        c = lerp((52, 50, 58, 255), (150, 144, 140, 255), max(0.0, min(1.0, light)))
        for kx, ky, kr in craters:
            dk = math.hypot(cx - kx, cy - ky)
            if dk <= kr:
                c = lerp(c, (30, 28, 36, 255), 0.35 if cx + cy > kx + ky else 0.18)
        px[x, y] = c
    for (x, y) in grow(disc, diagonal=False):
        if 0 <= x < N and 0 <= y < N:
            px[x, y] = (12, 12, 20, 255)


def bg_tile(px):
    """Rounded-square app tile, dark night-sky gradient."""
    r, lo, hi = 6, 1, 30

    def inside(x, y):
        qx = min(max(x, lo + r), hi - r)
        qy = min(max(y, lo + r), hi - r)
        return math.hypot(x - qx, y - qy) <= r

    tile = cells(inside)
    for (x, y) in tile:
        px[x, y] = lerp((40, 44, 92, 255), (14, 14, 36, 255), y / N)
    for (x, y) in tile & grow(set(cells(lambda *_: True)) - tile, diagonal=False):
        px[x, y] = (8, 8, 22, 255)


def bg_orb(px):
    """Dark round orb with a thin bright rim."""
    disc = cells(lambda x, y: math.hypot(x - 16, y - 16) <= 15.2)
    rim = disc & grow(set(cells(lambda *_: True)) - disc, diagonal=False)
    for (x, y) in disc:
        px[x, y] = lerp((44, 40, 96, 255), (12, 12, 32, 255), (x + y) / (2 * N))
    for (x, y) in rim:
        px[x, y] = (90, 96, 170, 255) if x + y < 32 else (30, 30, 70, 255)


BACKGROUNDS = {"none": bg_none, "planet": bg_planet, "tile": bg_tile, "orb": bg_orb}


def draw(background: str, glow: str) -> Image.Image:
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    px = img.load()
    BACKGROUNDS[background](px)

    sym = symbol()
    outline = grow(sym, diagonal=False)
    # The glow only spreads through space reachable from the image border, so
    # the hole inside the circle stays clean.
    outside = flood_outside(sym | outline)
    glow_rings = []
    edge = sym | outline
    for _ in GLOWS[glow]:
        ring = grow(edge, diagonal=False) & outside
        glow_rings.append(ring)
        edge |= ring

    # Glow, outermost first, blended over the background.
    for ring, colour in reversed(list(zip(glow_rings, GLOWS[glow]))):
        for c in ring:
            if 0 <= c[0] < N and 0 <= c[1] < N:
                base = px[c]
                a = colour[3] / 255
                if base[3] == 0:
                    px[c] = colour
                else:
                    px[c] = tuple(round(base[i] * (1 - a) + colour[i] * a) for i in range(3)) + (255,)
    for c in outline:
        px[c] = SYMBOL["outline"]
    for (x, y) in sym:
        col = SYMBOL["sym"]
        if (x - 1, y) not in sym or (x, y - 1) not in sym:
            col = SYMBOL["sym_hi"]
        if ((x + 1, y) not in sym and (x, y + 1) not in sym) or ((x, y + 1) not in sym and (x - 1, y) in sym):
            col = SYMBOL["sym_lo"]
        px[x, y] = col
    return img


def scaled(img: Image.Image, size: int) -> Image.Image:
    """Whole multiples of 32 stay crisp; anything else is resampled smoothly."""
    if size % N == 0:
        return img.resize((size, size), Image.NEAREST)
    return img.resize((512, 512), Image.NEAREST).resize((size, size), Image.LANCZOS)


def export_final(root: Path) -> None:
    img = draw(*FINAL)
    out = root / "assets" / "icon"
    scaled(img, 512).save(out / "icon.png")
    # Windows icon with a hand-picked frame per size (window, taskbar, Explorer).
    frames = [scaled(img, s) for s in (256, 128, 64, 48, 32, 24, 16)]
    frames[0].save(out / "icon.ico", sizes=[f.size for f in frames], append_images=frames[1:])
    public = root / "frontend" / "public"
    public.mkdir(exist_ok=True)
    scaled(img, 32).save(public / "favicon-32.png")
    scaled(img, 64).save(public / "favicon-64.png")
    scaled(img, 128).save(public / "favicon-128.png")  # sidebar brand icon


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    out = root / "assets" / "icon"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("icon-*"):
        old.unlink()
    export_final(root)

    rows = list(GLOWS)
    cols = list(BACKGROUNDS)
    cell = 256 + 40
    sheet = Image.new("RGBA", (len(cols) * cell + 40, len(rows) * (cell + 48) + 40), (44, 44, 52, 255))
    for r, glow in enumerate(rows):
        for c, bg in enumerate(cols):
            img = draw(bg, glow)
            ox, oy = 40 + c * cell, 40 + r * (cell + 48)
            sheet.alpha_composite(img.resize((256, 256), Image.NEAREST), (ox, oy))
            # Actual-size and 2x previews, as it would look in a taskbar or tab.
            sheet.alpha_composite(img, (ox, oy + 262))
            sheet.alpha_composite(img.resize((64, 64), Image.NEAREST).crop((0, 0, 64, 40)), (ox + 44, oy + 262))
    sheet.save(out / "preview.png")
    print(f"wrote {out.resolve()}")


if __name__ == "__main__":
    main()
