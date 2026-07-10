from __future__ import annotations

import hashlib
import io
from typing import BinaryIO, Tuple

from PIL import Image
from imagehash import phash


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def compute_phash(data: bytes) -> str:
    img = Image.open(io.BytesIO(data))
    hash_val = phash(img)
    return str(hash_val)


def get_image_dimensions(data: bytes) -> Tuple[int, int]:
    img = Image.open(io.BytesIO(data))
    return img.width, img.height


_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "TIFF": "image/tiff",
}


def detect_mime(data: bytes) -> str:
    """Sniff the real mime type from image bytes (Pillow's magic-byte
    detection via Image.format), rather than trusting a caller-supplied or
    assumed content type. Callers that hardcode a mime (e.g. always
    "image/jpeg") silently mislabel non-JPEG sources -- wrong storage-key
    extension, and in the matting pipeline, a wrong assumption about
    whether the source already carries an alpha channel."""
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "").upper()
    return _FORMAT_TO_MIME.get(fmt, "application/octet-stream")

