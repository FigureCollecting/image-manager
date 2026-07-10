"""Tests for Pydantic request/response validation on all endpoints.

TDD: These tests should FAIL initially because routes still use Dict[str, Any].
After wiring Pydantic models, they should all pass.
"""

from __future__ import annotations

import hashlib
import io
from unittest.mock import MagicMock, patch

from app.models import Album, Image

# ---------------------------------------------------------------------------
# Image routes — validation
# ---------------------------------------------------------------------------


class TestInitiateUploadValidation:
    def test_missing_filename(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"mime": "image/jpeg", "size": 1024},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_missing_mime(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "a.jpg", "size": 1024},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_missing_size(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "a.jpg", "mime": "image/jpeg"},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_zero_size(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "a.jpg", "mime": "image/jpeg", "size": 0},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_negative_size(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "a.jpg", "mime": "image/jpeg", "size": -1},
            headers=auth_headers,
        )
        assert r.status_code == 422


class TestCompleteUploadValidation:
    def test_missing_sha256(self, client, auth_headers):
        r = client.post(
            "/images/complete",
            json={"key": "k", "mime": "image/jpeg", "size": 100},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_sha256_too_short(self, client, auth_headers):
        r = client.post(
            "/images/complete",
            json={"sha256": "abc", "key": "k", "mime": "image/jpeg", "size": 100},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_missing_key(self, client, auth_headers):
        r = client.post(
            "/images/complete",
            json={"sha256": "a" * 64, "mime": "image/jpeg", "size": 100},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_zero_size(self, client, auth_headers):
        r = client.post(
            "/images/complete",
            json={"sha256": "a" * 64, "key": "k", "mime": "image/jpeg", "size": 0},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_valid_complete_response_shape(self, client, auth_headers):
        # complete_upload now proves possession by hashing the staging
        # object, so the mock bytes must hash to the submitted sha256.
        data_bytes = b"shape-test-bytes"
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(data_bytes)}
        with patch("app.routes.image_routes.get_s3", return_value=mock_s3):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": hashlib.sha256(data_bytes).hexdigest(),
                    "key": "uploads/k",
                    "mime": "image/jpeg",
                    "size": len(data_bytes),
                },
                headers=auth_headers,
            )
        assert r.status_code == 200
        data = r.json()
        assert "image_id" in data
        assert "created" in data
        assert isinstance(data["created"], bool)


class TestGetImageResponseShape:
    def test_image_detail_shape(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="b" * 64, bytes=100, mime="image/png", storage_key="k/1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        r = client.get(f"/images/{img.id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert "sha256" in data
        assert "versions" in data
        assert isinstance(data["versions"], list)


class TestCreateVersionValidation:
    def test_invalid_visibility(self, client, auth_headers):
        r = client.post(
            "/images/1/versions",
            json={"visibility": "nonsense"},
            headers=auth_headers,
        )
        assert r.status_code == 422


class TestSetVisibilityValidation:
    def test_missing_visibility(self, client, auth_headers):
        r = client.post("/images/1/versions/1/visibility", json={}, headers=auth_headers)
        assert r.status_code == 422

    def test_invalid_visibility(self, client, auth_headers):
        r = client.post(
            "/images/1/versions/1/visibility", json={"visibility": "nonsense"}, headers=auth_headers
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Album routes — validation
# ---------------------------------------------------------------------------


class TestCreateAlbumValidation:
    def test_missing_title(self, client, auth_headers):
        r = client.post("/albums", json={}, headers=auth_headers)
        assert r.status_code == 422

    def test_invalid_default_visibility(self, client, auth_headers):
        r = client.post(
            "/albums", json={"title": "t", "default_visibility": "nonsense"}, headers=auth_headers
        )
        assert r.status_code == 422

    def test_valid_create_response_shape(self, client, auth_headers):
        r = client.post("/albums", json={"title": "My Album"}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert isinstance(data["id"], int)


class TestAddAlbumItemValidation:
    def test_missing_image_id(self, client, auth_headers, db_session):
        album = Album(title="test")
        db_session.add(album)
        db_session.commit()
        r = client.post(f"/albums/{album.id}/items", json={}, headers=auth_headers)
        assert r.status_code == 422


class TestShareAlbumValidation:
    def test_missing_enable(self, client, auth_headers, db_session):
        album = Album(title="test")
        db_session.add(album)
        db_session.commit()
        r = client.post(f"/albums/{album.id}/share", json={}, headers=auth_headers)
        assert r.status_code == 422


class TestGetAlbumResponseShape:
    def test_album_detail_shape(self, client, auth_headers, db_session):
        album = Album(
            title="My Album",
            description="desc",
            default_visibility="private",
            tenant_id="11111111-2222-3333-4444-555555555555",
        )
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert "title" in data
        assert "items" in data
        assert isinstance(data["items"], list)


# ---------------------------------------------------------------------------
# Tag routes — validation
# ---------------------------------------------------------------------------


class TestCreateTagValidation:
    def test_missing_name(self, client, auth_headers):
        r = client.post("/tags", json={"scope": "global"}, headers=auth_headers)
        assert r.status_code == 422

    def test_missing_scope(self, client, auth_headers):
        r = client.post("/tags", json={"name": "landscape"}, headers=auth_headers)
        assert r.status_code == 422

    def test_invalid_scope(self, client, auth_headers):
        r = client.post(
            "/tags", json={"name": "landscape", "scope": "invalid"}, headers=auth_headers
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# External routes — validation
# ---------------------------------------------------------------------------


class TestCreateExternalRefValidation:
    def test_missing_ref_type(self, client, auth_headers):
        r = client.post("/external/refs", json={"ref_id": "x", "image_id": 1}, headers=auth_headers)
        assert r.status_code == 422

    def test_missing_ref_id(self, client, auth_headers):
        r = client.post(
            "/external/refs", json={"ref_type": "x", "image_id": 1}, headers=auth_headers
        )
        assert r.status_code == 422

    def test_missing_image_id(self, client, auth_headers):
        r = client.post(
            "/external/refs", json={"ref_type": "x", "ref_id": "y"}, headers=auth_headers
        )
        assert r.status_code == 422

    def test_valid_create_response_shape(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        img = Image(sha256="c" * 64, bytes=50, mime="image/jpeg", storage_key="k/ext")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()
        r = client.post(
            "/external/refs",
            json={"ref_type": "post", "ref_id": "123", "image_id": img.id},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert isinstance(data["id"], int)
