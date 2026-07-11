from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_auth_ctx
from ..models import Album, AlbumTag, ImageTag, Tag
from ..policy import AuthCtx
from ..schemas import (
    CreateTagRequest,
    CreateTagResponse,
    TagItemsRequest,
    TagItemsResponse,
)
from .album_routes import _check_album_access
from .image_routes import _check_image_ownership

router = APIRouter(prefix="/tags", tags=["tags"])


@router.post("", response_model=CreateTagResponse)
def create_tag(
    payload: CreateTagRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> CreateTagResponse:
    t = Tag(name=payload.name, scope=payload.scope, tenant_id=ctx.tenant_id)
    db.add(t)
    db.commit()
    return CreateTagResponse(id=t.id, name=t.name)


@router.post("/images/{image_id}", response_model=TagItemsResponse)
def tag_image(
    image_id: int,
    payload: TagItemsRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> TagItemsResponse:
    # Only the image's owner (UserImageLink) or a service token may tag it.
    _check_image_ownership(db, image_id, ctx)
    ids: list[int] = list(payload.tag_ids)
    if payload.names:
        # TODO(C1): resolving tags by global name is an existence oracle and
        # allows attaching foreign tags; needs the tag tenant/ownership model.
        tags = db.execute(select(Tag).where(Tag.name.in_(payload.names))).scalars().all()
        ids.extend([t.id for t in tags])
    ids = list({int(i) for i in ids})
    for tid in ids:
        existing = db.execute(
            select(ImageTag).where(ImageTag.image_id == image_id, ImageTag.tag_id == tid)
        ).scalar_one_or_none()
        if not existing:
            db.add(ImageTag(image_id=image_id, tag_id=tid))
    db.commit()
    return TagItemsResponse(count=len(ids))


@router.post("/albums/{album_id}", response_model=TagItemsResponse)
def tag_album(
    album_id: int,
    payload: TagItemsRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> TagItemsResponse:
    # Same fail-closed guard as the album handlers: service, tenant match,
    # or owner match; otherwise 404.
    _check_album_access(db.get(Album, album_id), ctx)
    ids: list[int] = list(payload.tag_ids)
    if payload.names:
        # TODO(C1): resolving tags by global name is an existence oracle and
        # allows attaching foreign tags; needs the tag tenant/ownership model.
        tags = db.execute(select(Tag).where(Tag.name.in_(payload.names))).scalars().all()
        ids.extend([t.id for t in tags])
    ids = list({int(i) for i in ids})
    for tid in ids:
        existing = db.execute(
            select(AlbumTag).where(AlbumTag.album_id == album_id, AlbumTag.tag_id == tid)
        ).scalar_one_or_none()
        if not existing:
            db.add(AlbumTag(album_id=album_id, tag_id=tid))
    db.commit()
    return TagItemsResponse(count=len(ids))
