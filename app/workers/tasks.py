from __future__ import annotations

import io
import logging
import mimetypes
from typing import Optional

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..hashing import compute_phash, get_image_dimensions, sha256_bytes
from ..models import Image as ImageModel
from ..models import ImageVersion, UserImageLink
from ..s3 import get_s3, move_object
from . import celery_app

logger = logging.getLogger(__name__)


def _ext_for_mime(mime: str) -> str:
    if mime in ("image/jpeg", "image/jpg"):
        return "jpg"
    if mime == "image/png":
        return "png"
    if mime == "image/webp":
        return "webp"
    if mime == "image/tiff":
        return "tif"
    guess = mimetypes.guess_extension(mime) or ".bin"
    return guess.lstrip(".")


def _final_key(sha256: str, ext: str) -> str:
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}.{ext}"


@celery_app.task(name="verify_and_register_object")
def verify_and_register_object(image_id: int, bucket: str, key: str, expected_sha256: str) -> None:
    s3 = get_s3()
    obj = s3.get_object(Bucket=bucket, Key=key)
    data: bytes = obj["Body"].read()

    actual_sha = sha256_bytes(data)
    if actual_sha != expected_sha256:
        logger.error("sha_mismatch", extra={"image_id": image_id})
        return

    width, height = get_image_dimensions(data)
    p_hash = compute_phash(data)

    mime = obj.get("ContentType") or "image/jpeg"
    ext = _ext_for_mime(mime)
    final_key = _final_key(actual_sha, ext)

    move_object(key, final_key)

    db: Session = SessionLocal()
    try:
        img = db.get(ImageModel, image_id)
        if not img:
            return
        img.storage_key = final_key
        img.width = width
        img.height = height
        img.bytes = len(data)
        img.mime = mime
        img.phash = p_hash
        db.flush()

        # Insert version 1 if not exists
        v1 = db.execute(select(ImageVersion).where(ImageVersion.image_id == img.id, ImageVersion.version_no == 1)).scalar_one_or_none()
        if not v1:
            v1 = ImageVersion(
                image_id=img.id,
                version_no=1,
                transform_spec={},
                mime=mime,
                width=width,
                height=height,
                bytes=len(data),
                storage_key=final_key,
                visibility="private",
                age_rating=0,
            )
            db.add(v1)
            db.flush()

        # Update links to point to v1 if unset
        links = db.execute(select(UserImageLink).where(UserImageLink.image_id == img.id)).scalars().all()
        for link in links:
            if link.current_version_id is None:
                link.current_version_id = v1.id

        db.commit()
    finally:
        db.close()


def enqueue_verify(*, image_id: int, bucket: str, key: str, expected_sha256: str) -> None:
    verify_and_register_object.delay(image_id, bucket, key, expected_sha256)

