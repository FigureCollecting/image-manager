from __future__ import annotations

import uuid
import random
import string
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_auth_ctx
from ..models import Image, ImageVersion, UserImageLink
from ..policy import AuthCtx
from ..s3 import presign_post_for_upload
from ..workers.tasks import enqueue_transform

router = APIRouter(prefix="/images", tags=["images"])


@router.post("/initiate-upload")
def initiate_upload(payload: Dict[str, Any], settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    filename = payload.get("filename")
    mime = payload.get("mime")
    size = int(payload.get("size") or 0)
    if not filename or not mime or not size:
        raise HTTPException(status_code=400, detail="filename, mime, size are required")
    staging_key = f"uploads/{uuid.uuid4()}/{filename}"
    presigned = presign_post_for_upload(staging_key, content_type=mime, size=size, settings=settings)
    presigned["staging_key"] = staging_key
    presigned["bucket"] = settings.s3_bucket
    return presigned


@router.post("/complete")
def complete_upload(payload: Dict[str, Any], db: Session = Depends(get_db), settings: Settings = Depends(get_settings), ctx: Optional[AuthCtx] = Depends(get_auth_ctx)) -> Dict[str, Any]:
    sha256 = payload.get("sha256")
    size = int(payload.get("size") or 0)
    mime = payload.get("mime")
    key = payload.get("key")
    if not sha256 or not key or not mime or not size:
        raise HTTPException(status_code=400, detail="sha256, key, mime, size required")

    # Idempotent by sha256
    img = db.execute(select(Image).where(Image.sha256 == sha256)).scalar_one_or_none()
    created = False
    if not img:
        img = Image(sha256=sha256, bytes=size, mime=mime, storage_key=key)
        db.add(img)
        db.flush()
        created = True

    # Ensure link for user tokens
    if ctx and not ctx.is_service and ctx.subject and "-" in ctx.subject:
        link = db.get(UserImageLink, {"user_id": ctx.subject, "image_id": img.id})
        if not link:
            link = UserImageLink(user_id=ctx.subject, tenant_id=ctx.tenant_id, image_id=img.id, current_version_id=None, role="owner")
            db.add(link)

    db.commit()

    # Defer to worker to verify/move and create v1
    try:
        from ..workers.tasks import enqueue_verify

        enqueue_verify(image_id=img.id, bucket=settings.s3_bucket, key=key, expected_sha256=sha256)
    except Exception:
        # Worker not ready in this step; ignore
        pass

    return {"image_id": img.id, "created": created}


@router.get("/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db), ctx: Optional[AuthCtx] = Depends(get_auth_ctx)) -> Dict[str, Any]:
    img = db.get(Image, image_id)
    if not img:
        raise HTTPException(status_code=404, detail="not found")
    # include versions summary
    versions = db.execute(select(ImageVersion).where(ImageVersion.image_id == img.id).order_by(ImageVersion.version_no)).scalars().all()
    return {
        "id": img.id,
        "sha256": img.sha256,
        "mime": img.mime,
        "bytes": img.bytes,
        "width": img.width,
        "height": img.height,
        "versions": [
            {
                "id": v.id,
                "version_no": v.version_no,
                "visibility": v.visibility,
                "age_rating": v.age_rating,
                "alt_for_version_id": v.alt_for_version_id,
                "mime": v.mime,
                "width": v.width,
                "height": v.height,
                "bytes": v.bytes,
            }
            for v in versions
        ],
    }


@router.get("/{image_id}/versions/{version_id}")
def get_image_version(image_id: int, version_id: int) -> Dict[str, Any]:
    return {"image_id": image_id, "version_id": version_id}


def _short_id(n: int = 6) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


@router.post("/{image_id}/versions")
def create_version(
    image_id: int,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    transform_spec: Dict[str, Any] = payload.get("transform_spec") or {}
    base_version_id = payload.get("base_version_id")
    visibility = payload.get("visibility") or "private"
    age_rating = int(payload.get("age_rating") or 0)
    create_safe_alt_for = payload.get("create_safe_alt_for")

    base: Optional[ImageVersion] = None
    if base_version_id:
        base = db.get(ImageVersion, int(base_version_id))
    if not base:
        # fall back to v1
        base = db.execute(select(ImageVersion).where(ImageVersion.image_id == image_id).order_by(ImageVersion.version_no)).scalars().first()
    if not base:
        raise HTTPException(status_code=400, detail="base version not found")

    max_no = db.execute(select(func.max(ImageVersion.version_no)).where(ImageVersion.image_id == image_id)).scalar() or 1
    new_no = int(max_no) + 1
    fmt = (transform_spec.get("format") or (base.mime or "image/jpeg")).lower()
    ext = "jpg" if "jpeg" in fmt or "jpg" in fmt else ("png" if "png" in fmt else ("webp" if "webp" in fmt else "jpg"))
    dest_key = f"versions/{image_id}/v{new_no}-{_short_id()}.{ext}"

    version = ImageVersion(
        image_id=image_id,
        version_no=new_no,
        derived_from_version=base.version_no,
        transform_spec=transform_spec,
        mime=None,
        width=None,
        height=None,
        bytes=None,
        storage_key=dest_key,
        visibility=visibility,
        age_rating=age_rating,
        alt_for_version_id=create_safe_alt_for,
    )
    db.add(version)
    db.flush()

    enqueue_transform(
        image_id=image_id,
        base_version_id=base.id,
        version_id=version.id,
        transform_spec=transform_spec,
        dest_key=dest_key,
        visibility=visibility,
        age_rating=age_rating,
        alt_for_version_id=create_safe_alt_for,
    )
    db.commit()
    return {"version_id": version.id, "version_no": new_no, "storage_key": dest_key}


@router.post("/{image_id}/versions/{version_id}/visibility")
def set_visibility(image_id: int, version_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    v = db.get(ImageVersion, version_id)
    if not v or v.image_id != image_id:
        raise HTTPException(status_code=404, detail="not found")
    vis = payload.get("visibility")
    if vis not in ("private", "tenant", "public", "catalog"):
        raise HTTPException(status_code=400, detail="invalid visibility")
    v.visibility = vis
    db.commit()
    return {"ok": True}


@router.post("/{image_id}/versions/{version_id}/expose-safe-alt")
def expose_safe_alt(image_id: int, version_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    v = db.get(ImageVersion, version_id)
    if not v or v.image_id != image_id:
        raise HTTPException(status_code=404, detail="not found")
    age_rating = int(payload.get("age_rating") or 0)
    blur_spec = payload.get("blur_spec") or {"sigma": 12}
    # create a blurred alternative derived from this version
    transform_spec = {"blur": blur_spec, "format": v.mime or "jpeg"}
    max_no = db.execute(select(func.max(ImageVersion.version_no)).where(ImageVersion.image_id == image_id)).scalar() or v.version_no
    new_no = int(max_no) + 1
    ext = "jpg"
    dest_key = f"versions/{image_id}/v{new_no}-{_short_id()}.{ext}"
    safe_v = ImageVersion(
        image_id=image_id,
        version_no=new_no,
        derived_from_version=v.version_no,
        transform_spec=transform_spec,
        storage_key=dest_key,
        visibility=v.visibility,
        age_rating=age_rating,
        alt_for_version_id=v.id,
    )
    db.add(safe_v)
    db.flush()
    enqueue_transform(
        image_id=image_id,
        base_version_id=v.id,
        version_id=safe_v.id,
        transform_spec=transform_spec,
        dest_key=dest_key,
        visibility=v.visibility,
        age_rating=age_rating,
        alt_for_version_id=v.id,
    )
    db.commit()
    return {"version_id": safe_v.id, "alt_for": v.id}
