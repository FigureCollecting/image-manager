from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Album, AlbumTag, Image, ImageTag, Tag

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/images")
def search_images(query: Optional[str] = None, tags: Optional[str] = None, db: Session = Depends(get_db)) -> dict:
    stmt: Select[tuple[Image]] = select(Image)
    if query:
        q = f"%{query}%"
        stmt = stmt.where((Image.mime.ilike(q)) | (Image.phash.ilike(q)) | (Image.storage_key.ilike(q)))
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            stmt = (
                stmt.join(ImageTag, ImageTag.image_id == Image.id, isouter=True)
                .join(Tag, Tag.id == ImageTag.tag_id, isouter=True)
                .where(Tag.name.in_(tag_list))
            )
    rows = db.execute(stmt.limit(100)).scalars().all()
    return {"results": [{"id": r.id, "mime": r.mime, "sha256": r.sha256} for r in rows]}


@router.get("/albums")
def search_albums(query: Optional[str] = None, tags: Optional[str] = None, db: Session = Depends(get_db)) -> dict:
    stmt: Select[tuple[Album]] = select(Album)
    if query:
        q = f"%{query}%"
        stmt = stmt.where((Album.title.ilike(q)) | (Album.description.ilike(q)))
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            stmt = (
                stmt.join(AlbumTag, AlbumTag.album_id == Album.id, isouter=True)
                .join(Tag, Tag.id == AlbumTag.tag_id, isouter=True)
                .where(Tag.name.in_(tag_list))
            )
    rows = db.execute(stmt.limit(100)).scalars().all()
    return {"results": [{"id": r.id, "title": r.title} for r in rows]}

