"""Tests for app.workers.grounding — the Python port of fc-mobile's
src/components/display/alphaMargin.ts (computeBottomMarginFrac /
computeContactBand). Same algorithm, same thresholds, operating on a raw
RGBA numpy array (the sharp/PIL-equivalent of the JS AlphaBuffer) instead
of a DOM ImageData/canvas.

Test fixtures mirror alphaMargin.test.ts's bufferWithBottomMargin /
bufferWithContactBand builders so the two suites can be compared directly.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.workers.grounding import compute_bottom_margin_frac, compute_contact_band


def _buffer_with_bottom_margin(width: int, height: int, opaque_until_row: int) -> np.ndarray:
    """Fully opaque above `opaque_until_row` (exclusive), fully transparent
    from there to the bottom -- mirrors the JS bufferWithBottomMargin."""
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    arr[:opaque_until_row, :, 3] = 255
    return arr


def _buffer_with_contact_band(
    width: int, height: int, band_rows: int, x_start: int, x_end: int
) -> np.ndarray:
    """Full-width opaque body above the bottom `band_rows` rows, and a
    narrower [x_start, x_end) opaque base within those bottom rows --
    mirrors the JS bufferWithContactBand (Ryuko off-center-base fixture)."""
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    band_start_row = height - band_rows
    arr[:band_start_row, :, 3] = 255
    arr[band_start_row:height, x_start:x_end, 3] = 255
    return arr


class TestComputeBottomMarginFrac:
    def test_returns_0_when_content_extends_to_last_row(self) -> None:
        buf = _buffer_with_bottom_margin(10, 100, 100)
        assert compute_bottom_margin_frac(buf) == 0

    def test_returns_exact_fraction_of_transparent_rows_below_last_opaque(self) -> None:
        # Opaque rows 0..77 (78 rows), transparent 78..99 (22 rows) of 100.
        buf = _buffer_with_bottom_margin(10, 100, 78)
        assert compute_bottom_margin_frac(buf) == pytest.approx(22 / 100)

    def test_matches_real_measured_figure_madoka_41_712(self) -> None:
        buf = _buffer_with_bottom_margin(10, 712, 712 - 41)
        assert compute_bottom_margin_frac(buf) == pytest.approx(41 / 712, abs=1e-3)

    def test_ignores_near_zero_anti_aliasing_alpha_noise(self) -> None:
        width, height = 10, 50
        arr = np.zeros((height, width, 4), dtype=np.uint8)
        arr[:20, :, 3] = 255
        arr[20:50, :, 3] = 3  # faint AA dust -- below ALPHA_THRESHOLD (10)
        assert compute_bottom_margin_frac(arr) == pytest.approx(30 / 50)

    def test_returns_0_for_fully_transparent_image(self) -> None:
        arr = np.zeros((40, 10, 4), dtype=np.uint8)
        assert compute_bottom_margin_frac(arr) == 0

    def test_never_throws_on_degenerate_zero_size_buffer(self) -> None:
        arr = np.zeros((0, 0, 4), dtype=np.uint8)
        assert compute_bottom_margin_frac(arr) == 0


class TestComputeContactBand:
    def test_measures_off_center_base_ignoring_wider_body_above(self) -> None:
        # 100-wide image; base occupies x=[10,30) in the bottom 8 rows.
        buf = _buffer_with_contact_band(100, 100, 8, 10, 30)
        band = compute_contact_band(buf)
        assert band is not None
        center_x_frac, width_frac = band
        assert center_x_frac == pytest.approx(19.5 / 100)
        assert width_frac == pytest.approx(19 / 100)

    def test_measures_centered_base_as_center_x_frac_about_half(self) -> None:
        buf = _buffer_with_contact_band(100, 100, 8, 40, 60)
        band = compute_contact_band(buf)
        assert band is not None
        assert band[0] == pytest.approx(49.5 / 100)

    def test_reads_bottom_contact_band_not_just_last_opaque_row(self) -> None:
        width, height = 100, 100
        arr = np.zeros((height, width, 4), dtype=np.uint8)
        # Base band: rows 90..97 (8 rows), x=[20,40).
        arr[90:98, 20:40, 3] = 255
        # A single stray opaque pixel at the very last row, far off to the side.
        arr[99, 95, 3] = 255
        band = compute_contact_band(arr)
        assert band is not None
        assert band[1] > 20 / 100

    def test_returns_none_for_fully_transparent_image(self) -> None:
        arr = np.zeros((50, 50, 4), dtype=np.uint8)
        assert compute_contact_band(arr) is None

    def test_never_throws_on_degenerate_zero_size_buffer(self) -> None:
        arr = np.zeros((0, 0, 4), dtype=np.uint8)
        assert compute_contact_band(arr) is None

    def test_ignores_near_zero_anti_aliasing_alpha_noise(self) -> None:
        width, height = 100, 100
        arr = np.zeros((height, width, 4), dtype=np.uint8)
        arr[99, :, 3] = 3  # faint AA dust across the whole bottom row
        arr[90:95, 30:50, 3] = 255
        band = compute_contact_band(arr)
        assert band is not None
        assert band[0] == pytest.approx(39.5 / 100)
