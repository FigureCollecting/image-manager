from __future__ import annotations

import io
import logging
import mimetypes
from typing import Optional

import httpx
from PIL import Image
from sqlalchemy import select

from ..db import worker_session
from ..hashing import compute_phash, get_image_dimensions, sha256_bytes
from ..models import AlbumItem, FigureGallery, Image as ImageModel
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

    with worker_session() as db:
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
    with worker_session() as db:
        vbase = db.get(ImageVersion, base_version_id)
        if not vbase:
            return
        s3 = get_s3()
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


@celery_app.task(name="generate_album_cover")
def generate_album_cover(album_id: int) -> str:
    from ..config import get_settings

    settings = get_settings()
    s3 = get_s3()
    # pick first 4 items
    with worker_session() as db:
        items = db.query(AlbumItem).filter(AlbumItem.album_id == album_id).order_by(AlbumItem.position).limit(4).all()
        keys: list[str] = []
        for it in items:
            v = db.query(ImageVersion).filter(ImageVersion.image_id == it.image_id).order_by(ImageVersion.version_no).first()
            if v and v.storage_key:
                keys.append(v.storage_key)
        # create mosaic
        tiles: list[Image.Image] = []
        for k in keys:
            obj = s3.get_object(Bucket=settings.s3_bucket, Key=k)
            data: bytes = obj["Body"].read()
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((256, 256))
            tiles.append(im)
        if not tiles:
            # default blank
            canvas = Image.new("RGB", (512, 512), color=(240, 240, 240))
        else:
            canvas = Image.new("RGB", (512, 512))
            positions = [(0, 0), (256, 0), (0, 256), (256, 256)]
            for i, im in enumerate(tiles[:4]):
                canvas.paste(im, positions[i])
        out = io.BytesIO()
        canvas.save(out, format="WEBP", quality=80)
        data_out = out.getvalue()
        import hashlib

        h = hashlib.sha256(data_out).hexdigest()[:8]
        dest_key = f"albums/{album_id}/cover-{h}.webp"
        s3.put_object(Bucket=settings.s3_bucket, Key=dest_key, Body=data_out, ContentType="image/webp", ACL="public-read")
        return dest_key


def enqueue_album_cover(album_id: int) -> str:
    # Returns expected key; generation happens async
    key = f"albums/{album_id}/cover-pending.webp"
    generate_album_cover.delay(album_id)
    return key


@celery_app.task(name="ingest_gallery_images")
def ingest_gallery_images(*, figure_id: str, images: list[dict]) -> None:
    """Download gallery images, deduplicate by SHA256, create FigureGallery records."""
    from ..config import get_settings

    settings = get_settings()
    s3 = get_s3()

    with worker_session() as db:
        for item in images:
            url = item["url"]
            position = item.get("position", 0)
            caption = item.get("caption")

            # Download image
            try:
                resp = httpx.get(url, timeout=30)
                resp.raise_for_status()
            except Exception:
                logger.warning("gallery_download_failed", extra={"url": url, "figure_id": figure_id})
                continue

            data = resp.content
            sha = sha256_bytes(data)

            # Content-addressable dedup: check if Image with this hash exists
            existing_img = db.execute(
                select(ImageModel).where(ImageModel.sha256 == sha)
            ).scalar_one_or_none()

            if existing_img:
                image_id = existing_img.id
            else:
                # Upload to S3 and create Image record
                mime = "image/jpeg"
                ext = _ext_for_mime(mime)
                storage_key = _final_key(sha, ext)

                s3.put_object(
                    Bucket=settings.s3_bucket,
                    Key=storage_key,
                    Body=data,
                    ContentType=mime,
                    ACL="private",
                )

                width, height = get_image_dimensions(data)
                p_hash = compute_phash(data)

                new_img = ImageModel(
                    sha256=sha,
                    phash=p_hash,
                    mime=mime,
                    width=width,
                    height=height,
                    bytes=len(data),
                    storage_key=storage_key,
                )
                db.add(new_img)
                db.flush()
                image_id = new_img.id

            # Create FigureGallery record
            gallery_entry = FigureGallery(
                figure_id=figure_id,
                source_url=url,
                image_id=image_id,
                position=position,
                caption=caption,
                source="mfc",
            )
            db.add(gallery_entry)

        db.flush()
