"""Tests for the image upload flow: initiate -> complete -> detail."""

from __future__ import annotations

import hashlib
import io
from unittest.mock import MagicMock, patch

from app.models import Image, ImageVersion, UserImageLink
from botocore.exceptions import ClientError

#: complete_upload proves possession by hashing the caller's staging object,
#: so tests patch the s3 client in the route module and control the bytes.
_S3 = "app.routes.image_routes.get_s3"
_SERVE_S3 = "app.routes.serve_routes.get_s3"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _staging_s3(data: bytes) -> MagicMock:
    """Mock S3 whose get_object streams `data` -- a fresh stream per call so
    repeated completes each hash the full object."""
    mock_s3 = MagicMock()

    def _get_object(**kwargs: object) -> dict[str, object]:
        return {"Body": io.BytesIO(data)}

    mock_s3.get_object.side_effect = _get_object
    return mock_s3


def _missing_staging_s3() -> MagicMock:
    """Mock S3 whose get_object raises NoSuchKey -- no staging object."""
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
        "GetObject",
    )
    return mock_s3


def _patch_upload_s3(mock_s3: MagicMock):
    """Patch the s3 client used by complete_upload. create=True: on the
    pre-fix (vulnerable) module the attribute did not exist at all -- the
    handler never touched S3 -- and the RED run had to demonstrate the live
    exploit, not an AttributeError. If the import style ever changes, the
    real client's connection failure still fails these tests loudly."""
    return patch(_S3, return_value=mock_s3, create=True)


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
        data = b"upload-bytes-a"
        sha = _sha(data)
        with _patch_upload_s3(_staging_s3(data)):
            r = client.post(
                "/images/complete",
                json={"sha256": sha, "key": "uploads/k", "mime": "image/jpeg", "size": len(data)},
                headers=auth_headers,
            )
        assert r.status_code == 200
        body = r.json()
        assert body["created"] is True
        img = db_session.get(Image, body["image_id"])
        assert img is not None
        assert img.sha256 == sha

    def test_idempotent_by_sha256(self, client, auth_headers):
        data = b"upload-bytes-b"
        sha = _sha(data)
        payload = {"sha256": sha, "key": "uploads/k", "mime": "image/jpeg", "size": len(data)}
        with _patch_upload_s3(_staging_s3(data)):
            r1 = client.post("/images/complete", json=payload, headers=auth_headers)
            r2 = client.post("/images/complete", json=payload, headers=auth_headers)
        assert r1.json()["image_id"] == r2.json()["image_id"]
        assert r1.json()["created"] is True
        assert r2.json()["created"] is False

    def test_creates_user_image_link(self, client, auth_headers, db_session):
        data = b"upload-bytes-c"
        with _patch_upload_s3(_staging_s3(data)):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": _sha(data),
                    "key": "uploads/k",
                    "mime": "image/jpeg",
                    "size": len(data),
                },
                headers=auth_headers,
            )
        image_id = r.json()["image_id"]
        link = db_session.query(UserImageLink).filter_by(image_id=image_id).first()
        assert link is not None
        assert link.role == "owner"

    def test_hyphenless_subject_gets_ownership_link(self, client, make_auth_headers, db_session):
        """A user whose subject has no hyphen (a valid 32-hex-char UUID) must
        still get an ownership link on complete -- the old `"-" in subject`
        heuristic locked such users out of every newly-tightened path
        (get/serve their own image)."""
        subject = "aaaaaaaabbbbccccddddeeeeeeeeeeee"  # valid UUID, no hyphens
        headers = make_auth_headers(subject=subject, tenant_id=None)
        data = b"upload-bytes-e"
        with _patch_upload_s3(_staging_s3(data)):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": _sha(data),
                    "key": "uploads/k",
                    "mime": "image/jpeg",
                    "size": len(data),
                },
                headers=headers,
            )
        image_id = r.json()["image_id"]
        link = db_session.query(UserImageLink).filter_by(image_id=image_id).first()
        assert link is not None
        # The UUID column normalizes to canonical (hyphenated) form on store.
        assert str(link.user_id).replace("-", "") == subject
        assert link.role == "owner"
        # The owner can now read back their own (private) image -- proves the
        # link actually resolves through the ownership gate for this subject.
        r = client.get(f"/images/{image_id}", headers=headers)
        assert r.status_code == 200

    def test_service_token_gets_no_ownership_link(self, client, service_headers, db_session):
        """Service tokens own nothing and need no link -- they are trusted
        globally, so no UserImageLink should be created for them."""
        data = b"upload-bytes-f"
        with _patch_upload_s3(_staging_s3(data)):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": _sha(data),
                    "key": "uploads/k",
                    "mime": "image/jpeg",
                    "size": len(data),
                },
                headers=service_headers,
            )
        image_id = r.json()["image_id"]
        link = db_session.query(UserImageLink).filter_by(image_id=image_id).first()
        assert link is None


