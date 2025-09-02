from __future__ import annotations

import hashlib
import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Album, AlbumItem, ImageVersion
from ..workers.tasks import enqueue_album_cover

router = APIRouter(prefix="/albums", tags=["albums"])


@router.post("")
def create_album(payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    title = payload.get("title")
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    album = Album(
        title=title,
        description=payload.get("description"),
        default_visibility=payload.get("default_visibility") or "private",
        is_shareable=bool(payload.get("is_shareable") or False),
        share_age_threshold=int(payload.get("share_age_threshold") or 0),
    )
    db.add(album)
    db.commit()
    return {"id": album.id}


@router.put("/{album_id}")
def update_album(album_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    album = db.get(Album, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="not found")
    for k in ("title", "description", "default_visibility", "is_shareable", "share_age_threshold"):
        if k in payload and payload[k] is not None:
            setattr(album, k, payload[k])
    db.commit()
    return {"ok": True}


@router.post("/{album_id}/items")
def add_item(album_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    album = db.get(Album, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="not found")
    image_id = int(payload.get("image_id"))
    version_id = payload.get("version_id")
    position = payload.get("position")
    if position is None:
        # compute next position
        cnt = db.execute(select(AlbumItem).where(AlbumItem.album_id == album_id)).scalars().all()
        position = len(cnt)
    item = AlbumItem(album_id=album_id, position=int(position), image_id=image_id, version_id=int(version_id) if version_id else None)
    db.add(item)
    db.commit()
    return {"position": item.position}


@router.put("/{album_id}/items/reorder")
def reorder(album_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    items = payload.get("items") or []
    for it in items:
        from_pos = int(it.get("from_position"))
        to_pos = int(it.get("to_position"))
        item = db.get(AlbumItem, {"album_id": album_id, "position": from_pos})
        if item:
            item.position = to_pos
    db.commit()
    return {"ok": True}


@router.get("/{album_id}")
def get_album(album_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    album = db.get(Album, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="not found")
    items = db.execute(select(AlbumItem).where(AlbumItem.album_id == album_id).order_by(AlbumItem.position)).scalars().all()
    return {
        "id": album.id,
        "title": album.title,
        "description": album.description,
        "default_visibility": album.default_visibility,
        "is_shareable": album.is_shareable,
        "share_age_threshold": album.share_age_threshold,
        "items": [
            {"position": it.position, "image_id": it.image_id, "version_id": it.version_id}
            for it in items
        ],
    }


@router.post("/{album_id}/share")
def share_album(album_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    album = db.get(Album, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="not found")
    enable = bool(payload.get("enable"))
    if enable:
        token = secrets.token_urlsafe(16)
        album.share_token_hash = hashlib.sha256(token.encode()).hexdigest()
        if "share_age_threshold" in payload and payload["share_age_threshold"] is not None:
            album.share_age_threshold = int(payload["share_age_threshold"])
        db.commit()
        url = f"/p/albums/{token}"
        return {"share_url": url}
    else:
        album.share_token_hash = None
        db.commit()
        return {"share_url": None}


@router.get("/cover/{album_id}")
def album_cover(album_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    # trigger cover generation and return expected key
    key = enqueue_album_cover(album_id)
    return {"storage_key": key}

