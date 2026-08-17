#!/usr/bin/env python3
"""Generate the app icons.

Pillow is not a dependency of anything else here, so the icons are drawn by
hand and written with zlib -- a PNG is a handful of length-prefixed chunks
around a zlib stream, which is less code than adding an image library.

    python3 gifmaker/icons/make_icons.py

Re-run it only if you want to change the artwork; the PNGs are committed.
"""

import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))

# Render big, then average down -- cheap anti-aliasing without a rasteriser.
SUPERSAMPLE = 3
BASE = 512

BG_TOP = (0x8C, 0x6B, 0xFF)
BG_BOTTOM = (0x4A, 0x2F, 0xD0)


class Canvas:
    def __init__(self, size):
        self.size = size
        self.buf = bytearray(size * size * 3)

    def blend(self, x, y, colour, alpha):
        if alpha <= 0:
            return
        if alpha > 1:
            alpha = 1.0
        offset = (y * self.size + x) * 3
        for channel in range(3):
            old = self.buf[offset + channel]
            self.buf[offset + channel] = int(old + (colour[channel] - old) * alpha)

    def gradient(self, top, bottom):
        for y in range(self.size):
            t = y / (self.size - 1)
            row = bytes(
                int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)
            ) * self.size
            self.buf[y * self.size * 3:(y + 1) * self.size * 3] = row

    def rounded_rect(self, x0, y0, x1, y1, radius, colour, alpha=1.0):
        for y in range(max(0, int(y0)), min(self.size, int(y1) + 1)):
            for x in range(max(0, int(x0)), min(self.size, int(x1) + 1)):
                if _inside_rounded(x + 0.5, y + 0.5, x0, y0, x1, y1, radius):
                    self.blend(x, y, colour, alpha)

    def triangle(self, points, colour, alpha=1.0):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        for y in range(max(0, int(min(ys))), min(self.size, int(max(ys)) + 1)):
            for x in range(max(0, int(min(xs))), min(self.size, int(max(xs)) + 1)):
                if _inside_triangle(x + 0.5, y + 0.5, points):
                    self.blend(x, y, colour, alpha)

    def downsample(self, factor):
        out_size = self.size // factor
        out = bytearray(out_size * out_size * 3)
        area = factor * factor
        for y in range(out_size):
            for x in range(out_size):
                totals = [0, 0, 0]
                for dy in range(factor):
                    row = (y * factor + dy) * self.size
                    for dx in range(factor):
                        offset = (row + x * factor + dx) * 3
                        totals[0] += self.buf[offset]
                        totals[1] += self.buf[offset + 1]
                        totals[2] += self.buf[offset + 2]
                out_offset = (y * out_size + x) * 3
                for channel in range(3):
                    out[out_offset + channel] = totals[channel] // area
        return out_size, out


def _inside_rounded(px, py, x0, y0, x1, y1, radius):
    if px < x0 or px > x1 or py < y0 or py > y1:
        return False
    cx = min(max(px, x0 + radius), x1 - radius)
    cy = min(max(py, y0 + radius), y1 - radius)
    return (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius


def _inside_triangle(px, py, points):
    (ax, ay), (bx, by), (cx, cy) = points

    def edge(x0, y0, x1, y1):
        return (px - x0) * (y1 - y0) - (py - y0) * (x1 - x0)

    d1 = edge(ax, ay, bx, by)
    d2 = edge(bx, by, cx, cy)
    d3 = edge(cx, cy, ax, ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def write_png(path, size, rgb):
    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xffffffff))

    # Each scanline is prefixed with a filter byte; 0 means "no filter".
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw += rgb[y * size * 3:(y + 1) * size * 3]

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")

    with open(path, "wb") as handle:
        handle.write(png)


def draw():
    """A stack of frames with a play arrow -- 'pictures, in motion'."""
    size = BASE * SUPERSAMPLE
    canvas = Canvas(size)
    canvas.gradient(BG_TOP, BG_BOTTOM)

    unit = size / 512.0
    white = (255, 255, 255)

    # Two frames peeking out behind, then the front one.
    canvas.rounded_rect(150 * unit, 118 * unit, 400 * unit, 320 * unit,
                        26 * unit, white, 0.28)
    canvas.rounded_rect(128 * unit, 142 * unit, 378 * unit, 344 * unit,
                        26 * unit, white, 0.45)
    canvas.rounded_rect(106 * unit, 166 * unit, 356 * unit, 368 * unit,
                        28 * unit, white, 1.0)

    # Play arrow punched out of the front frame.
    canvas.triangle([
        (198 * unit, 214 * unit),
        (198 * unit, 320 * unit),
        (286 * unit, 267 * unit),
    ], BG_BOTTOM, 1.0)

    # A baseline bar hints at the filmstrip without needing text.
    canvas.rounded_rect(106 * unit, 400 * unit, 262 * unit, 424 * unit,
                        12 * unit, white, 0.85)
    canvas.rounded_rect(278 * unit, 400 * unit, 356 * unit, 424 * unit,
                        12 * unit, white, 0.45)

    return canvas


def main():
    canvas = draw()
    full_size, full = canvas.downsample(SUPERSAMPLE)

    write_png(os.path.join(HERE, "icon-512.png"), full_size, full)

    for name, target in [("icon-192.png", 192), ("apple-touch-icon.png", 180)]:
        factor = full_size / target
        small = bytearray(target * target * 3)
        for y in range(target):
            for x in range(target):
                # Nearest-neighbour from the already-smooth 512px render.
                sx = min(full_size - 1, int(x * factor))
                sy = min(full_size - 1, int(y * factor))
                src = (sy * full_size + sx) * 3
                dst = (y * target + x) * 3
                small[dst:dst + 3] = full[src:src + 3]
        write_png(os.path.join(HERE, name), target, small)

    print("wrote icon-512.png, icon-192.png, apple-touch-icon.png")


if __name__ == "__main__":
    main()
