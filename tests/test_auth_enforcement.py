"""Tests that all protected routes return 401 when no auth token is provided.

TDD: These tests should FAIL initially because auth enforcement is missing on
most routes. After adding get_auth_ctx checks, they should all pass.
"""

from __future__ import annotations

import hashlib
import io
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Image routes
# ---------------------------------------------------------------------------


class TestImageRoutesRequireAuth:
    def test_initiate_upload_no_auth(self, client):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "a.jpg", "mime": "image/jpeg", "size": 1024},
        )
        assert r.status_code == 401

    def test_initiate_upload_with_auth(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "a.jpg", "mime": "image/jpeg", "size": 1024},
            headers=auth_headers,
        )
        assert r.status_code != 401

    def test_complete_upload_no_auth(self, client):
        r = client.post(
            "/images/complete",
            json={"sha256": "a" * 64, "key": "uploads/k", "mime": "image/jpeg", "size": 1024},
        )
        assert r.status_code == 401

    def test_complete_upload_with_auth(self, client, auth_headers):
        # Mock the staging object so the possession proof doesn't reach out
        # to a real S3 endpoint; this test only asserts auth clearance. The
        # key sits in the caller's own staging namespace (subject from the
        # auth_headers fixture) so the namespace binding clears too.
        data = b"auth-check-bytes"
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": io.BytesIO(data)}
        with patch("app.routes.image_routes.get_s3", return_value=mock_s3):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "key": "uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/k",
                    "mime": "image/jpeg",
                    "size": len(data),
                },
                headers=auth_headers,
            )
        assert r.status_code != 401

    def test_get_image_no_auth(self, client):
        r = client.get("/images/1")
        assert r.status_code == 401

    def test_get_image_with_auth(self, client, auth_headers):
        # 404 is fine — we just check it's not 401
        r = client.get("/images/1", headers=auth_headers)
        assert r.status_code != 401

    def test_get_image_version_no_auth(self, client):
        r = client.get("/images/1/versions/1")
        assert r.status_code == 401

    def test_get_image_version_with_auth(self, client, auth_headers):
        r = client.get("/images/1/versions/1", headers=auth_headers)
        assert r.status_code != 401

    def test_create_version_no_auth(self, client):
        r = client.post("/images/1/versions", json={"transform_spec": {}})
        assert r.status_code == 401

    def test_create_version_with_auth(self, client, auth_headers):
        r = client.post("/images/1/versions", json={"transform_spec": {}}, headers=auth_headers)
        # Will be 400 (no base version) — just not 401
        assert r.status_code != 401

    def test_set_visibility_no_auth(self, client):
        r = client.post("/images/1/versions/1/visibility", json={"visibility": "public"})
        assert r.status_code == 401

    def test_set_visibility_with_auth(self, client, auth_headers):
        r = client.post(
            "/images/1/versions/1/visibility", json={"visibility": "public"}, headers=auth_headers
        )
        assert r.status_code != 401

    def test_expose_safe_alt_no_auth(self, client):
        r = client.post("/images/1/versions/1/expose-safe-alt", json={})
        assert r.status_code == 401

    def test_expose_safe_alt_with_auth(self, client, auth_headers):
        r = client.post("/images/1/versions/1/expose-safe-alt", json={}, headers=auth_headers)
        assert r.status_code != 401


# ---------------------------------------------------------------------------
# Album routes
# ---------------------------------------------------------------------------


class TestAlbumRoutesRequireAuth:
    def test_create_album_no_auth(self, client):
        r = client.post("/albums", json={"title": "test"})
        assert r.status_code == 401

    def test_create_album_with_auth(self, client, auth_headers):
        r = client.post("/albums", json={"title": "test"}, headers=auth_headers)
        assert r.status_code != 401

    def test_update_album_no_auth(self, client):
        r = client.put("/albums/1", json={"title": "updated"})
        assert r.status_code == 401

    def test_update_album_with_auth(self, client, auth_headers):
        r = client.put("/albums/1", json={"title": "updated"}, headers=auth_headers)
        assert r.status_code != 401

    def test_add_item_no_auth(self, client):
        r = client.post("/albums/1/items", json={"image_id": 1})
        assert r.status_code == 401

    def test_add_item_with_auth(self, client, auth_headers):
        r = client.post("/albums/1/items", json={"image_id": 1}, headers=auth_headers)
        assert r.status_code != 401

    def test_reorder_no_auth(self, client):
        r = client.put("/albums/1/items/reorder", json={"items": []})
        assert r.status_code == 401

    def test_reorder_with_auth(self, client, auth_headers):
        r = client.put("/albums/1/items/reorder", json={"items": []}, headers=auth_headers)
        assert r.status_code != 401

    def test_get_album_no_auth(self, client):
        r = client.get("/albums/1")
        assert r.status_code == 401

    def test_get_album_with_auth(self, client, auth_headers):
        r = client.get("/albums/1", headers=auth_headers)
        assert r.status_code != 401

    def test_share_album_no_auth(self, client):
        r = client.post("/albums/1/share", json={"enable": True})
        assert r.status_code == 401

    def test_share_album_with_auth(self, client, auth_headers):
        r = client.post("/albums/1/share", json={"enable": True}, headers=auth_headers)
        assert r.status_code != 401

    def test_album_cover_no_auth(self, client):
        r = client.get("/albums/cover/1")
        assert r.status_code == 401

    def test_album_cover_with_auth(self, client, auth_headers):
        r = client.get("/albums/cover/1", headers=auth_headers)
        assert r.status_code != 401


