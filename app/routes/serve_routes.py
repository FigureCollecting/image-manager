from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_auth_ctx
from ..models import Image, ImageVersion
from ..policy import AuthCtx, can_view_version
from ..s3 import get_s3

router = APIRouter(tags=["serve"])


def _stream_version(version: ImageVersion, *, cache_control: str, etag: str) -> Response:
    """Byte-stream a version's bytes through the app rather than 302
    redirecting to a presigned S3/MinIO URL. A presigned URL for the
    internal minio:9000 host is not resolvable outside the Docker network
    -- unreachable from a real browser. Streaming through the app trades a
    (small, in-cluster) extra hop for actually working client-side.
    """
    settings = get_settings()
    s3 = get_s3()
    obj = s3.get_object(Bucket=settings.s3_bucket, Key=version.storage_key)
    data: bytes = obj["Body"].read()
    resp = Response(content=data, media_type=version.mime or "application/octet-stream")
    resp.headers["Cache-Control"] = cache_control
    resp.headers["ETag"] = etag
    return resp


@router.get("/serve/{image_id}@{version_id}")
def serve_version(
    image_id: int,
    version_id: int,
    mode: str | None = None,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx | None = Depends(get_auth_ctx),  # noqa: B008
) -> Response:
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

    return _stream_version(target, cache_control="private, max-age=600", etag=img.sha256)


@router.get("/public/{image_id}@{version_id}")
def public_serve(image_id: int, version_id: int, db: Session = Depends(get_db)) -> Response:  # noqa: B008
    img = db.get(Image, image_id)
    v = db.get(ImageVersion, version_id)
    if not img or not v or v.image_id != img.id:
        raise HTTPException(status_code=404, detail="not found")
    if v.visibility != "public":
        raise HTTPException(status_code=403, detail="forbidden")
    # (image_id, version_id) is a stable, content-addressed identifier --
    # a version's bytes never change after creation -- so this is safe to
    # cache aggressively at any layer (browser, CDN).
    return _stream_version(v, cache_control="public, max-age=31536000, immutable", etag=img.sha256)
