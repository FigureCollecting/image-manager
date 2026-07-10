"""Lightweight placeholder-rendering derivatives: thumbhash + dominant color.

Both are computed from a version's own bytes (typically the matted
derivative) and stored on that ImageVersion row alongside the grounding
scalars (see app.workers.grounding).
"""

from __future__ import annotations

import base64
import io
import math

import numpy as np
from PIL import Image, ImageOps

#: Ignore near-transparent pixels when computing dominant color -- mirrors
#: app.workers.grounding.ALPHA_THRESHOLD so a matted image's cut-out
#: backdrop never counts as "content".
_DOMINANT_COLOR_ALPHA_THRESHOLD = 10

#: ThumbHash never encodes an image larger than 100x100 on its long edge.
_THUMBHASH_MAX_SIZE = 100


def _thumbhash_encode_channel(
    channel: list[float], w: int, h: int, nx: int, ny: int
) -> tuple[float, list[float], float]:
    """DCT-ish frequency encode of one channel. Direct port of the
    reference ThumbHash algorithm (github.com/evanw/thumbhash, Evan
    Wallace, MIT licensed)."""
    dc = 0.0
    ac: list[float] = []
    scale = 0.0
    fx = [0.0] * w

    cy = 0
    while cy < ny:
        cx = 0
        while cx * ny < nx * (ny - cy):
            f = 0.0
            for x in range(w):
                fx[x] = math.cos(math.pi / w * cx * (x + 0.5))
            for y in range(h):
                fy = math.cos(math.pi / h * cy * (y + 0.5))
                for x in range(w):
                    f += channel[x + y * w] * fx[x] * fy
            f /= w * h
            if cx > 0 or cy > 0:
                ac.append(f)
                scale = max(scale, abs(f))
            else:
                dc = f
            cx += 1
        cy += 1

    if scale:
        ac = [0.5 + 0.5 / scale * f for f in ac]
    return dc, ac, scale


