from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_auth_ctx
from ..models import ExternalRef, Image, ImageVersion
from ..policy import AuthCtx, can_view_version
from ..s3 import presign_get
from ..schemas import (
    CreateExternalRefRequest,
    CreateExternalRefResponse,
    ExternalAssetResponse,
)

router = APIRouter(prefix="/external", tags=["external"])


@router.post("/refs", response_model=CreateExternalRefResponse)
def create_external_ref(
    payload: CreateExternalRefRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> CreateExternalRefResponse:
    er = ExternalRef(
        ref_type=payload.ref_type,
        ref_id=payload.ref_id,
        image_id=payload.image_id,
        version_id=payload.version_id,
        tenant_id=ctx.tenant_id,
    )
    db.add(er)
    db.commit()
    return CreateExternalRefResponse(id=er.id)


@router.get("/assets/by-external-ref", response_model=ExternalAssetResponse)
def by_external_ref(
    ref_type: str,
    ref_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> ExternalAssetResponse:
    er = db.execute(
        select(ExternalRef).where(ExternalRef.ref_type == ref_type, ExternalRef.ref_id == ref_id)
    ).scalar_one_or_none()
    if not er:
        raise HTTPException(status_code=404, detail="not found")
    img = db.get(Image, er.image_id)
    v = (
        db.get(ImageVersion, er.version_id)
        if er.version_id
        else db.execute(
            select(ImageVersion)
            .where(ImageVersion.image_id == er.image_id)
            .order_by(ImageVersion.version_no)
        )
        .scalars()
        .first()
    )
    if not img or not v:
        raise HTTPException(status_code=404, detail="not found")

    if not can_view_version(ctx, v.visibility, None, v.age_rating):
        raise HTTPException(status_code=403, detail="forbidden")

    url = presign_get(v.storage_key)
    return ExternalAssetResponse(
        image_id=img.id,
        version_id=v.id,
        mime=v.mime,
        width=v.width,
        height=v.height,
        url=url,
    )
