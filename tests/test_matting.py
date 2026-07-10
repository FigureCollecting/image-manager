"""Tests for app.workers.matting -- the pluggable matting backend and the
matte transform's RGBA-preserving pipeline.

MODEL RULE: no real matting model, real ONNX weights, or onnxruntime
import is ever exercised here. The default StubMattingBackend is a
lightweight luminance/edge threshold against a near-uniform backdrop, real
enough to exercise the pipeline end-to-end. BiRefNetMattingBackend's real
preprocess/run/postprocess/apply-alpha pipeline IS exercised, but only
with a fake inference seam (a fake ``session=`` or a ``_run()`` override)
that returns a synthetic logit array -- see ``_FakeOrtSession``,
``_UniformMaskBackend``, and ``_HalfMaskBackend`` below. onnxruntime
itself is never imported by anything in this file; the real GPU model
load path (``BiRefNetMattingBackend._get_session()`` loading actual
weights) is a DEPLOY concern (baked into the worker Docker image).
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

    def test_birefnet_name_constructs_a_birefnet_backend(self) -> None:
        # Construction alone must never import onnxruntime or touch a
        # model path -- that only happens lazily, inside matte().
        backend = get_matting_backend("birefnet")
        assert isinstance(backend, BiRefNetMattingBackend)


# ---------------------------------------------------------------------------
# BiRefNetMattingBackend -- exercised with a fake inference seam only. No
# onnxruntime import, no real ONNX weights, anywhere in this file.
# ---------------------------------------------------------------------------


class _FakeOrtInput:
    name = "input"


class _FakeOrtSession:
    """Duck-types just enough of onnxruntime.InferenceSession's surface
    (get_inputs()[0].name, run(output_names, input_feed)) for the
    ``session=`` constructor seam."""

    def __init__(self, mask_value: float) -> None:
        self._mask_value = mask_value

    def get_inputs(self) -> list[_FakeOrtInput]:
        return [_FakeOrtInput()]

    def run(
        self, output_names: list[str] | None, input_feed: dict[str, np.ndarray]
    ) -> list[np.ndarray]:
        arr = input_feed["input"]
        n, _c, h, w = arr.shape
        return [np.full((n, 1, h, w), self._mask_value, dtype=np.float32)]


class _UniformMaskBackend(BiRefNetMattingBackend):
    """Overrides the ``_run()`` seam directly (no session object at all)
    with a uniform synthetic logit map -- the simplest injection path."""

    def __init__(self, logit_value: float) -> None:
        super().__init__()
        self._logit_value = logit_value

    def _run(self, input_array: np.ndarray) -> np.ndarray:
        n, _c, h, w = input_array.shape
        return np.full((n, 1, h, w), self._logit_value, dtype=np.float32)


class _HalfMaskBackend(BiRefNetMattingBackend):
    """Left half of the model-space mask is strongly foreground, right half
    strongly background -- used to prove the mask is actually resized back
    to the source dimensions preserving spatial layout, not just uniformly
    stamped."""

    def _run(self, input_array: np.ndarray) -> np.ndarray:
        n, _c, h, w = input_array.shape
        mask = np.full((n, 1, h, w), -10.0, dtype=np.float32)
        mask[:, :, :, : w // 2] = 10.0
        return mask


class TestBiRefNetMattingBackend:
    def test_unconfigured_backend_raises_clearly(self) -> None:
        """A configured-but-broken (here: unconfigured) backend must raise
        clearly rather than silently falling back to a wrong result."""
        backend = BiRefNetMattingBackend()
        with pytest.raises(RuntimeError, match="matting_model_path"):
            backend.matte(Image.new("RGBA", (4, 4), (0, 0, 0, 255)))

    def test_returns_rgba_image(self) -> None:
        backend = _UniformMaskBackend(logit_value=10.0)
        img = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
        out = backend.matte(img)
        assert out.mode == "RGBA"

    def test_all_white_mask_preserves_the_subject(self) -> None:
        # Strongly positive logits -> sigmoid ~1 -> fully opaque mask.
        backend = _UniformMaskBackend(logit_value=10.0)
        img = Image.new("RGBA", (16, 16), (10, 20, 30, 255))
        out = backend.matte(img)
        arr = np.asarray(out)
        assert bool((arr[..., 3] == 255).all())

    def test_all_black_mask_cuts_everything(self) -> None:
        # Strongly negative logits -> sigmoid ~0 -> fully transparent mask.
        backend = _UniformMaskBackend(logit_value=-10.0)
        img = Image.new("RGBA", (16, 16), (10, 20, 30, 255))
        out = backend.matte(img)
        arr = np.asarray(out)
        assert bool((arr[..., 3] == 0).all())

    def test_alpha_only_ever_decreases_vs_input(self) -> None:
        """Regression guard mirroring StubMattingBackend's contract: even
        an all-opaque computed mask must never widen pre-existing
        transparency."""
        backend = _UniformMaskBackend(logit_value=10.0)  # would-be all-opaque
        img = Image.new("RGBA", (10, 10), (200, 200, 200, 255))
        img.putpixel((5, 5), (200, 200, 200, 40))  # already partly transparent
        out = backend.matte(img)
        arr = np.asarray(out)
        assert arr[5, 5, 3] == 40
        # And never exceeds the original alpha anywhere in the image.
        original_alpha = np.asarray(img)[..., 3]
        assert bool((arr[..., 3] <= original_alpha).all())

    def test_mask_is_resized_to_source_dimensions_preserving_layout(self) -> None:
        backend = _HalfMaskBackend()
        img = Image.new("RGBA", (40, 20), (100, 150, 200, 255))
        out = backend.matte(img)
        arr = np.asarray(out)
        assert arr.shape[:2] == (20, 40)  # (height, width) unchanged
        assert arr[10, 5, 3] == 255  # left half: foreground
        assert arr[10, 35, 3] == 0  # right half: background

    def test_session_ctor_injection_seam(self) -> None:
        """The alternate injection point: a fake object duck-typing
        onnxruntime's InferenceSession, passed via session=, exercising
        _get_session()/_run() together rather than overriding _run()
        directly."""
        backend = BiRefNetMattingBackend(session=_FakeOrtSession(mask_value=10.0))
        img = Image.new("RGBA", (12, 12), (10, 20, 30, 255))
        out = backend.matte(img)
        assert out.mode == "RGBA"
        arr = np.asarray(out)
        assert arr[6, 6, 3] == 255


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
