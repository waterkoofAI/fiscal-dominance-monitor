#!/usr/bin/env python3
"""Minimal pure-python PNG writer + the app icon. No PIL, no converters."""
import struct, zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "docs"

def write_png(path, w, h, px):
    raw = b"".join(b"\x00" + bytes(px[y*w*4:(y+1)*w*4]) for y in range(h))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)

def blend(dst, src, a):
    return tuple(int(d + (s - d) * a) for d, s in zip(dst, src))

def make(size, maskable=False):
    w = h = size
    px = bytearray(w * h * 4)
    bg = (11, 14, 19)
    # maskable icons must keep content inside a 80% safe zone
    inset = size * 0.11 if maskable else 0.0
    r = size * (0.16 if not maskable else 0.0)          # corner radius

    stages = [(46,160,67), (212,167,44), (232,135,58), (229,72,77), (164,87,232)]
    n = len(stages)
    span = size - 2 * inset
    pad = span * 0.13
    bw = (span - pad * 2) / (n * 2 - 1)

    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            # rounded-square background
            inside = True
            if r > 0:
                for cx, cy in ((r, r), (w-r, r), (r, h-r), (w-r, h-r)):
                    if ((x < r and cx == r) or (x > w-r and cx == w-r)) and \
                       ((y < r and cy == r) or (y > h-r and cy == h-r)):
                        if (x-cx)**2 + (y-cy)**2 > r*r:
                            inside = False
            if not inside:
                px[i:i+4] = bytes((0, 0, 0, 0)); continue
            px[i:i+4] = bytes((*bg, 255))

    # ascending stage bars
    for k, col in enumerate(stages):
        bx = inset + pad + k * bw * 2
        frac = 0.26 + 0.155 * k                    # each bar taller than the last
        by = inset + span - pad - span * frac
        bh = span * frac
        for y in range(int(by), int(by + bh)):
            for x in range(int(bx), int(bx + bw)):
                if 0 <= x < w and 0 <= y < h:
                    i = (y * w + x) * 4
                    if px[i+3] == 0:
                        continue
                    px[i:i+4] = bytes((*col, 255))
    return bytes(px)

for s in (192, 512, 180):
    write_png(OUT / f"icon-{s}.png", s, s, make(s))
    print(f"icon-{s}.png")
write_png(OUT / "icon-512-maskable.png", 512, 512, make(512, maskable=True))
print("icon-512-maskable.png")

(OUT / "icon.svg").write_text('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="82" fill="#0b0e13"/>
<rect x="67" y="290" width="53" height="155" fill="#2ea043"/>
<rect x="173" y="250" width="53" height="195" fill="#d4a72c"/>
<rect x="279" y="211" width="53" height="234" fill="#e8873a"/>
<rect x="385" y="171" width="53" height="274" fill="#e5484d"/>
</svg>''')
print("icon.svg")
