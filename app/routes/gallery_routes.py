from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_auth_ctx
from ..models import FigureGallery, ImageVersion
from ..policy import AuthCtx
from ..schemas import (
    ContactBandResponse,
    DisplayMetaResponse,
    GalleryDetailResponse,
    GalleryImageSummary,
    IngestGalleryRequest,
    IngestGalleryResponse,
    OkResponse,
    ReorderGalleryRequest,
)
from ..url_guard import _validate_ingest_url
from ..workers.tasks import ingest_gallery_images

router = APIRouter(prefix="/galleries", tags=["galleries"])


@router.post("/ingest", response_model=IngestGalleryResponse)
def ingest_gallery(
    payload: IngestGalleryRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> IngestGalleryResponse:
    # TODO(C1): galleries are fully open to any authenticated caller
    # (FigureGallery has no owner column); needs the grant model. Do NOT add a
    # service-only stopgap -- it would break the ingest flow.
    figure_id = payload.figureId

    # SSRF gate (PRIMARY): the worker fetches these URLs server-side, so any
    # internal target in the batch rejects the WHOLE request before anything
    # is enqueued -- a mostly-legit payload cannot smuggle one internal fetch.
    # The worker re-checks right before httpx.get (defense-in-depth).
    for img in payload.images:
        try:
            _validate_ingest_url(img.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid image url: {exc}") from exc

    # Check which source_urls already exist for this figure
    existing = (
        db.query(FigureGallery.source_url)
        .filter(
            FigureGallery.figure_id == figure_id,
            FigureGallery.deleted_at.is_(None),
        )
        .all()
    )
    existing_urls = {row[0] for row in existing}

    new_images = [img for img in payload.images if img.url not in existing_urls]
    duplicates_skipped = len(payload.images) - len(new_images)

    if new_images:
        ingest_gallery_images.delay(
            figure_id=figure_id,
            images=[
                {"url": img.url, "position": img.position, "caption": img.caption}
                for img in new_images
            ],
        )

    return IngestGalleryResponse(
        figureId=figure_id,
        imagesQueued=len(new_images),
        duplicatesSkipped=duplicates_skipped,
    )


@router.get("/{figure_id}", response_model=GalleryDetailResponse)
def get_gallery(
    figure_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> GalleryDetailResponse:
    # TODO(C1): galleries are fully open to any authenticated caller
    # (FigureGallery has no owner column); needs the grant model. Do NOT add a
    # service-only stopgap -- it would break the ingest flow.
    entries = (
        db.query(FigureGallery)
        .filter(
            FigureGallery.figure_id == figure_id,
            FigureGallery.deleted_at.is_(None),
        )
        .order_by(FigureGallery.position)
        .all()
    )
    return GalleryDetailResponse(
        figureId=figure_id,
        images=[
            GalleryImageSummary(
                id=e.id,
                url=f"/serve/{e.image_id}",
                position=e.position,
                caption=e.caption,
            )
            for e in entries
        ],
        count=len(entries),
    )


@router.get("/{figure_id}/display-meta", response_model=DisplayMetaResponse)
def get_gallery_display_meta(
    figure_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> DisplayMetaResponse:
    # TODO(C1): galleries are fully open to any authenticated caller
    # (FigureGallery has no owner column); needs the grant model. Do NOT add a
    # service-only stopgap -- it would break the ingest flow.
    entry = (
        db.query(FigureGallery)
        .filter(
            FigureGallery.figure_id == figure_id,
            FigureGallery.deleted_at.is_(None),
        )
        .order_by(FigureGallery.position)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="not found")

    matted_version = (
        db.query(ImageVersion)
        .filter(
            ImageVersion.image_id == entry.image_id,
            ImageVersion.matted.is_(True),
            ImageVersion.deleted_at.is_(None),
        )
        .order_by(ImageVersion.version_no.desc())
        .first()
    )
    if not matted_version:
        return DisplayMetaResponse(matted=False)

    contact_band = None
    if (
        matted_version.contact_band_center_x_frac is not None
        and matted_version.contact_band_width_frac is not None
    ):
        contact_band = ContactBandResponse(
            centerXFrac=matted_version.contact_band_center_x_frac,
            widthFrac=matted_version.contact_band_width_frac,
        )

    return DisplayMetaResponse(
        matted=True,
        matteImageId=str(entry.image_id),
        matteVersionId=str(matted_version.id),
        bottomMarginFrac=matted_version.bottom_margin_frac,
        contactBand=contact_band,
        thumbhash=matted_version.thumbhash,
        dominantColor=matted_version.dominant_color,
    )


@router.delete("/{figure_id}/images/{image_id}", response_model=OkResponse)
def delete_gallery_image(
    figure_id: str,
    image_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    # TODO(C1): galleries are fully open to any authenticated caller
    # (FigureGallery has no owner column); needs the grant model. Do NOT add a
    # service-only stopgap -- it would break the ingest flow.
    entry = (
        db.query(FigureGallery)
        .filter(
            FigureGallery.figure_id == figure_id,
            FigureGallery.image_id == image_id,
            FigureGallery.deleted_at.is_(None),
        )
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="not found")
    entry.deleted_at = dt.datetime.now(dt.UTC)
    db.commit()
    return OkResponse(ok=True)


@router.post("/{figure_id}/reorder", response_model=OkResponse)
def reorder_gallery(
    figure_id: str,
    payload: ReorderGalleryRequest,
    db: Session = Depends(get_db),  # noqa: B008
    ctx: AuthCtx = Depends(require_auth_ctx),  # noqa: B008
) -> OkResponse:
    # TODO(C1): galleries are fully open to any authenticated caller
    # (FigureGallery has no owner column); needs the grant model. Do NOT add a
    # service-only stopgap -- it would break the ingest flow.
    entries = (
        db.query(FigureGallery)
        .filter(
            FigureGallery.figure_id == figure_id,
            FigureGallery.deleted_at.is_(None),
        )
        .all()
    )
    entry_map = {e.image_id: e for e in entries}

    for position, img_id in enumerate(payload.imageIds):
        if img_id in entry_map:
            entry_map[img_id].position = position

    db.commit()
    return OkResponse(ok=True)
