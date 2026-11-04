"""Draws assets/icon.png (1024px): the page's sage green and zigzag lines, a download arrow.

Run: uv run --with pillow assets/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 2                      # draw at 2x, then downscale for clean edges
SIZE, INSET, RADIUS = 1024 * S, 100 * S, 185 * S
GREEN, LINES, INK = (159, 205, 153), (141, 191, 135), (27, 33, 28)


def squircle_mask() -> Image.Image:
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((INSET, INSET, SIZE - INSET, SIZE - INSET), RADIUS, fill=255)
    return mask


def main() -> None:
    art = Image.new("RGBA", (SIZE, SIZE), GREEN + (255,))
    d = ImageDraw.Draw(art)

    # The zigzag rows, as on the page: points facing down.
    w, h, amp = 96 * S, 64 * S, 24 * S
    for y in range(-h, SIZE + h, h):
        pts = [(x, y + (amp if (x // w) % 2 else 0)) for x in range(-w, SIZE + 2 * w, w)]
        d.line(pts, fill=LINES, width=7 * S, joint="curve")

    # A rounded download arrow dropping onto a line.
    stroke, cx = 64 * S, SIZE // 2
    top, tip, half = 300 * S, 640 * S, 150 * S
    d.line([(cx, top), (cx, tip)], fill=INK, width=stroke)
    d.line([(cx - half, tip - half), (cx, tip), (cx + half, tip - half)], fill=INK, width=stroke, joint="curve")
    d.line([(cx - 200 * S, 760 * S), (cx + 200 * S, 760 * S)], fill=INK, width=stroke)
    for x, y in [(cx, top), (cx - half, tip - half), (cx + half, tip - half), (cx, tip),
                 (cx - 200 * S, 760 * S), (cx + 200 * S, 760 * S)]:
        r = stroke // 2
        d.ellipse((x - r, y - r, x + r, y + r), fill=INK)   # round caps

    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(art, (0, 0), squircle_mask())
    out = Path(__file__).parent / "icon.png"
    icon.resize((1024, 1024), Image.LANCZOS).save(out)
    print(out)


if __name__ == "__main__":
    main()
