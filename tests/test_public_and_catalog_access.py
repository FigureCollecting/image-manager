"""Tests for serve routes, public access, and visibility enforcement.

Both /serve and /public byte-stream through the app rather than 302
redirecting to a presigned S3/MinIO URL -- a presigned URL for the
internal minio:9000 host is not resolvable from a browser, which made
every served image (matted derivatives especially, since they're the ones
meant to render directly in fc-mobile) unreachable outside the Docker
network.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from app.models import Image, ImageVersion, UserImageLink


def _mock_s3(body: bytes) -> MagicMock:
    mock_s3 = MagicMock()
    mock_body = MagicMock()
    mock_body.read.return_value = body
    mock_s3.get_object.return_value = {"Body": mock_body}
    return mock_s3


def _make_private_version(db_session, sha_seed: str, key: str) -> tuple[Image, ImageVersion]:
    img = Image(sha256=sha_seed * 32, bytes=100, mime="image/jpeg", storage_key=key)
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
        storage_key=key,
        visibility="private",
        age_rating=0,
    )
    db_session.add(v)
    db_session.commit()
    return img, v


class TestServeVersion:
    def test_serve_private_owner_streams_bytes(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        # The OWNER (has a UserImageLink) may stream a private version.
        img, v = _make_private_version(db_session, "s1", "k/s1")
        link_image_to_user(img.id)
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"fake-jpeg-bytes")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)

        assert r.status_code == 200
        assert r.content == b"fake-jpeg-bytes"
        assert r.headers.get("content-type") == "image/jpeg"
        # No redirect to an internal-only host -- the app served the bytes itself.
        assert "location" not in r.headers

    def test_serve_private_authenticated_non_owner_404(self, client, auth_headers, db_session):
        # An authenticated caller with NO UserImageLink must get 404 --
        # indistinguishable from a nonexistent image (no enumeration oracle).
        img, v = _make_private_version(db_session, "n1", "k/n1")
        # Link belongs to a DIFFERENT user; the auth_headers user owns nothing.
        db_session.add(
            UserImageLink(
                user_id="99999999-8888-7777-6666-555555555555",
                tenant_id="11111111-2222-3333-4444-555555555555",
                image_id=img.id,
                role="owner",
            )
        )
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"secret")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404
        assert b"secret" not in r.content

    def test_serve_private_unauthenticated_404(self, client, db_session):
        img, v = _make_private_version(db_session, "n2", "k/n2")

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"secret")):
            r = client.get(f"/serve/{img.id}@{v.id}", follow_redirects=False)
        assert r.status_code == 404
        assert b"secret" not in r.content

    def test_serve_private_service_token_streams(self, client, service_headers, db_session):
        # Service tokens are trusted for everything -- no UserImageLink needed.
        img, v = _make_private_version(db_session, "n3", "k/n3")

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"svc-bytes")):
            r = client.get(
                f"/serve/{img.id}@{v.id}", headers=service_headers, follow_redirects=False
            )
        assert r.status_code == 200
        assert r.content == b"svc-bytes"

    def test_serve_public_anonymous_200(self, client, db_session):
        # Public versions stay served to anonymous callers via /serve.
        img = Image(sha256="n4" * 32, bytes=100, mime="image/jpeg", storage_key="k/n4")
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
            storage_key="k/n4",
            visibility="public",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"pub-bytes")):
            r = client.get(f"/serve/{img.id}@{v.id}", follow_redirects=False)
        assert r.status_code == 200
        assert r.content == b"pub-bytes"

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
    def test_serve_sets_cache_headers(self, client, auth_headers, db_session, link_image_to_user):
        img, v = _make_private_version(db_session, "v1", "k/v1")
        link_image_to_user(img.id)
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"data")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)

        assert r.status_code == 200
        assert r.headers.get("Cache-Control") == "private, max-age=600"
        assert r.headers.get("ETag") == "v1" * 32


class TestSoftDeleteAndCacheVariance:
    """Revocation and cache-correctness on the byte-stream serve paths."""

    def test_serve_soft_deleted_version_404(self, client, auth_headers, db_session):
        img = Image(sha256="d1" * 32, bytes=100, mime="image/jpeg", storage_key="k/d1")
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
            storage_key="k/d1",
            visibility="public",
            age_rating=0,
            deleted_at=dt.datetime.now(dt.UTC),
        )
        db_session.add(v)
        db_session.commit()

        # A soft-deleted (revoked) version must not stream, even though it's public.
        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"revoked")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404

    def test_public_serve_soft_deleted_version_404(self, client, db_session):
        img = Image(sha256="d2" * 32, bytes=100, mime="image/jpeg", storage_key="k/d2")
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
            storage_key="k/d2",
            visibility="public",
            age_rating=0,
            deleted_at=dt.datetime.now(dt.UTC),
        )
        db_session.add(v)
        db_session.commit()

        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"revoked")):
            r = client.get(f"/public/{img.id}@{v.id}", follow_redirects=False)
        assert r.status_code == 404

    def test_serve_varies_on_safe_mode_header(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        img, v = _make_private_version(db_session, "d3", "k/d3")
        link_image_to_user(img.id)
        db_session.commit()

        # /serve's body depends on the x-safe-mode request header, so a cache
        # must key on it or it will replay one variant for the other.
        with patch("app.routes.serve_routes.get_s3", return_value=_mock_s3(b"data")):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 200
        assert "x-safe-mode" in r.headers.get("Vary", "").lower()
