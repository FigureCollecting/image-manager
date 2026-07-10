"""Pluggable matting (background-removal) pipeline.

MODEL RULE: this module never downloads or runs a real subject-segmentation
model. StubMattingBackend is a lightweight luminance/edge threshold against
a near-uniform backdrop -- good enough to exercise the pipeline end-to-end
in dev/tests. The real backend (BiRefNet via onnxruntime-gpu) is a DEPLOY
concern: its weights + GPU runtime get baked into the worker Docker image
(see Dockerfile.worker) at deploy time, never installed or invoked from
application code or tests. BiRefNetMattingBackend below is the seam for
that: it intentionally raises until it's wired up at the deploy layer.

CRITICAL: nothing in this module (or its callers) may call
``.convert("RGB")`` on the working image -- that silently drops the alpha
channel that the grounding scalars (app.workers.grounding) and the serve
route depend on. Always work in RGBA.
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod

import numpy as np
from PIL import Image

from ..hashing import detect_mime


class MattingBackend(ABC):
    """Interface for producing an RGBA cutout from a source image."""

    @abstractmethod
    def matte(self, image: Image.Image) -> Image.Image:
        """Given an RGBA PIL Image, return a new RGBA Image with the alpha
        channel replaced (or refined) to cut out the background."""
        raise NotImplementedError


class StubMattingBackend(MattingBackend):
    """Default backend: samples a near-uniform backdrop color from the
    image's own corners and thresholds pixel distance from it to transparent
    vs. opaque. This is NOT real subject segmentation -- it's a stand-in
    that produces a real RGBA cutout so the rest of the pipeline (grounding
    scalars, watermark, serve route) can be built and tested against real
    alpha data without a GPU model. Swap via get_matting_backend() /
    Settings.matting_backend once a real backend is deployed.
    """

    #: Euclidean RGB distance from the sampled backdrop color below which a
    #: pixel is considered part of the backdrop (made transparent).
    BACKDROP_DISTANCE_THRESHOLD = 30.0
    #: Corner patch size (pixels) used to sample the backdrop color.
    CORNER_SAMPLE = 5

    def matte(self, image: Image.Image) -> Image.Image:
        rgba = image.convert("RGBA")
        arr = np.asarray(rgba)
        h, w = arr.shape[0], arr.shape[1]
        rgb = arr[..., :3].astype(np.float32)

        n = max(1, min(self.CORNER_SAMPLE, h, w))
        corners = np.concatenate(
            [
                rgb[:n, :n].reshape(-1, 3),
                rgb[:n, -n:].reshape(-1, 3),
                rgb[-n:, :n].reshape(-1, 3),
                rgb[-n:, -n:].reshape(-1, 3),
            ]
        )
        backdrop = np.median(corners, axis=0)

        dist = np.sqrt(((rgb - backdrop) ** 2).sum(axis=-1))
        computed_alpha = np.where(dist < self.BACKDROP_DISTANCE_THRESHOLD, 0, 255).astype(np.uint8)

        # Never make an already-transparent pixel opaque -- only ever cut
        # further into the existing alpha channel.
        existing_alpha = arr[..., 3]
        combined_alpha = np.minimum(computed_alpha, existing_alpha)

        out = np.dstack([arr[..., :3], combined_alpha]).astype(np.uint8)
        return Image.fromarray(out, mode="RGBA")


class BiRefNetMattingBackend(MattingBackend):
    """Real subject-segmentation backend (BiRefNet, run via
    onnxruntime-gpu). DEPLOY CONCERN: the ONNX model weights and GPU
    runtime are baked into the worker Docker image at deploy time (see
    Dockerfile.worker), not downloaded or executed here. This class is the
    intentional seam for that wiring -- it raises rather than silently
    falling back, so it can never be accidentally selected before it's
    actually implemented at the deploy layer.
    """

    def matte(self, image: Image.Image) -> Image.Image:
        raise NotImplementedError(
            "BiRefNetMattingBackend is a deploy-time concern: install "
            "onnxruntime-gpu and the baked-in BiRefNet model weights in "
            "the worker Docker image (see Dockerfile.worker), then "
            "implement matte() to run inference. Not available in "
            "application code or tests by design."
        )


_BACKENDS: dict[str, type[MattingBackend]] = {
    "stub": StubMattingBackend,
    "birefnet": BiRefNetMattingBackend,
}


def get_matting_backend(name: str | None = None) -> MattingBackend:
    """Factory for the configured matting backend. Falls back to the stub
    for an unset or unrecognized name -- the pipeline should never hard-fail
    a whole ingest run because of a matting backend misconfiguration."""
    if name is None:
        from ..config import get_settings

        name = getattr(get_settings(), "matting_backend", "stub")
    backend_cls = _BACKENDS.get((name or "stub").lower(), StubMattingBackend)
    return backend_cls()


def apply_matting(data: bytes, *, backend: MattingBackend | None = None) -> tuple[bytes, str]:
    """Run the matting pipeline on raw source image bytes.

    Detects the real source mime (never trusts a caller assumption), opens
    it, converts to RGBA (never RGB -- see module docstring), runs the
    backend, and re-encodes as PNG (the alpha-capable, lossless format the
    rest of the pipeline expects). Returns ``(rgba_png_bytes, mime)``.
    """
    detect_mime(data)  # sniff the real source mime; never assume one
    img = Image.open(io.BytesIO(data))
    rgba = img.convert("RGBA")

    active_backend = backend or get_matting_backend()
    matted = active_backend.matte(rgba)
    if matted.mode != "RGBA":
        matted = matted.convert("RGBA")

    out = io.BytesIO()
    matted.save(out, format="PNG")
    return out.getvalue(), "image/png"
