"""Tests for the image upload flow: initiate -> complete -> detail."""

from app.models import Image, ImageVersion, UserImageLink


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


class TestInitiateUpload:
    def test_returns_presigned_fields(self, client, auth_headers):
        r = client.post(
            "/images/initiate-upload",
            json={"filename": "photo.jpg", "mime": "image/jpeg", "size": 2048},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "url" in data
        assert "fields" in data
        assert "staging_key" in data
        assert "bucket" in data
        assert data["staging_key"].startswith("uploads/")

    def test_different_calls_get_different_staging_keys(self, client, auth_headers):
        payload = {"filename": "a.jpg", "mime": "image/jpeg", "size": 100}
        r1 = client.post("/images/initiate-upload", json=payload, headers=auth_headers)
        r2 = client.post("/images/initiate-upload", json=payload, headers=auth_headers)
        assert r1.json()["staging_key"] != r2.json()["staging_key"]


class TestCompleteUpload:
    def test_creates_image_record(self, client, auth_headers, db_session):
        sha = "a" * 64
        r = client.post(
            "/images/complete",
            json={"sha256": sha, "key": "uploads/k", "mime": "image/jpeg", "size": 1024},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["created"] is True
        img = db_session.get(Image, data["image_id"])
        assert img is not None
        assert img.sha256 == sha

    def test_idempotent_by_sha256(self, client, auth_headers):
        sha = "b" * 64
        payload = {"sha256": sha, "key": "uploads/k", "mime": "image/jpeg", "size": 512}
        r1 = client.post("/images/complete", json=payload, headers=auth_headers)
        r2 = client.post("/images/complete", json=payload, headers=auth_headers)
        assert r1.json()["image_id"] == r2.json()["image_id"]
        assert r1.json()["created"] is True
        assert r2.json()["created"] is False

    def test_creates_user_image_link(self, client, auth_headers, db_session):
        sha = "c" * 64
        r = client.post(
            "/images/complete",
            json={"sha256": sha, "key": "uploads/k", "mime": "image/jpeg", "size": 512},
            headers=auth_headers,
        )
        image_id = r.json()["image_id"]
        link = db_session.query(UserImageLink).filter_by(image_id=image_id).first()
        assert link is not None
        assert link.role == "owner"


class TestGetImage:
    def test_returns_image_with_versions(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        img = Image(sha256="d" * 64, bytes=100, mime="image/png", storage_key="k/1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/png",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/1",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.get(f"/images/{img.id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == img.id
        assert data["sha256"] == "d" * 64
        assert len(data["versions"]) == 1
        assert data["versions"][0]["version_no"] == 1

    def test_404_for_missing(self, client, auth_headers):
        r = client.get("/images/99999", headers=auth_headers)
        assert r.status_code == 404
