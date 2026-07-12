"""Per-family watermark overlay and EXIF stripping for matted derivatives.

"Family" is the image's originating source (FigureGallery.source, e.g.
"mfc") -- different sources get different watermark presentation. Both
steps run on an already-RGBA matted image, after matting and before the
final PNG encode in the ingest pipeline (app.workers.tasks).
"""

from __future__ import annotations

from typing import TypedDict

from PIL import Image, ImageDraw, ImageFont, ImageOps


class WatermarkConfig(TypedDict):
    text: str
    opacity: int  # 0-255


#: Fallback family used for any source with no dedicated config.
DEFAULT_FAMILY = "default"

WATERMARK_CONFIG: dict[str, WatermarkConfig] = {
    "mfc": {"text": "figurecollecting.com", "opacity": 110},
    DEFAULT_FAMILY: {"text": "figurecollecting.com", "opacity": 110},
}

#: Margin (px) from the bottom-right corner where the watermark text is drawn.
_MARGIN = 8


def apply_watermark(image: Image.Image, *, family: str) -> Image.Image:
    """Overlay a small semi-transparent text watermark in the bottom-right
    corner, sized per the family's config (falls back to DEFAULT_FAMILY for
    an unrecognized source rather than raising -- a new/unconfigured
    gallery source must never break ingestion).

    Returns a new RGBA image; the input is not mutated.
    """
    cfg = WATERMARK_CONFIG.get(family, WATERMARK_CONFIG[DEFAULT_FAMILY])
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_size = max(10, base.width // 20)
    font = ImageFont.load_default(size=font_size)
    text = cfg["text"]
    opacity = cfg["opacity"]

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, base.width - text_w - _MARGIN)
    y = max(0, base.height - text_h - _MARGIN)
    draw.text((x, y), text, font=font, fill=(255, 255, 255, opacity))

    return Image.alpha_composite(base, overlay)


def strip_exif(image: Image.Image) -> Image.Image:
    """Bake in EXIF orientation (so removing the raw EXIF doesn't silently
    un-rotate the image for viewers that don't re-read it), then drop the
    EXIF blob itself -- which is where identifying metadata (GPS, camera
    make/model) lives -- before the derivative is stored.
    """
    transposed = ImageOps.exif_transpose(image) or image.copy()
    # exif_transpose already drops the 'exif' key when it performs a
    # rotation, but for the (common) no-rotation-needed case it can return
    # the same object with .info still populated -- always scrub explicitly.
    cleaned = transposed.copy()
    cleaned.info.pop("exif", None)
    return cleaned
