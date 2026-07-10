"""Tests for serve routes, public access, and visibility enforcement.

Both /serve and /public byte-stream through the app rather than 302
redirecting to a presigned S3/MinIO URL -- a presigned URL for the
internal minio:9000 host is not resolvable from a browser, which made
every served image (matted derivatives especially, since they're the ones
meant to render directly in fc-mobile) unreachable outside the Docker
network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models import Image, ImageVersion


def _mock_s3(body: bytes) -> MagicMock:
    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.read.return_value = body
    mock_s3.get_object.return_value = {"Body": mock_body}
    return mock_s3


class TestServeVersion:
    def test_serve_private_with_auth_streams_bytes(self, client, auth_headers, db_session):
        img = Image(sha256="s1" * 32, bytes=100, mime="image/jpeg", storage_key="k/s1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/s1",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"fake-jpeg-bytes")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)

        assert r.status_code == 200
        assert r.content == b"fake-jpeg-bytes"
        assert r.headers.get("content-type") == "image/jpeg"
        # No redirect to an internal-only host -- the app served the bytes itself.
        assert "location" not in r.headers

    def test_serve_missing_404(self, client, auth_headers):
        r = client.get("/serve/99999@99999", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404

    def test_serve_wrong_image_version_combo_404(self, client, auth_headers, db_session):
        img1 = Image(sha256="s2" * 32, bytes=100, mime="image/jpeg", storage_key="k/s2")
        img2 = Image(sha256="s3" * 32, bytes=100, mime="image/jpeg", storage_key="k/s3")
        db_session.add_all([img1, img2])
        db_session.flush()
        v = ImageVersion(
            image_id=img1.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/s2",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # Version belongs to img1 but we request with img2
        r = client.get(f"/serve/{img2.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404


class TestPublicServe:
    def test_public_serve_public_version_streams_bytes(self, client, db_session):
        img = Image(sha256="p1" * 32, bytes=100, mime="image/jpeg", storage_key="k/p1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/p1",
            visibility="public",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # No auth needed for public endpoint
        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"fake-public-bytes")):
            r = client.get(f"/public/{img.id}@{v.id}", follow_redirects=False)

        assert r.status_code == 200
        assert r.content == b"fake-public-bytes"
        assert "location" not in r.headers
        assert r.headers.get("Cache-Control") == "public, max-age=31536000, immutable"

    def test_public_serve_matted_rgba_png_streams_correctly(self, client, db_session):
        """The primary case this route exists for: a matted derivative,
        public visibility, real RGBA PNG bytes served directly."""
        img = Image(sha256="m1" * 32, bytes=100, mime="image/jpeg", storage_key="k/m1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=2,
            transform_spec={"matte": True},
            mime="image/png",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/m1-matte.png",
            visibility="public",
            age_rating=0,
            matted=True,
            bottom_margin_frac=0.1,
            contact_band_center_x_frac=0.5,
            contact_band_width_frac=0.3,
            thumbhash="abc123",
            dominant_color="#112233",
        )
        db_session.add(v)
        db_session.commit()

        with patch(
            "app.routes.serve_routes.get_s3", return_value=_mock_s3(b"\x89PNG-fake-rgba-bytes")
        ):
            r = client.get(f"/public/{img.id}@{v.id}", follow_redirects=False)

        assert r.status_code == 200
        assert r.headers.get("content-type") == "image/png"

    def test_public_serve_private_version_403(self, client, db_session):
        img = Image(sha256="p2" * 32, bytes=100, mime="image/jpeg", storage_key="k/p2")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/p2",
            visibility="private",
            age_rating=0,
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
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/v1",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"data")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)

        assert r.status_code == 200
        assert r.headers.get("Cache-Control") == "private, max-age=600"
        assert r.headers.get("ETag") == "v1" * 32