class TestCompleteUploadProofOfPossession:
    """complete_upload is the ownership-minting primitive: it must never
    create a UserImageLink (nor register an Image) for a caller who cannot
    prove possession of bytes hashing to the sha256 they claim. The sha256
    of any image leaks as its ETag on /serve and /public, so without a
    synchronous possession proof anyone could replay a victim's hash with an
    arbitrary staging key and mint themselves an owner link (the async
    verify task only LOGS on mismatch -- it never revokes the link)."""

    def _make_victim_image(self, db_session, data: bytes, storage_key: str):
        """Register a victim-owned image + private v1 directly in the DB."""
        img = Image(sha256=_sha(data), bytes=len(data), mime="image/jpeg", storage_key=storage_key)
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=len(data),
            storage_key=storage_key,
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.flush()
        db_session.add(
            UserImageLink(
                user_id="99999999-8888-7777-6666-555555555555",
                tenant_id="11111111-2222-3333-4444-555555555555",
                image_id=img.id,
                role="owner",
            )
        )
        db_session.commit()
        return img, v

    def test_mismatched_sha_rejected_no_image_no_link(self, client, auth_headers, db_session):
        claimed_sha = "a" * 64  # does NOT match the staging bytes below
        with _patch_upload_s3(_staging_s3(b"not-those-bytes")):
            r = client.post(
                "/images/complete",
                json={"sha256": claimed_sha, "key": "uploads/x", "mime": "image/jpeg", "size": 15},
                headers=auth_headers,
            )
        assert r.status_code == 400
        # No Image registered, no ownership link minted.
        assert db_session.query(Image).filter_by(sha256=claimed_sha).first() is None
        assert db_session.query(UserImageLink).first() is None

    def test_missing_staging_object_rejected_no_image_no_link(
        self, client, auth_headers, db_session
    ):
        claimed_sha = "b" * 64
        with _patch_upload_s3(_missing_staging_s3()):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": claimed_sha,
                    "key": "uploads/never-uploaded",
                    "mime": "image/jpeg",
                    "size": 10,
                },
                headers=auth_headers,
            )
        assert r.status_code == 404
        assert db_session.query(Image).filter_by(sha256=claimed_sha).first() is None
        assert db_session.query(UserImageLink).first() is None

    def test_attacker_cannot_claim_victim_image_via_leaked_sha(
        self, client, auth_headers, db_session
    ):
        """The keystone exploit: the victim's sha256 leaks as an ETag; the
        attacker replays it on /images/complete with a 1-byte staging object
        and must NOT receive a co-owner link on the victim's image."""
        victim_data = b"victim-original-bytes"
        img, v = self._make_victim_image(db_session, victim_data, "k/victim")

        with _patch_upload_s3(_staging_s3(b"x")):  # 1 byte, wrong hash
            r = client.post(
                "/images/complete",
                json={
                    "sha256": img.sha256,
                    "key": "uploads/attacker-staging",
                    "mime": "image/jpeg",
                    "size": 1,
                },
                headers=auth_headers,
            )
        assert r.status_code == 400
        attacker_links = (
            db_session.query(UserImageLink)
            .filter_by(image_id=img.id, user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            .all()
        )
        assert attacker_links == []

        # And the attacker still cannot read the victim's image or stream its
        # private version -- 404, indistinguishable from nonexistent.
        r = client.get(f"/images/{img.id}", headers=auth_headers)
        assert r.status_code == 404
        with patch(_SERVE_S3, return_value=_staging_s3(victim_data)):
            r = client.get(f"/serve/{img.id}@{v.id}", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 404
        assert victim_data not in r.content

    def test_legit_uploader_matching_hash_gets_link_and_serves(
        self, client, auth_headers, db_session
    ):
        """The legit flow keeps working: staging bytes hash to the claimed
        sha256 -> image registered, owner link minted, private version
        streams back to the uploader."""
        data = b"legit-uploaded-bytes"
        with _patch_upload_s3(_staging_s3(data)):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": _sha(data),
                    "key": "uploads/legit",
                    "mime": "image/jpeg",
                    "size": len(data),
                },
                headers=auth_headers,
            )
        assert r.status_code == 200
        image_id = r.json()["image_id"]
        link = (
            db_session.query(UserImageLink)
            .filter_by(image_id=image_id, user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            .first()
        )
        assert link is not None
        assert link.role == "owner"

        # The worker (mocked in tests) would create v1; create it directly.
        v = ImageVersion(
            image_id=image_id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=len(data),
            storage_key="uploads/legit",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        with patch(_SERVE_S3, return_value=_staging_s3(data)):
            r = client.get(
                f"/serve/{image_id}@{v.id}", headers=auth_headers, follow_redirects=False
            )
        assert r.status_code == 200
        assert r.content == data

    def test_dedup_hit_requires_possession_then_grants_co_owner_link(
        self, client, auth_headers, db_session
    ):
        """Dedup path: the Image row already exists (another user uploaded the
        same content). A second caller gets the co-owner link ONLY because
        their own staging object hashes to the same sha256."""
        shared_data = b"identical-shared-content"
        img, _v = self._make_victim_image(db_session, shared_data, "k/shared")

        with _patch_upload_s3(_staging_s3(shared_data)):
            r = client.post(
                "/images/complete",
                json={
                    "sha256": img.sha256,
                    "key": "uploads/second-copy",
                    "mime": "image/jpeg",
                    "size": len(shared_data),
                },
                headers=auth_headers,
            )
        assert r.status_code == 200
        assert r.json()["created"] is False
        link = (
            db_session.query(UserImageLink)
            .filter_by(image_id=img.id, user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            .first()
        )
        assert link is not None


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
