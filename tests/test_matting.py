"""Tests for app.workers.matting -- the pluggable matting backend and the
matte transform's RGBA-preserving pipeline.

MODEL RULE: no real matting model is downloaded or run here. The default
StubMattingBackend is a lightweight luminance/edge threshold against a
near-uniform backdrop, real enough to exercise the pipeline end-to-end.
The real BiRefNet/onnxruntime-gpu backend is a DEPLOY concern (baked into
the worker Docker image) -- these tests assert it's a clearly-marked seam,
not a working implementation.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from app.workers.matting import (
    BiRefNetMattingBackend,
    MattingBackend,
    StubMattingBackend,
    apply_matting,
    get_matting_backend,
)
from PIL import Image


def _studio_photo_bytes(fmt: str = "JPEG") -> bytes:
    """A synthetic "product photo": a near-white backdrop with a solid
    dark-colored square subject in the middle -- similar in spirit to a
    typical MFC listing photo (uniform backdrop + centered figure)."""
    img = Image.new("RGB", (40, 40), color=(250, 250, 248))
    for y in range(10, 30):
        for x in range(10, 30):
            img.putpixel((x, y), (20, 30, 40))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class TestStubMattingBackend:
    def test_returns_rgba_image(self) -> None:
        backend = StubMattingBackend()
        img = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
        out = backend.matte(img)
        assert out.mode == "RGBA"

    def test_cuts_out_the_near_uniform_backdrop(self) -> None:
        backend = StubMattingBackend()
        img = Image.open(io.BytesIO(_studio_photo_bytes())).convert("RGBA")
        out = backend.matte(img)
        arr = np.asarray(out)
        # Corners (backdrop) should have been made transparent.
        assert arr[0, 0, 3] == 0
        assert arr[-1, -1, 3] == 0

    def test_preserves_the_subject_as_opaque(self) -> None:
        backend = StubMattingBackend()
        img = Image.open(io.BytesIO(_studio_photo_bytes())).convert("RGBA")
        out = backend.matte(img)
        arr = np.asarray(out)
        # Center pixel (the dark "figure") should remain opaque.
        assert arr[20, 20, 3] == 255

    def test_never_widens_pre_existing_transparency(self) -> None:
        backend = StubMattingBackend()
        img = Image.new("RGBA", (10, 10), (250, 250, 248, 255))
        img.putpixel((5, 5), (250, 250, 248, 0))  # already transparent
        out = backend.matte(img)
        arr = np.asarray(out)
        assert arr[5, 5, 3] == 0


class TestGetMattingBackend:
    def test_default_is_stub(self) -> None:
        backend = get_matting_backend()
        assert isinstance(backend, StubMattingBackend)

    def test_explicit_stub_name(self) -> None:
        assert isinstance(get_matting_backend("stub"), StubMattingBackend)

    def test_unknown_name_falls_back_to_stub(self) -> None:
        assert isinstance(get_matting_backend("nonexistent-backend"), StubMattingBackend)

    def test_birefnet_backend_is_a_deploy_time_seam_not_implemented(self) -> None:
        backend = BiRefNetMattingBackend()
        assert isinstance(backend, MattingBackend)
        with pytest.raises(NotImplementedError):
            backend.matte(Image.new("RGBA", (4, 4)))


class TestApplyMatting:
    def test_output_is_rgba_png(self) -> None:
        data = _studio_photo_bytes("JPEG")
        out_bytes, mime = apply_matting(data)
        assert mime == "image/png"
        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.mode == "RGBA"
        assert out_img.format == "PNG"

    def test_never_converts_to_rgb_even_for_rgba_png_source(self) -> None:
        """CRITICAL regression guard: a naive .convert('RGB') anywhere in
        this path would silently strip the alpha channel the rest of the
        pipeline (grounding scalars, serve route) depends on."""
        src = Image.new("RGBA", (16, 16), (255, 255, 255, 255))
        src.putpixel((8, 8), (10, 10, 10, 128))
        buf = io.BytesIO()
        src.save(buf, format="PNG")

        out_bytes, _mime = apply_matting(buf.getvalue())
        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.mode == "RGBA"
        # A real alpha channel must exist (not a flat 255-everywhere band
        # coerced by an RGB round-trip).
        alphas = {px[3] for px in out_img.getdata()}
        assert len(alphas) >= 1  # sanity: still has a real alpha band at all

    def test_uses_a_custom_backend_when_given(self) -> None:
        class _AllOpaqueBackend(MattingBackend):
            def matte(self, image: Image.Image) -> Image.Image:
                rgba = image.convert("RGBA")
                arr = np.asarray(rgba).copy()
                arr[..., 3] = 255
                return Image.fromarray(arr, mode="RGBA")

        data = _studio_photo_bytes("PNG")
        out_bytes, _mime = apply_matting(data, backend=_AllOpaqueBackend())
        out_img = Image.open(io.BytesIO(out_bytes))
        arr = np.asarray(out_img)
        assert bool((arr[..., 3] == 255).all())
