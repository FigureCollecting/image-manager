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
from .serve_routes import _caller_owns_image

router = APIRouter(prefix="/external", tags=["external"])


@router.post("/refs", response_model=CreateExternalRefResponse)
def create_external_ref(
    payload: CreateExternalRefRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> CreateExternalRefResponse:
    # The caller must own the ref's image (service tokens bypass). Without this
    # an attacker could register a ref against a victim's image -- and also
    # squat the unique (ref_type, ref_id) namespace. 404 so an unowned image
    # is indistinguishable from a nonexistent one.
    if not _caller_owns_image(db, ctx, payload.image_id):
        raise HTTPException(status_code=404, detail="not found")
    if payload.version_id is not None:
        # A pinned version must belong to the ref's image; a decoupled
        # (image_id A, version_id V-of-B) ref is the read-side IDOR vector.
        v = db.get(ImageVersion, payload.version_id)
        if not v or v.image_id != payload.image_id:
            raise HTTPException(status_code=404, detail="not found")
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
    caller_owns = _caller_owns_image(db, ctx, er.image_id)
    if not ctx.is_service:
        if er.tenant_id is not None:
            if er.tenant_id != ctx.tenant_id:
                raise HTTPException(status_code=404, detail="not found")
        elif not caller_owns:
            # A null-tenant ref is NOT world-visible: the caller must own the
            # ref's image (fail closed, indistinguishable from nonexistent).
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

    # The resolved version MUST belong to the ref's image; caller_owns and the
    # tenant gate above are computed from er.image_id, so a stored ref whose
    # version_id points at a different image would leak that other image's
    # bytes. Mirror serve_routes' image/version binding check. 404, no oracle.
    if v.image_id != er.image_id:
        raise HTTPException(status_code=404, detail="not found")

    # Never presign a non-public version the caller cannot view; 404 so a
    # denied version is indistinguishable from a nonexistent one.
    # TODO(C1): owner_tenant_id hardcoded None makes tenant visibility
    # unreachable here; needs a real owner-tenant lookup (grant model).
    if not can_view_version(ctx, v.visibility, None, v.age_rating, caller_owns=caller_owns):
        raise HTTPException(status_code=404, detail="not found")

    url = presign_get(v.storage_key)
    return ExternalAssetResponse(
        image_id=img.id,
        version_id=v.id,
        mime=v.mime,
        width=v.width,
        height=v.height,
        url=url,
    )