# ---------------------------------------------------------------------------
# Tag routes
# ---------------------------------------------------------------------------


class TestTagRoutesRequireAuth:
    def test_create_tag_no_auth(self, client):
        r = client.post("/tags", json={"name": "landscape", "scope": "global"})
        assert r.status_code == 401

    def test_create_tag_with_auth(self, client, auth_headers):
        r = client.post(
            "/tags", json={"name": "landscape", "scope": "global"}, headers=auth_headers
        )
        assert r.status_code != 401

    def test_tag_image_no_auth(self, client):
        r = client.post("/tags/images/1", json={"tag_ids": [1]})
        assert r.status_code == 401

    def test_tag_image_with_auth(self, client, auth_headers):
        r = client.post("/tags/images/1", json={"tag_ids": [1]}, headers=auth_headers)
        assert r.status_code != 401

    def test_tag_album_no_auth(self, client):
        r = client.post("/tags/albums/1", json={"tag_ids": [1]})
        assert r.status_code == 401

    def test_tag_album_with_auth(self, client, auth_headers):
        r = client.post("/tags/albums/1", json={"tag_ids": [1]}, headers=auth_headers)
        assert r.status_code != 401


# ---------------------------------------------------------------------------
# Search routes
# ---------------------------------------------------------------------------


class TestSearchRoutesRequireAuth:
    def test_search_images_no_auth(self, client):
        r = client.get("/search/images")
        assert r.status_code == 401

    def test_search_images_with_auth(self, client, auth_headers):
        r = client.get("/search/images", headers=auth_headers)
        assert r.status_code != 401

    def test_search_albums_no_auth(self, client):
        r = client.get("/search/albums")
        assert r.status_code == 401

    def test_search_albums_with_auth(self, client, auth_headers):
        r = client.get("/search/albums", headers=auth_headers)
        assert r.status_code != 401


# ---------------------------------------------------------------------------
# External routes
# ---------------------------------------------------------------------------


class TestExternalRoutesRequireAuth:
    def test_create_external_ref_no_auth(self, client):
        r = client.post("/external/refs", json={"ref_type": "foo", "ref_id": "bar", "image_id": 1})
        assert r.status_code == 401

    def test_create_external_ref_with_auth(self, client, auth_headers):
        r = client.post(
            "/external/refs",
            json={"ref_type": "foo", "ref_id": "bar", "image_id": 1},
            headers=auth_headers,
        )
        assert r.status_code != 401

    def test_by_external_ref_no_auth(self, client):
        r = client.get("/external/assets/by-external-ref?ref_type=foo&ref_id=bar")
        assert r.status_code == 401

    def test_by_external_ref_with_auth(self, client, auth_headers):
        r = client.get(
            "/external/assets/by-external-ref?ref_type=foo&ref_id=bar", headers=auth_headers
        )
        # 404 is fine
        assert r.status_code != 401


# ---------------------------------------------------------------------------
# Routes that SHOULD remain public
# ---------------------------------------------------------------------------


class TestPublicRoutes:
    def test_healthz_no_auth(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200

    def test_dev_token_no_auth(self, client):
        r = client.post("/auth/dev-token", json={"user_id": "test-user"})
        assert r.status_code == 200

    def test_dev_token_user_path_cannot_forge_service_subject(self, client):
        """user_id='service:x' via the user path would mint a token that
        deps.get_auth_ctx trusts as a GLOBAL service principal. Must be 400."""
        r = client.post("/auth/dev-token", json={"user_id": "service:x"})
        assert r.status_code == 400
