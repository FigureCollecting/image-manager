"""Tests for the figure gallery integration.

Covers: FigureGallery model, ingest endpoint, gallery retrieval,
soft delete, reorder, and Celery task orchestration.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from app.models import Image as ImageModel, ImageVersion
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session


def _studio_photo_bytes(size: int = 64) -> bytes:
    """A synthetic "product photo": near-white backdrop with a solid
    dark-colored square subject in the middle -- similar in spirit to a
    typical MFC listing photo (uniform backdrop + centered figure)."""
    img = Image.new("RGB", (size, size), color=(250, 250, 248))
    q = size // 4
    for y in range(q, size - q):
        for x in range(q, size - q):
            img.putpixel((x, y), (20, 30, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestGalleryIngest:
    """POST /galleries/ingest — service-to-service batch import."""

    def test_ingest_queues_images(
        self, client: TestClient, service_headers: dict, db_session: Session
    ) -> None:
        with patch("app.routes.gallery_routes.ingest_gallery_images.delay") as mock_delay:
            resp = client.post(
                "/galleries/ingest",
                json={
                    "figureId": "mfc-12345",
                    "images": [
                        {"url": "https://static.mfc.net/big/12345.jpg", "position": 0},
                        {"url": "https://static.mfc.net/big/12345_2.jpg", "position": 1},
                    ],
                },
                headers=service_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["figureId"] == "mfc-12345"
        assert data["imagesQueued"] == 2
        mock_delay.assert_called_once()

    def test_ingest_skips_existing_source_urls(
        self, client: TestClient, service_headers: dict, db_session: Session
    ) -> None:
        """If source_url already exists for this figure_id, skip it."""
        from app.models import FigureGallery

        img = ImageModel(sha256="a" * 64, storage_key="existing/img.png")
        db_session.add(img)
        db_session.flush()

        gallery = FigureGallery(
            figure_id="mfc-99",
            source_url="https://static.mfc.net/big/99.jpg",
            image_id=img.id,
            position=0,
            source="mfc",
        )
        db_session.add(gallery)
        db_session.flush()

        with patch("app.routes.gallery_routes.ingest_gallery_images.delay") as mock_delay:
            resp = client.post(
                "/galleries/ingest",
                json={
                    "figureId": "mfc-99",
                    "images": [
                        {"url": "https://static.mfc.net/big/99.jpg", "position": 0},
                        {"url": "https://static.mfc.net/big/99_new.jpg", "position": 1},
                    ],
                },
                headers=service_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["duplicatesSkipped"] == 1
        assert data["imagesQueued"] == 1

    def test_ingest_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/galleries/ingest",
            json={"figureId": "mfc-1", "images": []},
        )
        assert resp.status_code == 401

    def test_ingest_validates_payload(self, client: TestClient, service_headers: dict) -> None:
        resp = client.post(
            "/galleries/ingest",
            json={"images": []},
            headers=service_headers,
        )
        assert resp.status_code == 422

    def test_ingest_empty_images_list(self, client: TestClient, service_headers: dict) -> None:
        with patch("app.routes.gallery_routes.ingest_gallery_images.delay"):
            resp = client.post(
                "/galleries/ingest",
                json={"figureId": "mfc-empty", "images": []},
                headers=service_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["imagesQueued"] == 0


class TestGalleryGet:
    """GET /galleries/{figureId} — retrieve ordered gallery."""

    def test_returns_ordered_gallery(
        self, client: TestClient, auth_headers: dict, db_session: Session
    ) -> None:
        from app.models import FigureGallery

        imgs = []
        for i in range(3):
            img = ImageModel(sha256=f"gal{i}" + "0" * 60, storage_key=f"gal/{i}.png")
            db_session.add(img)
            db_session.flush()
            imgs.append(img)

        for i, img in enumerate(imgs):
            g = FigureGallery(
                figure_id="mfc-200",
                source_url=f"https://mfc.net/{i}.jpg",
                image_id=img.id,
                position=i,
                caption=f"shot {i}" if i == 0 else None,
                source="mfc",
            )
            db_session.add(g)
        db_session.flush()

        resp = client.get("/galleries/mfc-200", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["figureId"] == "mfc-200"
        assert data["count"] == 3
        assert len(data["images"]) == 3
        assert data["images"][0]["position"] == 0
        assert data["images"][0]["caption"] == "shot 0"
        assert data["images"][2]["position"] == 2

    def test_returns_empty_for_unknown_figure(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/galleries/mfc-nonexistent", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["images"] == []

    def test_excludes_soft_deleted(
        self, client: TestClient, auth_headers: dict, db_session: Session
    ) -> None:
        import datetime as dt

        from app.models import FigureGallery

        img = ImageModel(sha256="del0" + "0" * 60, storage_key="gal/del.png")
        db_session.add(img)
        db_session.flush()

        g = FigureGallery(
            figure_id="mfc-del",
            source_url="https://mfc.net/del.jpg",
            image_id=img.id,
            position=0,
            source="mfc",
            deleted_at=dt.datetime.now(dt.timezone.utc),
        )
        db_session.add(g)
        db_session.flush()

        resp = client.get("/galleries/mfc-del", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/galleries/mfc-1")
        assert resp.status_code == 401


class TestGalleryDeleteImage:
    """DELETE /galleries/{figureId}/images/{imageId} — soft delete."""

    def test_soft_deletes_gallery_entry(
        self, client: TestClient, auth_headers: dict, db_session: Session
    ) -> None:
        from app.models import FigureGallery

        img = ImageModel(sha256="sdel" + "0" * 60, storage_key="gal/sdel.png")
        db_session.add(img)
        db_session.flush()

        g = FigureGallery(
            figure_id="mfc-300",
            source_url="https://mfc.net/300.jpg",
            image_id=img.id,
            position=0,
            source="mfc",
        )
        db_session.add(g)
        db_session.flush()

        resp = client.delete(f"/galleries/mfc-300/images/{img.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        db_session.refresh(g)
        assert g.deleted_at is not None

    def test_delete_nonexistent_returns_404(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.delete("/galleries/mfc-nope/images/9999", headers=auth_headers)
        assert resp.status_code == 404

    def test_does_not_delete_underlying_image(
        self, client: TestClient, auth_headers: dict, db_session: Session
    ) -> None:
        from app.models import FigureGallery

        img = ImageModel(sha256="keep" + "0" * 60, storage_key="gal/keep.png")
        db_session.add(img)
        db_session.flush()

        g = FigureGallery(
            figure_id="mfc-keep",
            source_url="https://mfc.net/keep.jpg",
            image_id=img.id,
            position=0,
            source="mfc",
        )
        db_session.add(g)
        db_session.flush()

        client.delete(f"/galleries/mfc-keep/images/{img.id}", headers=auth_headers)

        db_session.refresh(img)
        assert img.deleted_at is None

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.delete("/galleries/mfc-1/images/1")
        assert resp.status_code == 401


class TestGalleryReorder:
    """POST /galleries/{figureId}/reorder — update positions."""

    def test_reorder_updates_positions(
        self, client: TestClient, auth_headers: dict, db_session: Session
    ) -> None:
        from app.models import FigureGallery

        img_ids = []
        for i in range(3):
            img = ImageModel(sha256=f"reord{i}" + "0" * 59, storage_key=f"gal/r{i}.png")
            db_session.add(img)
            db_session.flush()
            img_ids.append(img.id)

            g = FigureGallery(
                figure_id="mfc-reorder",
                source_url=f"https://mfc.net/r{i}.jpg",
                image_id=img.id,
                position=i,
                source="mfc",
            )
            db_session.add(g)
        db_session.flush()

        # Reverse the order
        resp = client.post(
            "/galleries/mfc-reorder/reorder",
            json={"imageIds": list(reversed(img_ids))},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify new positions
        from app.models import FigureGallery

        entries = (
            db_session.query(FigureGallery)
            .filter(
                FigureGallery.figure_id == "mfc-reorder",
                FigureGallery.deleted_at.is_(None),
            )
            .order_by(FigureGallery.position)
            .all()
        )
        assert entries[0].image_id == img_ids[2]
        assert entries[1].image_id == img_ids[1]
        assert entries[2].image_id == img_ids[0]

    def test_reorder_empty_list(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/galleries/mfc-empty/reorder",
            json={"imageIds": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/galleries/mfc-1/reorder", json={"imageIds": []})
        assert resp.status_code == 401


class TestIngestGalleryTask:
    """Test the Celery task for gallery image ingestion."""

    def test_task_downloads_and_creates_records(self, db_session: Session) -> None:
        import io
        from contextlib import contextmanager
        from unittest.mock import patch

        from PIL import Image

        from app.workers.tasks import ingest_gallery_images

        img_data = io.BytesIO()
        Image.new("RGB", (8, 8), (255, 0, 0)).save(img_data, format="PNG")
        img_bytes = img_data.getvalue()

        from app.hashing import sha256_bytes

        sha = sha256_bytes(img_bytes)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.raise_for_status = MagicMock()

        mock_s3 = MagicMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        @contextmanager
        def _fake_ws(session_factory=None):
            yield db_session

        with (
            patch("app.workers.tasks.worker_session", _fake_ws),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.workers.tasks.httpx") as mock_httpx,
        ):
            mock_httpx.get.return_value = mock_response

            ingest_gallery_images(
                figure_id="mfc-task-1",
                images=[
                    {"url": "https://mfc.net/1.jpg", "position": 0},
                    {"url": "https://mfc.net/2.jpg", "position": 1},
                ],
            )

        from app.models import FigureGallery

        entries = (
            db_session.query(FigureGallery)
            .filter(FigureGallery.figure_id == "mfc-task-1")
            .order_by(FigureGallery.position)
            .all()
        )
        assert len(entries) == 2
        assert entries[0].position == 0
        assert entries[1].position == 1
        assert entries[0].source == "mfc"

    def test_task_deduplicates_by_sha256(self, db_session: Session) -> None:
        """If image with same SHA256 exists, reuse it."""
        import io
        from contextlib import contextmanager

        from PIL import Image

        from app.workers.tasks import ingest_gallery_images

        img_data = io.BytesIO()
        Image.new("RGB", (8, 8), (0, 255, 0)).save(img_data, format="PNG")
        img_bytes = img_data.getvalue()

        from app.hashing import sha256_bytes

        sha = sha256_bytes(img_bytes)

        # Pre-create an Image record with this hash
        existing = ImageModel(sha256=sha, storage_key="existing/dedup.png")
        db_session.add(existing)
        db_session.flush()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.raise_for_status = MagicMock()

        mock_s3 = MagicMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        @contextmanager
        def _fake_ws(session_factory=None):
            yield db_session

        with (
            patch("app.workers.tasks.worker_session", _fake_ws),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.workers.tasks.httpx") as mock_httpx,
        ):
            mock_httpx.get.return_value = mock_response

            ingest_gallery_images(
                figure_id="mfc-dedup",
                images=[{"url": "https://mfc.net/dedup.jpg", "position": 0}],
            )

        from app.models import FigureGallery

        entry = db_session.query(FigureGallery).filter(FigureGallery.figure_id == "mfc-dedup").one()
        # Should point to the existing image, not create a new one
        assert entry.image_id == existing.id
        # S3 upload should NOT have been called (content-addressable dedup)
        mock_s3.put_object.assert_not_called()

    def test_task_creates_v1_source_and_matted_public_v2(self, db_session: Session) -> None:
        """After storing a NEW source image, ingest also produces a matted
        v2 derivative: public visibility, matted=True, grounding scalars +
        thumbhash + dominant_color populated."""
        import io
        from contextlib import contextmanager

        from app.workers.tasks import ingest_gallery_images

        img_bytes = _studio_photo_bytes()
        from app.hashing import sha256_bytes

        sha256_bytes(img_bytes)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.raise_for_status = MagicMock()

        mock_s3 = MagicMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        @contextmanager
        def _fake_ws(session_factory=None):
            yield db_session

        with (
            patch("app.workers.tasks.worker_session", _fake_ws),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.workers.tasks.httpx") as mock_httpx,
        ):
            mock_httpx.get.return_value = mock_response

            ingest_gallery_images(
                figure_id="mfc-matte-1",
                images=[{"url": "https://mfc.net/matte1.jpg", "position": 0}],
            )

        from app.models import FigureGallery

        entry = (
            db_session.query(FigureGallery).filter(FigureGallery.figure_id == "mfc-matte-1").one()
        )
        versions = (
            db_session.query(ImageVersion)
            .filter(ImageVersion.image_id == entry.image_id)
            .order_by(ImageVersion.version_no)
            .all()
        )
        assert [v.version_no for v in versions] == [1, 2]

        v1, v2 = versions
        assert v1.visibility == "private"
        assert v1.matted is False

        assert v2.matted is True
        assert v2.visibility == "public"
        assert v2.mime == "image/png"
        assert v2.derived_from_version == 1
        assert v2.bottom_margin_frac is not None
        assert v2.thumbhash is not None
        assert v2.dominant_color is not None

        # Stored bytes are a real RGBA PNG, not RGB.
        put_calls = [
            c for c in mock_s3.put_object.call_args_list if c.kwargs["Key"] == v2.storage_key
        ]
        assert len(put_calls) == 1
        stored_bytes = put_calls[0].kwargs["Body"]
        stored_img = Image.open(io.BytesIO(stored_bytes))
        assert stored_img.mode == "RGBA"

    def test_task_matting_failure_does_not_abort_ingest(self, db_session: Session) -> None:
        """A matting failure on one image must not blow up the whole
        gallery ingest -- the gallery entry (and v1 source) still land."""
        from contextlib import contextmanager

        from app.workers.tasks import ingest_gallery_images

        img_bytes = _studio_photo_bytes()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.raise_for_status = MagicMock()

        mock_s3 = MagicMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        @contextmanager
        def _fake_ws(session_factory=None):
            yield db_session

        with (
            patch("app.workers.tasks.worker_session", _fake_ws),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.workers.tasks.httpx") as mock_httpx,
            patch("app.workers.tasks._apply_transforms", side_effect=RuntimeError("boom")),
        ):
            mock_httpx.get.return_value = mock_response

            ingest_gallery_images(
                figure_id="mfc-matte-fail",
                images=[{"url": "https://mfc.net/mattefail.jpg", "position": 0}],
            )

        from app.models import FigureGallery

        entry = (
            db_session.query(FigureGallery)
            .filter(FigureGallery.figure_id == "mfc-matte-fail")
            .one()
        )
        versions = (
            db_session.query(ImageVersion)
            .filter(ImageVersion.image_id == entry.image_id)
            .order_by(ImageVersion.version_no)
            .all()
        )
        assert [v.version_no for v in versions] == [1]

    def test_task_watermark_does_not_corrupt_grounding_scalars(self, db_session: Session) -> None:
        """The bottom-right watermark corner must not be mistaken for the
        figure's own content when measuring bottom_margin_frac / the
        contact band -- grounding must be measured before the watermark is
        composited in."""
        from contextlib import contextmanager

        from app.workers.tasks import ingest_gallery_images

        # Backdrop everywhere except a solid subject block near the TOP,
        # leaving a large transparent gap at the bottom (where the
        # watermark is drawn) that must be reflected in bottom_margin_frac.
        img = Image.new("RGB", (200, 200), color=(250, 250, 248))
        for y in range(10, 60):
            for x in range(60, 140):
                img.putpixel((x, y), (20, 30, 40))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.raise_for_status = MagicMock()

        mock_s3 = MagicMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        @contextmanager
        def _fake_ws(session_factory=None):
            yield db_session

        with (
            patch("app.workers.tasks.worker_session", _fake_ws),
            patch("app.workers.tasks.get_s3", return_value=mock_s3),
            patch("app.config.get_settings", return_value=mock_settings),
            patch("app.workers.tasks.httpx") as mock_httpx,
        ):
            mock_httpx.get.return_value = mock_response

            ingest_gallery_images(
                figure_id="mfc-matte-ground",
                images=[{"url": "https://mfc.net/matteground.jpg", "position": 0}],
            )

        from app.models import FigureGallery

        entry = (
            db_session.query(FigureGallery)
            .filter(FigureGallery.figure_id == "mfc-matte-ground")
            .one()
        )
        v2 = (
            db_session.query(ImageVersion)
            .filter(ImageVersion.image_id == entry.image_id, ImageVersion.matted.is_(True))
            .one()
        )
        # Subject bottom edge is at row 59 of 200 -> bottom margin should be
        # roughly (200-1-59)/200 ~= 0.70, NOT near-zero (which is what a
        # watermark-corrupted measurement bottom-right would produce).
        assert v2.bottom_margin_frac > 0.5
