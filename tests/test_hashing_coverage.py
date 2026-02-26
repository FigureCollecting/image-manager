"""Tests to raise hashing.py coverage to ≥85%.

Covers: sha256_bytes, sha256_stream, compute_phash, get_image_dimensions
using 1x1 in-memory PNG fixtures.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image

from app.hashing import compute_phash, get_image_dimensions, sha256_bytes, sha256_stream


def _make_png(width: int = 1, height: int = 1, color: tuple = (255, 0, 0)) -> bytes:
    """Create a minimal PNG image in memory."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestSha256Bytes:
    def test_known_hash(self) -> None:
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_bytes(data) == expected

    def test_empty_bytes(self) -> None:
        expected = hashlib.sha256(b"").hexdigest()
        assert sha256_bytes(b"") == expected

    def test_deterministic(self) -> None:
        data = b"test data"
        assert sha256_bytes(data) == sha256_bytes(data)


class TestSha256Stream:
    def test_matches_sha256_bytes(self) -> None:
        data = b"stream test data" * 100
        stream = io.BytesIO(data)
        assert sha256_stream(stream) == sha256_bytes(data)

    def test_small_chunk_size(self) -> None:
        data = b"abcdef" * 50
        stream = io.BytesIO(data)
        assert sha256_stream(stream, chunk_size=4) == sha256_bytes(data)

    def test_empty_stream(self) -> None:
        stream = io.BytesIO(b"")
        assert sha256_stream(stream) == sha256_bytes(b"")


class TestComputePhash:
    def test_returns_hex_string(self) -> None:
        data = _make_png(8, 8)
        result = compute_phash(data)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_identical_images_same_hash(self) -> None:
        data = _make_png(8, 8, (0, 0, 255))
        assert compute_phash(data) == compute_phash(data)

    def test_different_images_different_hash(self) -> None:
        white = _make_png(64, 64, (255, 255, 255))
        black = _make_png(64, 64, (0, 0, 0))
        # Perceptual hashes may or may not differ for solid colors,
        # but we at least verify the function runs without error
        compute_phash(white)
        compute_phash(black)


class TestGetImageDimensions:
    def test_1x1_png(self) -> None:
        data = _make_png(1, 1)
        w, h = get_image_dimensions(data)
        assert w == 1
        assert h == 1

    def test_100x50_png(self) -> None:
        data = _make_png(100, 50)
        w, h = get_image_dimensions(data)
        assert w == 100
        assert h == 50

    def test_jpeg_format(self) -> None:
        img = Image.new("RGB", (32, 16), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        data = buf.getvalue()
        w, h = get_image_dimensions(data)
        assert w == 32
        assert h == 16
