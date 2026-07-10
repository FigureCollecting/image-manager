"""Tests for app.workers.watermark -- per-family watermark overlay and
EXIF stripping, applied to matted derivatives before they're stored.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from app.workers.watermark import (
    DEFAULT_FAMILY,
    WATERMARK_CONFIG,
    apply_watermark,
    strip_exif,
)
from PIL import ExifTags, Image

_EXIF_MAKE = int(ExifTags.Base.Make)
_EXIF_ORIENTATION = int(ExifTags.Base.Orientation)


def _jpeg_with_exif(img: Image.Image, tags: dict[int, object]) -> bytes:
    exif = Image.Exif()
    for tag, value in tags.items():
        exif[tag] = value
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _rgba_canvas(size: int = 200) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Opaque "figure" block so watermark placement has something to sit near.
    for x in range(50, 150):
        for y in range(50, 150):
            img.putpixel((x, y), (30, 30, 30, 255))
    return img


class TestApplyWatermark:
    def test_returns_rgba_image_same_size(self) -> None:
        img = _rgba_canvas(200)
        out = apply_watermark(img, family="mfc")
        assert out.mode == "RGBA"
        assert out.size == img.size

    def test_modifies_pixels_for_a_known_family(self) -> None:
        img = _rgba_canvas(200)
        out = apply_watermark(img, family="mfc")
        before = np.asarray(img)
        after = np.asarray(out)
        assert not np.array_equal(before, after)

    def test_unknown_family_falls_back_to_default_without_raising(self) -> None:
        img = _rgba_canvas(200)
        out = apply_watermark(img, family="some-brand-new-source-nobody-configured")
        assert out.mode == "RGBA"
        assert out.size == img.size

    def test_default_family_is_a_valid_config_key(self) -> None:
        assert DEFAULT_FAMILY in WATERMARK_CONFIG

    def test_never_widens_transparency_outside_the_watermark_footprint(self) -> None:
        """The watermark must only touch its own corner region -- it must
        not accidentally make the whole canvas opaque or wipe the figure's
        cutout alpha elsewhere."""
        img = _rgba_canvas(200)
        out = apply_watermark(img, family="mfc")
        # A background corner far from both the figure and the (bottom-right
        # placed) watermark should remain fully transparent.
        assert out.getpixel((0, 0))[3] == 0


class TestStripExif:
    def test_removes_exif_from_info(self) -> None:
        img = Image.new("RGB", (10, 10), (1, 2, 3))
        data = _jpeg_with_exif(img, {_EXIF_MAKE: "TestCam"})
        loaded = Image.open(io.BytesIO(data))
        assert loaded.info.get("exif")  # sanity: exif really is present

        stripped = strip_exif(loaded)
        assert not stripped.info.get("exif")

    def test_stripped_image_round_trips_through_save_without_exif(self) -> None:
        img = Image.new("RGB", (10, 10), (1, 2, 3))
        data = _jpeg_with_exif(img, {_EXIF_MAKE: "TestCam"})
        loaded = Image.open(io.BytesIO(data))

        stripped = strip_exif(loaded)
        out = io.BytesIO()
        stripped.save(out, format="JPEG")
        re_loaded = Image.open(io.BytesIO(out.getvalue()))
        assert not re_loaded.info.get("exif")

    def test_applies_orientation_before_stripping(self) -> None:
        """EXIF orientation must be baked into the pixels before the raw
        EXIF (which encodes that same orientation) is discarded -- otherwise
        stripping silently un-rotates the image for every downstream viewer."""
        img = Image.new("RGB", (20, 10), (5, 6, 7))  # landscape
        data = _jpeg_with_exif(img, {_EXIF_ORIENTATION: 6})  # rotate 90 CW
        loaded = Image.open(io.BytesIO(data))

        stripped = strip_exif(loaded)
        # Orientation 6 on a 20x10 source bakes down to a 10x20 result.
        assert stripped.size == (10, 20)

    def test_no_exif_present_does_not_raise(self) -> None:
        img = Image.new("RGB", (10, 10), (1, 2, 3))
        stripped = strip_exif(img)
        assert stripped.size == (10, 10)


class TestWatermarkConfig:
    def test_mfc_family_configured(self) -> None:
        assert "mfc" in WATERMARK_CONFIG

    @pytest.mark.parametrize("family", list(WATERMARK_CONFIG.keys()))
    def test_every_configured_family_has_required_keys(self, family: str) -> None:
        cfg = WATERMARK_CONFIG[family]
        assert "text" in cfg
        assert "opacity" in cfg
        assert 0 < cfg["opacity"] <= 255
