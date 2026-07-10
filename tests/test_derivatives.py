"""Tests for app.workers.derivatives -- thumbhash + dominant-color helpers.

thumbhash uses the real ThumbHash algorithm (github.com/evanw/thumbhash, via
the `thumbhash` PyPI port) so a future fc-mobile decoder can decode it
bit-for-bit; dominant_color is computed over OPAQUE pixels only (so a
matted image's backdrop, once cut to transparent, doesn't skew the result
toward the old backdrop color).
"""

from __future__ import annotations

import base64
import io

from app.workers.derivatives import compute_dominant_color, compute_thumbhash
from PIL import Image


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestComputeThumbhash:
    def test_returns_a_nonempty_base64_string(self) -> None:
        data = _png_bytes(Image.new("RGB", (32, 32), (100, 150, 200)))
        result = compute_thumbhash(data)
        assert isinstance(result, str)
        assert len(result) > 0
        # Must be valid base64 (round-trips through decode without error).
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_deterministic_for_the_same_image(self) -> None:
        data = _png_bytes(Image.new("RGB", (16, 16), (10, 20, 30)))
        assert compute_thumbhash(data) == compute_thumbhash(data)

    def test_differs_for_different_images(self) -> None:
        red = _png_bytes(Image.new("RGB", (16, 16), (255, 0, 0)))
        blue = _png_bytes(Image.new("RGB", (16, 16), (0, 0, 255)))
        assert compute_thumbhash(red) != compute_thumbhash(blue)

    def test_handles_rgba_source_with_transparency(self) -> None:
        img = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
        data = _png_bytes(img)
        result = compute_thumbhash(data)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fits_in_the_thumbhash_column_width(self) -> None:
        # app.models.ImageVersion.thumbhash is String(64).
        data = _png_bytes(Image.new("RGB", (100, 100), (5, 5, 5)))
        assert len(compute_thumbhash(data)) <= 64


class TestComputeDominantColor:
    def test_returns_hex_color_string(self) -> None:
        data = _png_bytes(Image.new("RGB", (20, 20), (255, 0, 0)))
        result = compute_dominant_color(data)
        assert isinstance(result, str)
        assert result.startswith("#")
        assert len(result) == 7

    def test_solid_color_image_returns_that_color(self) -> None:
        data = _png_bytes(Image.new("RGB", (20, 20), (10, 200, 30)))
        assert compute_dominant_color(data) == "#0AC81E"

    def test_majority_color_wins_over_minority(self) -> None:
        img = Image.new("RGB", (10, 10), (0, 0, 255))
        for x in range(2):
            for y in range(10):
                img.putpixel((x, y), (255, 0, 0))
        assert compute_dominant_color(_png_bytes(img)) == "#0000FF"

    def test_ignores_transparent_pixels_in_rgba_source(self) -> None:
        """A matted image's cut-out backdrop (alpha=0) must not skew the
        dominant color -- only opaque (figure) pixels should count."""
        img = Image.new("RGBA", (20, 20), (255, 255, 255, 0))  # transparent white backdrop
        for x in range(5, 15):
            for y in range(5, 15):
                img.putpixel((x, y), (20, 40, 60, 255))  # opaque subject
        result = compute_dominant_color(_png_bytes(img))
        assert result == "#14283C"

    def test_fully_transparent_image_does_not_crash(self) -> None:
        img = Image.new("RGBA", (10, 10), (1, 2, 3, 0))
        result = compute_dominant_color(_png_bytes(img))
        assert isinstance(result, str)
        assert result.startswith("#")
