"""Tests for serve routes, public access, and visibility enforcement."""

from app.models import Image, ImageVersion


class TestServeVersion:
    def test_serve_private_with_auth(self, client, auth_headers, db_session):
        img = Image(sha256="s1" * 32, bytes=100, mime="image/jpeg", storage_key="k/s1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/s1", visibility="private", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        # Should redirect to presigned URL
        assert r.status_code == 302
        assert "Location" in r.headers

    def test_serve_missing_404(self, client, auth_headers):
        r = client.get("/serve/99999@99999", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404

    def test_serve_wrong_image_version_combo_404(self, client, auth_headers, db_session):
        img1 = Image(sha256="s2" * 32, bytes=100, mime="image/jpeg", storage_key="k/s2")
        img2 = Image(sha256="s3" * 32, bytes=100, mime="image/jpeg", storage_key="k/s3")
        db_session.add_all([img1, img2])
        db_session.flush()
        v = ImageVersion(
            image_id=img1.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/s2", visibility="private", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # Version belongs to img1 but we request with img2
        r = client.get(f"/serve/{img2.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404


class TestPublicServe:
    def test_public_serve_public_version(self, client, db_session):
        img = Image(sha256="p1" * 32, bytes=100, mime="image/jpeg", storage_key="k/p1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/p1", visibility="public", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # No auth needed for public endpoint
        r = client.get(f"/public/{img.id}@{v.id}", follow_redirects=False)
        assert r.status_code == 302
        assert "Location" in r.headers
        assert r.headers.get("Cache-Control") == "public, max-age=600"

    def test_public_serve_private_version_403(self, client, db_session):
        img = Image(sha256="p2" * 32, bytes=100, mime="image/jpeg", storage_key="k/p2")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/p2", visibility="private", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.get(f"/public/{img.id}@{v.id}", follow_redirects=False)
        assert r.status_code == 403


class TestVisibilityEnforcement:
    def test_serve_sets_cache_headers(self, client, auth_headers, db_session):
        img = Image(sha256="v1" * 32, bytes=100, mime="image/jpeg", storage_key="k/v1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/v1", visibility="private", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers.get("Cache-Control") == "private, max-age=600"
        assert r.headers.get("ETag") == "v1" * 32