def _rgba_to_thumb_hash(w: int, h: int, rgba: list[int]) -> list[int]:
    """Encode raw RGBA pixel bytes to a ThumbHash byte list.

    Vendored (not pip-installed) because the published `thumbhash` PyPI
    port (0.1.2) has a real bug for images with actual transparency: its
    ``a_dc, a_ac, a_scale = encode_channel(...) if has_alpha else 1.0, [], 1.0``
    line is an unparenthesized ternary, so on the has_alpha branch a_dc is
    bound to the whole (dc, ac, scale) TUPLE instead of just dc -- it
    crashes on any image that actually has transparency, which is exactly
    our primary case (matted derivatives). This port keeps the same
    algorithm with that one expression parenthesized correctly.
    """
    if w > 100 or h > 100:
        raise ValueError(f"{w}x{h} doesn't fit in 100x100")

    avg_r = avg_g = avg_b = avg_a = 0.0
    for i in range(w * h):
        j = i * 4
        alpha = rgba[j + 3] / 255
        avg_r += alpha / 255 * rgba[j]
        avg_g += alpha / 255 * rgba[j + 1]
        avg_b += alpha / 255 * rgba[j + 2]
        avg_a += alpha

    if avg_a:
        avg_r /= avg_a
        avg_g /= avg_a
        avg_b /= avg_a

    has_alpha = avg_a < w * h
    l_limit = 5 if has_alpha else 7
    lx = max(1, round(l_limit * w / max(w, h)))
    ly = max(1, round(l_limit * h / max(w, h)))
    lum: list[float] = []
    p: list[float] = []
    q: list[float] = []
    a: list[float] = []

    for i in range(w * h):
        j = i * 4
        alpha = rgba[j + 3] / 255
        r = avg_r * (1 - alpha) + alpha / 255 * rgba[j]
        g = avg_g * (1 - alpha) + alpha / 255 * rgba[j + 1]
        b = avg_b * (1 - alpha) + alpha / 255 * rgba[j + 2]
        lum.append((r + g + b) / 3)
        p.append((r + g) / 2 - b)
        q.append(r - g)
        a.append(alpha)

    l_dc, l_ac, l_scale = _thumbhash_encode_channel(lum, w, h, max(3, lx), max(3, ly))
    p_dc, p_ac, p_scale = _thumbhash_encode_channel(p, w, h, 3, 3)
    q_dc, q_ac, q_scale = _thumbhash_encode_channel(q, w, h, 3, 3)
    if has_alpha:
        a_dc, a_ac, a_scale = _thumbhash_encode_channel(a, w, h, 5, 5)
    else:
        a_dc, a_ac, a_scale = 1.0, [], 1.0

    is_landscape = w > h
    header24 = (
        round(63 * l_dc)
        | (round(31.5 + 31.5 * p_dc) << 6)
        | (round(31.5 + 31.5 * q_dc) << 12)
        | (round(31 * l_scale) << 18)
        | (has_alpha << 23)
    )
    header16 = (
        (ly if is_landscape else lx)
        | (round(63 * p_scale) << 3)
        | (round(63 * q_scale) << 9)
        | (is_landscape << 15)
    )
    thumb_hash = [
        header24 & 255,
        (header24 >> 8) & 255,
        header24 >> 16,
        header16 & 255,
        header16 >> 8,
    ]

    is_odd = False
    if has_alpha:
        thumb_hash.append(round(15 * a_dc) | (round(15 * a_scale) << 4))

    for channel_ac in (l_ac, p_ac, q_ac):
        for f in channel_ac:
            u = round(15.0 * f)
            if is_odd:
                thumb_hash[-1] |= u << 4
            else:
                thumb_hash.append(u)
            is_odd = not is_odd

    if has_alpha:
        for f in a_ac:
            u = round(15.0 * f)
            if is_odd:
                thumb_hash[-1] |= u << 4
            else:
                thumb_hash.append(u)
            is_odd = not is_odd

    return thumb_hash


def compute_thumbhash(data: bytes) -> str:
    """Compact placeholder hash (the real ThumbHash algorithm -- see
    github.com/evanw/thumbhash) for low-cost blur-up rendering, base64-encoded
    for storage in ImageVersion.thumbhash (String(64))."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    img.thumbnail((_THUMBHASH_MAX_SIZE, _THUMBHASH_MAX_SIZE))
    img = ImageOps.exif_transpose(img) or img

    rgba = [channel for pixel in img.getdata() for channel in pixel]
    raw_bytes = _rgba_to_thumb_hash(img.width, img.height, rgba)
    return base64.b64encode(bytes(raw_bytes)).decode("ascii")


def compute_dominant_color(data: bytes) -> str:
    """Dominant color of the image as `#RRGGBB`, computed over opaque
    pixels only (alpha > threshold) so a matted image's transparent
    backdrop never skews the result toward the old (now-removed) backdrop
    color. Falls back to considering all pixels if the image is fully
    transparent (degenerate case -- still returns a deterministic color
    rather than crashing)."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    arr = np.asarray(img)
    alpha = arr[..., 3]
    mask = alpha > _DOMINANT_COLOR_ALPHA_THRESHOLD
    if not mask.any():
        mask = np.ones_like(alpha, dtype=bool)

    pixels = arr[mask][:, :3].astype(np.uint32)
    # Coarse 5-bit-per-channel histogram bucket (32 levels/channel) to find
    # the most common color region, then average the actual pixels in that
    # bucket for a truer color than the bucket's quantized center.
    buckets = pixels >> 3
    keys = (buckets[:, 0] << 10) | (buckets[:, 1] << 5) | buckets[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    top_key = values[np.argmax(counts)]

    winning_pixels = pixels[keys == top_key]
    r, g, b = (int(round(c)) for c in winning_pixels.mean(axis=0))
    return f"#{r:02X}{g:02X}{b:02X}"
