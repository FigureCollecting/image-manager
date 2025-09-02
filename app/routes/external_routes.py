from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_auth_ctx
from ..models import ExternalRef, Image, ImageVersion
from ..policy import AuthCtx, can_view_version
from ..s3 import presign_get

router = APIRouter(prefix="/external", tags=["external"])


@router.post("/refs")
def create_external_ref(payload: dict, db: Session = Depends(get_db)) -> dict:
    ref_type = payload.get("ref_type")
    ref_id = payload.get("ref_id")
    image_id = payload.get("image_id")
    version_id = payload.get("version_id")
    if not ref_type or not ref_id or not image_id:
        raise HTTPException(status_code=400, detail="ref_type, ref_id, image_id required")
    er = ExternalRef(ref_type=ref_type, ref_id=ref_id, image_id=int(image_id), version_id=int(version_id) if version_id else None)
    db.add(er)
    db.commit()
    return {"id": er.id}


@router.get("/assets/by-external-ref")
def by_external_ref(ref_type: str, ref_id: str, db: Session = Depends(get_db), ctx: AuthCtx | None = Depends(get_auth_ctx)) -> dict:
    er = db.execute(select(ExternalRef).where(ExternalRef.ref_type == ref_type, ExternalRef.ref_id == ref_id)).scalar_one_or_none()
    if not er:
        raise HTTPException(status_code=404, detail="not found")
    img = db.get(Image, er.image_id)
    v = db.get(ImageVersion, er.version_id) if er.version_id else db.execute(select(ImageVersion).where(ImageVersion.image_id == er.image_id).order_by(ImageVersion.version_no)).scalars().first()
    if not img or not v:
        raise HTTPException(status_code=404, detail="not found")

    if v.visibility == "public":
        url = presign_get(v.storage_key)
    else:
        if not can_view_version(ctx, v.visibility, None, v.age_rating):
            raise HTTPException(status_code=403, detail="forbidden")
        url = presign_get(v.storage_key)

    return {
        "image_id": img.id,
        "version_id": v.id,
        "mime": v.mime,
        "width": v.width,
        "height": v.height,
        "url": url,
    }

