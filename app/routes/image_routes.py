import datetime as dt
import random
import string
import uuid
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import require_auth_ctx
from ..hashing import sha256_stream
from ..models import Image, ImageVersion, UserImageLink
from ..policy import AuthCtx
from ..s3 import get_s3, presign_post_for_upload
from ..schemas import (
    CompleteUploadRequest,
    CompleteUploadResponse,
    CreateVersionRequest,
    CreateVersionResponse,
    ExposeSafeAltRequest,
    ExposeSafeAltResponse,
    ImageDetailResponse,
    ImageVersionResponse,
    InitiateUploadRequest,
    OkResponse,
    SetVisibilityRequest,
    VersionSummary,
)
from ..workers.tasks import enqueue_transform

router = APIRouter(prefix="/images", tags=["images"])


def _check_image_ownership(db: Session, image_id: int, ctx: AuthCtx) -> None:
    """Verify that a non-service caller has a UserImageLink for this image."""
    if ctx.is_service:
        return
    link = db.execute(
        select(UserImageLink).where(
            UserImageLink.image_id == image_id,
            UserImageLink.user_id == ctx.subject,
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="not found")


@router.post("/initiate-upload")
def initiate_upload(
    payload: InitiateUploadRequest,
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> dict[str, Any]:
    settings = get_settings()
    staging_key = f"uploads/{uuid.uuid4()}/{payload.filename}"
    presigned = presign_post_for_upload(staging_key, content_type=payload.mime, size=payload.size)
    presigned["staging_key"] = staging_key
    presigned["bucket"] = settings.s3_bucket
    return presigned


def _prove_staging_possession(key: str, expected_sha256: str) -> None:
    """SYNCHRONOUSLY prove the caller possesses bytes hashing to the sha256
    they claim, by fetching and hashing THEIR staging object before any DB
    write. Without this, complete_upload is a forgeable ownership primitive:
    any image's sha256 leaks as its ETag on /serve and /public, so an
    attacker could replay a victim's hash with an arbitrary staging key and
    mint themselves an owner UserImageLink on the victim's image. The async
    verify task only LOGS on mismatch -- it never revokes the link -- so the
    gate must happen here, before the link (and the Image row) exist.
    The object is hashed in streamed chunks; nothing is buffered whole."""
    settings = get_settings()
    s3 = get_s3()
    try:
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError as exc:
        # No (readable) staging object at the caller's key: nothing to prove
        # possession of. Fail closed -- register no image, mint no link.
        raise HTTPException(status_code=404, detail="staging object not found") from exc
    if sha256_stream(obj["Body"]) != expected_sha256:
        raise HTTPException(status_code=400, detail="sha256 mismatch")


@router.post("/complete", response_model=CompleteUploadResponse)
def complete_upload(
    payload: CompleteUploadRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> CompleteUploadResponse:
    settings = get_settings()

    # Proof of possession gates EVERYTHING below -- including the dedup path:
    # when the Image row already exists, a co-owner link is minted, so the
    # caller must still prove they hold matching bytes themselves.
    _prove_staging_possession(payload.key, payload.sha256)

    # Idempotent by sha256
    img = db.execute(select(Image).where(Image.sha256 == payload.sha256)).scalar_one_or_none()
    created = False
    if not img:
        img = Image(
            sha256=payload.sha256, bytes=payload.size, mime=payload.mime, storage_key=payload.key
        )
        db.add(img)
        db.flush()
        created = True

    # Ensure an ownership link for any authenticated user token. Service
    # tokens are trusted globally and own nothing, so they need no link. The
    # old `"-" in subject` heuristic locked hyphen-less subjects out of every
    # ownership-gated path.
    if not ctx.is_service and ctx.subject:
        link = db.get(UserImageLink, {"user_id": ctx.subject, "image_id": img.id})
        if not link:
            link = UserImageLink(
                user_id=ctx.subject,
                tenant_id=ctx.tenant_id,
                image_id=img.id,
                current_version_id=None,
                role="owner",
            )
            db.add(link)

    db.commit()

    # Defer to worker to move the object to its final key and create v1. Its
    # sha check is now a harmless double-check behind the synchronous
    # possession proof above -- the link is never gated on the worker.
    try:
        from ..workers.tasks import enqueue_verify

        enqueue_verify(
            image_id=img.id,
            bucket=settings.s3_bucket,
            key=payload.key,
            expected_sha256=payload.sha256,
        )
    except Exception:
        pass

    return CompleteUploadResponse(image_id=img.id, created=created)


@router.get("/{image_id}", response_model=ImageDetailResponse)
def get_image(
    image_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> ImageDetailResponse:
    img = db.get(Image, image_id)
    if not img or img.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not found")
    _check_image_ownership(db, image_id, ctx)
    versions = (
        db.execute(
            select(ImageVersion)
            .where(ImageVersion.image_id == img.id, ImageVersion.deleted_at.is_(None))
            .order_by(ImageVersion.version_no)
        )
        .scalars()
        .all()
    )
    return ImageDetailResponse(
        id=img.id,
        sha256=img.sha256,
        mime=img.mime,
        bytes=img.bytes,
        width=img.width,
        height=img.height,
        versions=[
            VersionSummary(
                id=v.id,
                version_no=v.version_no,
                visibility=v.visibility,
                age_rating=v.age_rating,
                alt_for_version_id=v.alt_for_version_id,
                mime=v.mime,
                width=v.width,
                height=v.height,
                bytes=v.bytes,
            )
            for v in versions
        ],
    )


@router.get("/{image_id}/versions/{version_id}", response_model=ImageVersionResponse)
def get_image_version(
    image_id: int,
    version_id: int,
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> ImageVersionResponse:
    return ImageVersionResponse(image_id=image_id, version_id=version_id)


def _short_id(n: int = 6) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


@router.post("/{image_id}/versions", response_model=CreateVersionResponse)
def create_version(
    image_id: int,
    payload: CreateVersionRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> CreateVersionResponse:
    _check_image_ownership(db, image_id, ctx)
    base: ImageVersion | None = None
    if payload.base_version_id:
        base = db.get(ImageVersion, payload.base_version_id)
        # An explicitly-requested base must exist AND belong to an image the
        # caller owns; 404 either way so existing-but-unowned version ids are
        # indistinguishable from nonexistent ones. Without the ownership
        # check a caller could launder another owner's bytes through
        # base_version_id into an image they control.
        if not base:
            raise HTTPException(status_code=404, detail="not found")
        _check_image_ownership(db, base.image_id, ctx)
    if payload.create_safe_alt_for is not None:
        # The safe alt becomes alt_for_version_id, which safe-mode /serve will
        # swap in. It must reference a version of an image the caller owns,
        # else an attacker could point their version's alt at a victim's
        # private version and stream the victim's bytes. 404 either way so
        # existing-but-unowned ids stay indistinguishable from nonexistent.
        alt = db.get(ImageVersion, payload.create_safe_alt_for)
        if not alt:
            raise HTTPException(status_code=404, detail="not found")
        _check_image_ownership(db, alt.image_id, ctx)
    if not base:
        base = (
            db.execute(
                select(ImageVersion)
                .where(ImageVersion.image_id == image_id)
                .order_by(ImageVersion.version_no)
            )
            .scalars()
            .first()
        )
    if not base:
        raise HTTPException(status_code=400, detail="base version not found")

    max_no = (
        db.execute(
            select(func.max(ImageVersion.version_no)).where(ImageVersion.image_id == image_id)
        ).scalar()
        or 1
    )
    new_no = int(max_no) + 1
    fmt = (payload.transform_spec.get("format") or (base.mime or "image/jpeg")).lower()
    ext = (
        "jpg"
        if "jpeg" in fmt or "jpg" in fmt
        else ("png" if "png" in fmt else ("webp" if "webp" in fmt else "jpg"))
    )
    dest_key = f"versions/{image_id}/v{new_no}-{_short_id()}.{ext}"

    version = ImageVersion(
        image_id=image_id,
        version_no=new_no,
        derived_from_version=base.version_no,
        transform_spec=payload.transform_spec,
        mime=None,
        width=None,
        height=None,
        bytes=None,
        storage_key=dest_key,
        visibility=payload.visibility,
        age_rating=payload.age_rating,
        alt_for_version_id=payload.create_safe_alt_for,
    )
    db.add(version)
    db.flush()

    enqueue_transform(
        image_id=image_id,
        base_version_id=base.id,
        version_id=version.id,
        transform_spec=payload.transform_spec,
        dest_key=dest_key,
        visibility=payload.visibility,
        age_rating=payload.age_rating,
        alt_for_version_id=payload.create_safe_alt_for,
    )
    db.commit()
    return CreateVersionResponse(version_id=version.id, version_no=new_no, storage_key=dest_key)


@router.post("/{image_id}/versions/{version_id}/visibility", response_model=OkResponse)
def set_visibility(
    image_id: int,
    version_id: int,
    payload: SetVisibilityRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    _check_image_ownership(db, image_id, ctx)
    v = db.get(ImageVersion, version_id)
    if not v or v.image_id != image_id:
        raise HTTPException(status_code=404, detail="not found")
    v.visibility = payload.visibility
    db.commit()
    return OkResponse(ok=True)


@router.post(
    "/{image_id}/versions/{version_id}/expose-safe-alt", response_model=ExposeSafeAltResponse
)
def expose_safe_alt(
    image_id: int,
    version_id: int,
    payload: ExposeSafeAltRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> ExposeSafeAltResponse:
    _check_image_ownership(db, image_id, ctx)
    v = db.get(ImageVersion, version_id)
    if not v or v.image_id != image_id:
        raise HTTPException(status_code=404, detail="not found")
    blur_spec = payload.blur_spec or {"sigma": 12}
    transform_spec = {"blur": blur_spec, "format": v.mime or "jpeg"}
    max_no = (
        db.execute(
            select(func.max(ImageVersion.version_no)).where(ImageVersion.image_id == image_id)
        ).scalar()
        or v.version_no
    )
    new_no = int(max_no) + 1
    dest_key = f"versions/{image_id}/v{new_no}-{_short_id()}.jpg"
    safe_v = ImageVersion(
        image_id=image_id,
        version_no=new_no,
        derived_from_version=v.version_no,
        transform_spec=transform_spec,
        storage_key=dest_key,
        visibility=v.visibility,
        age_rating=payload.age_rating,
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
        age_rating=payload.age_rating,
        alt_for_version_id=v.id,
    )
    db.commit()
    return ExposeSafeAltResponse(version_id=safe_v.id, alt_for=v.id)


@router.delete("/{image_id}", response_model=OkResponse)
def delete_image(
    image_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    img = db.get(Image, image_id)
    if not img or img.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not found")
    _check_image_ownership(db, image_id, ctx)
    img.deleted_at = dt.datetime.now(dt.UTC)
    db.commit()
    return OkResponse(ok=True)


@router.delete("/{image_id}/versions/{version_id}", response_model=OkResponse)
def delete_version(
    image_id: int,
    version_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    _check_image_ownership(db, image_id, ctx)
    v = db.get(ImageVersion, version_id)
    if not v or v.image_id != image_id or v.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not found")
    v.deleted_at = dt.datetime.now(dt.UTC)
    db.commit()
    return OkResponse(ok=True)
