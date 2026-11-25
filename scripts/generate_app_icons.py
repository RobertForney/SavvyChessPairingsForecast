"""Generate deterministic app icons without fonts or third-party packages.

The gold rook uses the existing evergreen/gold UI palette. Its full silhouette
fits inside the maskable icon's central 80% safe circle. Run from any directory:

    python scripts/generate_app_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path
import struct
import zlib


OUTPUT = Path(__file__).resolve().parents[1] / "web" / "icons"
BACKGROUND = (23, 62, 53)
GOLD = (221, 191, 127)
# Coordinates share the SVG's 1000 by 1000 view box. Keep all detail within
# radius 400 from (500, 500) for launchers that crop maskable icons to a circle.
ROOK = [
    (300, 240), (390, 240), (390, 320), (455, 320), (455, 240),
    (545, 240), (545, 320), (610, 320), (610, 240), (700, 240),
    (700, 400), (630, 460), (600, 650), (655, 690), (655, 735),
    (345, 735), (345, 690), (400, 650), (370, 460), (300, 400),
]
BASE = [(300, 755), (700, 755), (700, 800), (300, 800)]
COLLAR = [(377, 455), (623, 455), (617, 490), (383, 490)]


def fill_polygon(pixels: bytearray, width: int, points: list[tuple[int, int]],
                 color: tuple[int, int, int]) -> None:
    """Fill a polygon using pixel-center intersections and even/odd scanlines."""
    row_color = bytes(color)
    for y in range(max(0, min(p[1] for p in points)),
                   min(width, max(p[1] for p in points))):
        center_y = y + 0.5
        intersections = []
        for start, end in zip(points, points[1:] + points[:1]):
            x1, y1 = start
            x2, y2 = end
            if (y1 <= center_y < y2) or (y2 <= center_y < y1):
                intersections.append(x1 + (center_y - y1) * (x2 - x1) / (y2 - y1))
        intersections.sort()
        for left, right in zip(intersections[::2], intersections[1::2]):
            first = max(0, math.ceil(left - 0.5))
            last = min(width, math.ceil(right - 0.5))
            offset = (y * width + first) * 3
            pixels[offset:offset + (last - first) * 3] = row_color * (last - first)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def make_png(size: int) -> bytes:
    # Three samples per axis keep small icons smooth without font rendering.
    factor = 3
    width = size * factor
    pixels = bytearray(bytes(BACKGROUND) * (width * width))
    for shape, color in ((ROOK, GOLD), (BASE, GOLD), (COLLAR, BACKGROUND)):
        points = [(round(x * width / 1000), round(y * width / 1000)) for x, y in shape]
        fill_polygon(pixels, width, points, color)
    rows = bytearray()
    samples = factor * factor
    for y in range(size):
        rows.append(0)  # PNG filter: None
        for x in range(size):
            totals = [0, 0, 0]
            for sy in range(factor):
                start = ((y * factor + sy) * width + x * factor) * 3
                for sx in range(factor):
                    for channel in range(3):
                        totals[channel] += pixels[start + sx * 3 + channel]
            rows.extend((value + samples // 2) // samples for value in totals)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
            + png_chunk(b"IDAT", zlib.compress(rows, 9)) + png_chunk(b"IEND", b""))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512),
                       ("icon-maskable-512.png", 512), ("apple-touch-icon.png", 180)):
        (OUTPUT / name).write_bytes(make_png(size))
        print(f"Generated {name}: {size} x {size}")
    def svg_points(points: list[tuple[int, int]]) -> str:
        return " ".join(f"{x},{y}" for x, y in points)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">\n'
        '  <rect width="1000" height="1000" fill="#173e35"/>\n'
        f'  <polygon points="{svg_points(ROOK)}" fill="#ddbf7f"/>\n'
        f'  <polygon points="{svg_points(BASE)}" fill="#ddbf7f"/>\n'
        f'  <polygon points="{svg_points(COLLAR)}" fill="#173e35"/>\n'
        '</svg>\n'
    )
    (OUTPUT / "icon.svg").write_text(svg, encoding="utf-8")
    print("Generated icon.svg")


if __name__ == "__main__":
    main()
