"""Generate crisp multi-resolution Tauri & Windows desktop icons using pure stdlib."""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def encode_png_rgba(width: int, height: int, pixels: bytes) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0
        start = y * stride
        raw.extend(pixels[start : start + stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def build_ico(frames: list[tuple[int, bytes]]) -> bytes:
    """Build a Windows .ico containing PNG-compressed RGBA frames."""
    count = len(frames)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    entries = []
    payloads = []
    for size, png_data in frames:
        dim = 0 if size >= 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dim,
                dim,
                0,
                0,
                1,
                32,
                len(png_data),
                offset,
            )
        )
        payloads.append(png_data)
        offset += len(png_data)
    return header + b"".join(entries) + b"".join(payloads)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    t = _clamp01((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _sd_rounded_box(px: float, py: float, bx: float, by: float, r: float) -> float:
    dx = abs(px) - bx + r
    dy = abs(py) - by + r
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - r


def _sd_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = _clamp01((pax * bax + pay * bay) / denom) if denom > 0 else 0.0
    return math.hypot(pax - bax * h, pay - bay * h)


def _blend(dst: tuple[float, float, float, float], src: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    out_a = sa + da * (1.0 - sa)
    if out_a <= 1e-6:
        return (0.0, 0.0, 0.0, 0.0)
    out_r = (sr * sa + dr * da * (1.0 - sa)) / out_a
    out_g = (sg * sa + dg * da * (1.0 - sa)) / out_a
    out_b = (sb * sa + db * da * (1.0 - sa)) / out_a
    return (out_r, out_g, out_b, out_a)


def _sample_icon(u: float, v: float, aa: float) -> tuple[float, float, float, float]:
    """Evaluate icon RGBA in normalized coordinates u,v in [-1, 1]."""
    # Outer dark slate-teal squircle tile
    d_tile = _sd_rounded_box(u, v, 0.86, 0.86, 0.24)
    tile_a = 1.0 - _smoothstep(-aa, aa, d_tile)
    if tile_a <= 0.001:
        return (0.0, 0.0, 0.0, 0.0)

    # Smooth diagonal gradient (#172b30 -> #114b5f -> #0a84ff accent glow top-right)
    grad_t = _clamp01(0.5 * ((u + 1.0) * 0.45 + (1.0 - v) * 0.55))
    glow = math.exp(-((u - 0.35) ** 2 + (v + 0.40) ** 2) * 1.9)
    r = (20 + grad_t * 14 + glow * 16) / 255.0
    g = (43 + grad_t * 70 + glow * 85) / 255.0
    b = (48 + grad_t * 105 + glow * 140) / 255.0
    col = (min(1.0, r), min(1.0, g), min(1.0, b), tile_a)

    # Subtle inner rim highlight
    d_inner = abs(d_tile + 0.025) - 0.012
    rim_a = (1.0 - _smoothstep(-aa, aa, d_inner)) * tile_a * (0.28 + 0.22 * (1.0 - v) * 0.5)
    col = _blend(col, (1.0, 1.0, 1.0, rim_a))

    # Stylized isometric 3-layer stack + precision time-hand motif (Layers3 + Clock)
    # Isometric diamond helper: diamond around center cy with half-width w and half-height h
    def diamond_dist(px: float, py: float, cy: float, hw: float, hh: float) -> float:
        # L1 norm ellipse approximation for rounded diamond edges
        nx = abs(px) / hw
        ny = abs(py - cy) / hh
        return (nx + ny - 1.0) * min(hw, hh) * 0.7071

    # Bottom layer (soft sky blue)
    d_bot_v = min(
        _sd_segment(u, v, -0.44, 0.20, 0.0, 0.41),
        _sd_segment(u, v, 0.0, 0.41, 0.44, 0.20),
    ) - 0.052
    a_bot = 1.0 - _smoothstep(-aa, aa, d_bot_v)
    if a_bot > 0:
        col = _blend(col, (0.39, 0.82, 1.0, a_bot * 0.82))

    # Middle layer (bright azure)
    d_mid = min(
        _sd_segment(u, v, -0.44, 0.02, 0.0, 0.23),
        _sd_segment(u, v, 0.0, 0.23, 0.44, 0.02),
    ) - 0.052
    a_mid = 1.0 - _smoothstep(-aa, aa, d_mid)
    if a_mid > 0:
        col = _blend(col, (0.65, 0.91, 1.0, a_mid * 0.92))

    # Top glowing diamond card (crisp white)
    d_top = diamond_dist(u, v, -0.20, 0.47, 0.23) - 0.032
    a_top = 1.0 - _smoothstep(-aa, aa, d_top)
    if a_top > 0:
        # Subtle vertical sheen on top card
        sheen = 0.94 + 0.06 * _clamp01((-v) * 2.0)
        col = _blend(col, (0.97 * sheen, 0.99 * sheen, 1.0, a_top))

    # Subtle cyan/teal clock-tick dot & hands inside top badge
    d_center = math.hypot(u, v + 0.20) - 0.042
    d_h1 = _sd_segment(u, v, 0.0, -0.20, 0.0, -0.32) - 0.022
    d_h2 = _sd_segment(u, v, 0.0, -0.20, 0.14, -0.15) - 0.022
    d_hands = min(d_center, d_h1, d_h2)
    a_hands = 1.0 - _smoothstep(-aa, aa, d_hands)
    if a_hands > 0:
        col = _blend(col, (0.04, 0.49, 0.96, a_hands * 0.95))

    return col


def render_png(size: int) -> bytes:
    pixels = bytearray(size * size * 4)
    aa = 2.2 / max(size, 16)
    # 2x2 subpixel grid gives smooth edges even at 16px and 32px
    offsets = (-0.25, 0.25)
    inv_samples = 0.25
    idx = 0
    for y in range(size):
        for x in range(size):
            ar = ag = ab = aa_sum = 0.0
            for oy in offsets:
                for ox in offsets:
                    u = ((x + 0.5 + ox) / size) * 2.0 - 1.0
                    v = ((y + 0.5 + oy) / size) * 2.0 - 1.0
                    cr, cg, cb, ca = _sample_icon(u, v, aa)
                    ar += cr * ca
                    ag += cg * ca
                    ab += cb * ca
                    aa_sum += ca
            alpha = aa_sum * inv_samples
            if alpha > 1e-6:
                r = int(_clamp01(ar * inv_samples / alpha) * 255 + 0.5)
                g = int(_clamp01(ag * inv_samples / alpha) * 255 + 0.5)
                b = int(_clamp01(ab * inv_samples / alpha) * 255 + 0.5)
                a = int(_clamp01(alpha) * 255 + 0.5)
            else:
                r = g = b = a = 0
            pixels[idx : idx + 4] = bytes((r, g, b, a))
            idx += 4
    return encode_png_rgba(size, size, bytes(pixels))


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    icons_dir = root / "src-tauri" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    rendered: dict[int, bytes] = {}
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        rendered[size] = render_png(size)

    (icons_dir / "32x32.png").write_bytes(rendered[32])
    (icons_dir / "128x128.png").write_bytes(rendered[128])
    (icons_dir / "128x128@2x.png").write_bytes(rendered[256])
    (icons_dir / "icon.png").write_bytes(rendered[512])
    ico_bytes = build_ico([(s, rendered[s]) for s in (16, 24, 32, 48, 64, 128, 256)])
    (icons_dir / "icon.ico").write_bytes(ico_bytes)
    print(f"Generated icons in {icons_dir}")


if __name__ == "__main__":
    main()
