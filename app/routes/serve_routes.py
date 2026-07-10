from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_auth_ctx
from ..models import Image, ImageVersion
from ..policy import AuthCtx, can_view_version
from ..s3 import presign_get

router = APIRouter(tags=["serve"])


@router.get("/serve/{image_id}@{version_id}")
def serve_version(image_id: int, version_id: int, mode: str | None = None, db: Session = Depends(get_db), ctx: AuthCtx | None = Depends(get_auth_ctx)) -> Response:
    img = db.get(Image, image_id)
    v = db.get(ImageVersion, version_id)
    if not img or not v or v.image_id != img.id:
        raise HTTPException(status_code=404, detail="not found")

    # policy: private requires caller to have a link; tenant/public/catalog handled via can_view_version
    if not can_view_version(ctx, v.visibility, None, v.age_rating):
        raise HTTPException(status_code=403, detail="forbidden")

    # age gating
    target = v
    safe_mode = (mode == "safe") or (ctx.safe_mode if ctx else False)
    if safe_mode and v.age_rating and v.age_rating > 0:
        if v.alt_for_version_id:
            alt = db.get(ImageVersion, v.alt_for_version_id)
            if alt:
                target = alt
        else:
            raise HTTPException(status_code=403, detail="age-gated")

    url = presign_get(target.storage_key)
    resp = Response(status_code=302)
    resp.headers["Location"] = url
    resp.headers["Cache-Control"] = "private, max-age=600"
    resp.headers["ETag"] = img.sha256
    return resp


@router.get("/public/{image_id}@{version_id}")
def public_serve(image_id: int, version_id: int, db: Session = Depends(get_db)) -> Response:
    img = db.get(Image, image_id)
    v = db.get(ImageVersion, version_id)
    if not img or not v or v.image_id != img.id:
        raise HTTPException(status_code=404, detail="not found")
    if v.visibility != "public":
        raise HTTPException(status_code=403, detail="forbidden")
    url = presign_get(v.storage_key)
    resp = Response(status_code=302)
    resp.headers["Location"] = url
    resp.headers["Cache-Control"] = "public, max-age=600"
    resp.headers["ETag"] = img.sha256
    return resp

