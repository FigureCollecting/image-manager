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


def _apply_transforms(data: bytes, spec: dict) -> tuple[bytes, str, int, int]:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    # resize
    resize = spec.get("resize")
    if resize:
        width = int(resize.get("width") or 0)
        height = int(resize.get("height") or 0)
        if width and height:
            img = img.resize((width, height))
        elif width:
            h = int(img.height * (width / img.width))
            img = img.resize((width, h))
        elif height:
            w = int(img.width * (height / img.height))
            img = img.resize((w, height))
    # crop
    crop = spec.get("crop")
    if crop:
        x, y, w, h = int(crop.get("x", 0)), int(crop.get("y", 0)), int(crop.get("width", img.width)), int(crop.get("height", img.height))
        img = img.crop((x, y, x + w, y + h))
    # blur (simple)
    blur = spec.get("blur")
    if blur:
        try:
            from PIL import ImageFilter

            sigma = float(blur.get("sigma", 2.0))
            img = img.filter(ImageFilter.GaussianBlur(radius=sigma))
        except Exception:
            pass
    fmt = (spec.get("format") or "JPEG").upper()
    quality = int(spec.get("quality") or 85)
    out = io.BytesIO()
    save_kwargs = {"quality": quality}
    if fmt == "WEBP":
        mime = "image/webp"
        img.save(out, format="WEBP", quality=quality)
    elif fmt in ("JPG", "JPEG"):
        mime = "image/jpeg"
        img.save(out, format="JPEG", quality=quality)
    elif fmt == "PNG":
        mime = "image/png"
        img.save(out, format="PNG")
    else:
        mime = "image/jpeg"
        img.save(out, format="JPEG", **save_kwargs)
    data_out = out.getvalue()
    return data_out, mime, img.width, img.height


@celery_app.task(name="create_transformed_version")
def create_transformed_version(
    *,
    image_id: int,
    base_version_id: int,
    version_id: int,
    transform_spec: dict,
    dest_key: str,
    visibility: str,
    age_rating: int,
    alt_for_version_id: int | None,
) -> None:
    db: Session = SessionLocal()
    try:
        vbase = db.get(ImageVersion, base_version_id)
        if not vbase:
            return
        s3 = get_s3()
        obj = s3.get_object(Bucket=s3.meta.config._kwargs.get("bucket", None) or None, Key=vbase.storage_key)  # type: ignore[attr-defined]
        # Above may not fetch bucket; prefer reading via configured bucket
        from ..config import get_settings

        settings = get_settings()
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=vbase.storage_key)
        data: bytes = obj["Body"].read()
        data_out, mime_out, w, h = _apply_transforms(data, transform_spec)
        s3.put_object(Bucket=settings.s3_bucket, Key=dest_key, Body=data_out, ContentType=mime_out, ACL="private")

        v = db.get(ImageVersion, version_id)
        if not v:
            return
        v.mime = mime_out
        v.width = w
        v.height = h
        v.bytes = len(data_out)
        v.storage_key = dest_key
        v.visibility = visibility
        v.age_rating = age_rating
        v.alt_for_version_id = alt_for_version_id
        v.transform_spec = transform_spec
        db.commit()
    finally:
        db.close()


def enqueue_transform(
    *,
    image_id: int,
    base_version_id: int,
    version_id: int,
    transform_spec: dict,
    dest_key: str,
    visibility: str,
    age_rating: int,
    alt_for_version_id: int | None,
) -> None:
    create_transformed_version.delay(
        image_id=image_id,
        base_version_id=base_version_id,
        version_id=version_id,
        transform_spec=transform_spec,
        dest_key=dest_key,
        visibility=visibility,
        age_rating=age_rating,
        alt_for_version_id=alt_for_version_id,
    )
