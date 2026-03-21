import datetime as dt
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_auth_ctx
from ..models import Album, AlbumItem
from ..policy import AuthCtx
from ..schemas import (
    AddAlbumItemRequest,
    AddAlbumItemResponse,
    AlbumCoverResponse,
    AlbumDetailResponse,
    CreateAlbumRequest,
    CreateAlbumResponse,
    OkResponse,
    ReorderRequest,
    ShareAlbumRequest,
    ShareAlbumResponse,
    UpdateAlbumRequest,
)
from ..workers.tasks import enqueue_album_cover

router = APIRouter(prefix="/albums", tags=["albums"])


@router.post("", response_model=CreateAlbumResponse)
def create_album(
    payload: CreateAlbumRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> CreateAlbumResponse:
    album = Album(
        title=payload.title,
        description=payload.description,
        default_visibility=payload.default_visibility,
        is_shareable=payload.is_shareable,
        share_age_threshold=payload.share_age_threshold,
        owner_user_id=ctx.subject if not ctx.is_service else None,
        tenant_id=ctx.tenant_id,
    )
    db.add(album)
    db.commit()
    return CreateAlbumResponse(id=album.id)


@router.put("/{album_id}", response_model=OkResponse)
def update_album(
    album_id: int,
    payload: UpdateAlbumRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    album = db.get(Album, album_id)
    if not album or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(album, k, v)
    db.commit()
    return OkResponse(ok=True)


@router.post("/{album_id}/items", response_model=AddAlbumItemResponse)
def add_item(
    album_id: int,
    payload: AddAlbumItemRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> AddAlbumItemResponse:
    album = db.get(Album, album_id)
    if not album or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    position = payload.position
    if position is None:
        cnt = db.execute(select(AlbumItem).where(AlbumItem.album_id == album_id)).scalars().all()
        position = len(cnt)
    item = AlbumItem(
        album_id=album_id,
        position=position,
        image_id=payload.image_id,
        version_id=payload.version_id,
    )
    db.add(item)
    db.commit()
    return AddAlbumItemResponse(position=item.position)


@router.put("/{album_id}/items/reorder", response_model=OkResponse)
def reorder(
    album_id: int,
    payload: ReorderRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    album = db.get(Album, album_id)
    if not album or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    for it in payload.items:
        item = db.get(AlbumItem, {"album_id": album_id, "position": it.from_position})
        if item:
            item.position = it.to_position
    db.commit()
    return OkResponse(ok=True)


@router.get("/{album_id}", response_model=AlbumDetailResponse)
def get_album(
    album_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> AlbumDetailResponse:
    album = db.get(Album, album_id)
    if not album or album.deleted_at is not None or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    items = db.execute(select(AlbumItem).where(AlbumItem.album_id == album_id).order_by(AlbumItem.position)).scalars().all()
    return AlbumDetailResponse(
        id=album.id,
        title=album.title,
        description=album.description,
        default_visibility=album.default_visibility,
        is_shareable=album.is_shareable,
        share_age_threshold=album.share_age_threshold,
        items=[
            {"position": it.position, "image_id": it.image_id, "version_id": it.version_id}
            for it in items
        ],
    )


@router.post("/{album_id}/share", response_model=ShareAlbumResponse)
def share_album(
    album_id: int,
    payload: ShareAlbumRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> ShareAlbumResponse:
    album = db.get(Album, album_id)
    if not album or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    if payload.enable:
        token = secrets.token_urlsafe(16)
        album.share_token_hash = hashlib.sha256(token.encode()).hexdigest()
        if payload.share_age_threshold is not None:
            album.share_age_threshold = payload.share_age_threshold
        db.commit()
        url = f"/p/albums/{token}"
        return ShareAlbumResponse(share_url=url)
    else:
        album.share_token_hash = None
        db.commit()
        return ShareAlbumResponse(share_url=None)


@router.get("/cover/{album_id}", response_model=AlbumCoverResponse)
def album_cover(
    album_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> AlbumCoverResponse:
    album = db.get(Album, album_id)
    if not album or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    key = enqueue_album_cover(album_id)
    return AlbumCoverResponse(storage_key=key)


@router.delete("/{album_id}", response_model=OkResponse)
def delete_album(
    album_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    album = db.get(Album, album_id)
    if not album or album.deleted_at is not None or (album.tenant_id and album.tenant_id != ctx.tenant_id):
        raise HTTPException(status_code=404, detail="not found")
    album.deleted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return OkResponse(ok=True)
