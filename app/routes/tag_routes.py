from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AlbumTag, ImageTag, Tag

router = APIRouter(prefix="/tags", tags=["tags"])


@router.post("")
def create_tag(payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    name = payload.get("name")
    scope = payload.get("scope")
    tenant_id = payload.get("tenant_id")
    if not name or scope not in ("global", "tenant", "user"):
        raise HTTPException(status_code=400, detail="invalid tag")
    t = Tag(name=name, scope=scope, tenant_id=tenant_id)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name}


@router.post("/images/{image_id}")
def tag_image(image_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    tag_ids: List[int] = payload.get("tag_ids") or []
    names: List[str] = payload.get("names") or []
    ids: List[int] = list(tag_ids)
    if names:
        tags = db.execute(select(Tag).where(Tag.name.in_(names))).scalars().all()
        ids.extend([t.id for t in tags])
    ids = list({int(i) for i in ids})
    for tid in ids:
        db.merge(ImageTag(image_id=image_id, tag_id=tid))
    db.commit()
    return {"count": len(ids)}


@router.post("/albums/{album_id}")
def tag_album(album_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    tag_ids: List[int] = payload.get("tag_ids") or []
    names: List[str] = payload.get("names") or []
    ids: List[int] = list(tag_ids)
    if names:
        tags = db.execute(select(Tag).where(Tag.name.in_(names))).scalars().all()
        ids.extend([t.id for t in tags])
    ids = list({int(i) for i in ids})
    for tid in ids:
        db.merge(AlbumTag(album_id=album_id, tag_id=tid))
    db.commit()
    return {"count": len(ids)}

