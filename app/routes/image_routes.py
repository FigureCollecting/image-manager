from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_auth_ctx
from ..models import Image, UserImageLink
from ..policy import AuthCtx
from ..s3 import presign_post_for_upload

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
    # minimal response; versions added later
    return {
        "id": img.id,
        "sha256": img.sha256,
        "mime": img.mime,
        "bytes": img.bytes,
        "width": img.width,
        "height": img.height,
    }


@router.get("/{image_id}/versions/{version_id}")
def get_image_version(image_id: int, version_id: int) -> Dict[str, Any]:
    # Placeholder; implemented fully in later steps
    return {"image_id": image_id, "version_id": version_id}

