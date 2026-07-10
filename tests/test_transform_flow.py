"""Tests for image version creation, visibility, safe-alt, and external refs."""

from app.models import ExternalRef, Image, ImageVersion
from sqlalchemy import select


class TestCreateVersion:
    def test_create_version_from_base(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="a1" * 32, bytes=100, mime="image/jpeg", storage_key="k/a1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v1 = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a1",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v1)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions",
            json={"transform_spec": {"resize": {"width": 100}}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["version_no"] == 2
        assert "storage_key" in data

    def test_create_version_explicit_base(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        img = Image(sha256="a2" * 32, bytes=100, mime="image/jpeg", storage_key="k/a2")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v1 = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a2",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v1)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions",
            json={"transform_spec": {"blur": {"sigma": 5}}, "base_version_id": v1.id},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["version_no"] == 2

    def test_create_version_foreign_base_404(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """base_version_id pointing at another owner's image must 404 --
        otherwise a caller could launder someone else's bytes into an image
        they own."""
        img_a = Image(sha256="a4" * 32, bytes=100, mime="image/jpeg", storage_key="k/a4")
        img_b = Image(sha256="a5" * 32, bytes=100, mime="image/jpeg", storage_key="k/a5")
        db_session.add_all([img_a, img_b])
        db_session.flush()
        link_image_to_user(img_a.id)  # caller owns A only
        v_a = ImageVersion(
            image_id=img_a.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a4",
            visibility="private",
            age_rating=0,
        )
        v_b = ImageVersion(
            image_id=img_b.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a5",
            visibility="private",
            age_rating=0,
        )
        db_session.add_all([v_a, v_b])
        db_session.commit()

        r = client.post(
            f"/images/{img_a.id}/versions",
            json={"transform_spec": {}, "base_version_id": v_b.id},
            headers=auth_headers,
        )
        assert r.status_code == 404
        # No version was created on image A beyond the original.
        versions = (
            db_session.execute(select(ImageVersion).where(ImageVersion.image_id == img_a.id))
            .scalars()
            .all()
        )
        assert len(versions) == 1

    def test_create_version_nonexistent_base_404(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """An explicitly-specified base_version_id that does not exist must
        404 exactly like an unowned one -- silently falling back to the
        image's first version would let an attacker distinguish existing
        (404) from nonexistent (200) version ids."""
        img = Image(sha256="a6" * 32, bytes=100, mime="image/jpeg", storage_key="k/a6")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v1 = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a6",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v1)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions",
            json={"transform_spec": {}, "base_version_id": 999999},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_create_version_foreign_safe_alt_404(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """create_safe_alt_for pointing at a version of an image the caller
        does not own must 404 -- otherwise the stored alt_for_version_id lets
        safe-mode /serve stream the victim's bytes (alt-swap IDOR)."""
        img_a = Image(sha256="a7" * 32, bytes=100, mime="image/jpeg", storage_key="k/a7")
        img_b = Image(sha256="a8" * 32, bytes=100, mime="image/jpeg", storage_key="k/a8")
        db_session.add_all([img_a, img_b])
        db_session.flush()
        link_image_to_user(img_a.id)  # caller owns A only
        v_a = ImageVersion(
            image_id=img_a.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a7",
            visibility="private",
            age_rating=0,
        )
        v_b = ImageVersion(
            image_id=img_b.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a8",
            visibility="private",
            age_rating=0,
        )
        db_session.add_all([v_a, v_b])
        db_session.commit()

        r = client.post(
            f"/images/{img_a.id}/versions",
            json={"transform_spec": {}, "create_safe_alt_for": v_b.id},
            headers=auth_headers,
        )
        assert r.status_code == 404
        # No version carrying the poisoned alt pointer was created.
        versions = (
            db_session.execute(select(ImageVersion).where(ImageVersion.image_id == img_a.id))
            .scalars()
            .all()
        )
        assert len(versions) == 1

    def test_create_version_nonexistent_safe_alt_404(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """A create_safe_alt_for that does not exist must 404 exactly like an
        unowned one -- same no-oracle rule as base_version_id."""
        img = Image(sha256="a9" * 32, bytes=100, mime="image/jpeg", storage_key="k/a9")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v1 = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/a9",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v1)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions",
            json={"transform_spec": {}, "create_safe_alt_for": 999999},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_create_version_own_safe_alt_ok(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """A create_safe_alt_for referencing the caller's own version still
        works -- the ownership gate must not break the legit safe-alt flow."""
        img = Image(sha256="aa" * 32, bytes=100, mime="image/jpeg", storage_key="k/aa")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v1 = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/aa",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v1)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions",
            json={"transform_spec": {}, "create_safe_alt_for": v1.id},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["version_no"] == 2

    def test_create_version_no_base_400(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="a3" * 32, bytes=100, mime="image/jpeg", storage_key="k/a3")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()
        # No versions exist
        r = client.post(
            f"/images/{img.id}/versions",
            json={"transform_spec": {}},
            headers=auth_headers,
        )
        assert r.status_code == 400


class TestSetVisibility:
    def test_set_visibility(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="b1" * 32, bytes=100, mime="image/jpeg", storage_key="k/b1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/b1",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions/{v.id}/visibility",
            json={"visibility": "public"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        db_session.refresh(v)
        assert v.visibility == "public"

    def test_set_visibility_404_wrong_image(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        img = Image(sha256="b2" * 32, bytes=100, mime="image/jpeg", storage_key="k/b2")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/b2",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # Wrong image_id
        r = client.post(
            f"/images/99999/versions/{v.id}/visibility",
            json={"visibility": "public"},
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestExposeSafeAlt:
    def test_expose_safe_alt(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="c1" * 32, bytes=100, mime="image/jpeg", storage_key="k/c1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=100,
            height=100,
            bytes=100,
            storage_key="k/c1",
            visibility="private",
            age_rating=18,
        )
        db_session.add(v)
        db_session.commit()

        r = client.post(
            f"/images/{img.id}/versions/{v.id}/expose-safe-alt",
            json={"age_rating": 0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["alt_for"] == v.id
        assert "version_id" in data


class TestExternalRefs:
    def test_create_and_lookup(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="d1" * 32, bytes=100, mime="image/jpeg", storage_key="k/d1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/d1",
            visibility="public",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # Create ref
        r = client.post(
            "/external/refs",
            json={"ref_type": "post", "ref_id": "post-42", "image_id": img.id, "version_id": v.id},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "id" in r.json()

        # Lookup
        r = client.get(
            "/external/assets/by-external-ref?ref_type=post&ref_id=post-42",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["image_id"] == img.id
        assert data["version_id"] == v.id
        assert "url" in data

    def _make_ref(self, db_session, *, sha_seed, key, ref_id, visibility, tenant_id=None):
        img = Image(sha256=sha_seed * 32, bytes=100, mime="image/jpeg", storage_key=key)
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key=key,
            visibility=visibility,
            age_rating=0,
        )
        db_session.add(v)
        db_session.flush()
        er = ExternalRef(
            ref_type="post", ref_id=ref_id, image_id=img.id, version_id=v.id, tenant_id=tenant_id
        )
        db_session.add(er)
        db_session.commit()
        return img, v

    def test_null_tenant_ref_unowned_image_404(self, client, auth_headers, db_session):
        """A null-tenant ref must NOT fail open: without a UserImageLink the
        caller gets 404 and no presigned URL."""
        self._make_ref(db_session, sha_seed="e1", key="k/e1", ref_id="nt-1", visibility="private")

        r = client.get(
            "/external/assets/by-external-ref?ref_type=post&ref_id=nt-1",
            headers=auth_headers,
        )
        assert r.status_code == 404
        assert "url" not in r.json()

    def test_null_tenant_ref_owner_gets_private_url(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """Real ownership must flow into can_view_version: the owner of the
        image behind a null-tenant ref may resolve its private version."""
        img, v = self._make_ref(
            db_session, sha_seed="e2", key="k/e2", ref_id="nt-2", visibility="private"
        )
        link_image_to_user(img.id)
        db_session.commit()

        r = client.get(
            "/external/assets/by-external-ref?ref_type=post&ref_id=nt-2",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["url"]

    def test_tenant_ref_private_version_unowned_404(self, client, auth_headers, db_session):
        """Even with a matching ref tenant, a private version the caller does
        not own must never yield a presigned URL."""
        self._make_ref(
            db_session,
            sha_seed="e3",
            key="k/e3",
            ref_id="tn-1",
            visibility="private",
            tenant_id="11111111-2222-3333-4444-555555555555",
        )

        r = client.get(
            "/external/assets/by-external-ref?ref_type=post&ref_id=tn-1",
            headers=auth_headers,
        )
        assert r.status_code == 404
        assert "url" not in r.json()

    def test_create_ref_unowned_image_404(self, client, auth_headers, db_session):
        """create_external_ref for an image the caller does not own must 404 --
        otherwise an attacker registers a ref against a victim's image."""
        img = Image(sha256="f1" * 32, bytes=100, mime="image/jpeg", storage_key="k/f1")
        db_session.add(img)
        db_session.flush()
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/f1",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.post(
            "/external/refs",
            json={"ref_type": "post", "ref_id": "f1", "image_id": img.id, "version_id": v.id},
            headers=auth_headers,
        )
        assert r.status_code == 404
        # No ref was persisted.
        assert (
            db_session.execute(select(ExternalRef).where(ExternalRef.ref_id == "f1"))
            .scalar_one_or_none()
            is None
        )

    def test_create_ref_version_mismatch_404(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """A version_id whose image_id != the ref's image_id must 404 even when
        the caller owns the ref image -- that decoupling is the read-side IDOR
        vector (ref image A, version V of victim's B)."""
        img_a = Image(sha256="f2" * 32, bytes=100, mime="image/jpeg", storage_key="k/f2")
        img_b = Image(sha256="f3" * 32, bytes=100, mime="image/jpeg", storage_key="k/f3")
        db_session.add_all([img_a, img_b])
        db_session.flush()
        link_image_to_user(img_a.id)  # caller owns A only
        v_a = ImageVersion(
            image_id=img_a.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/f2",
            visibility="private",
            age_rating=0,
        )
        v_b = ImageVersion(
            image_id=img_b.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/f3",
            visibility="private",
            age_rating=0,
        )
        db_session.add_all([v_a, v_b])
        db_session.commit()

        r = client.post(
            "/external/refs",
            json={"ref_type": "post", "ref_id": "f2", "image_id": img_a.id, "version_id": v_b.id},
            headers=auth_headers,
        )
        assert r.status_code == 404
        assert (
            db_session.execute(select(ExternalRef).where(ExternalRef.ref_id == "f2"))
            .scalar_one_or_none()
            is None
        )

    def test_stored_mismatched_ref_lookup_404_no_url(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        """Defence in depth on the READ side: a ref whose stored version_id
        belongs to a different image than image_id must 404 and leak no
        presigned URL, even for a caller who owns the ref's image."""
        img_a = Image(sha256="f4" * 32, bytes=100, mime="image/jpeg", storage_key="k/f4")
        img_b = Image(sha256="f5" * 32, bytes=100, mime="image/jpeg", storage_key="k/f5")
        db_session.add_all([img_a, img_b])
        db_session.flush()
        link_image_to_user(img_a.id)  # caller owns A
        v_a = ImageVersion(
            image_id=img_a.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/f4",
            visibility="private",
            age_rating=0,
        )
        v_b = ImageVersion(
            image_id=img_b.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/f5",
            visibility="private",
            age_rating=0,
        )
        db_session.add_all([v_a, v_b])
        db_session.flush()
        # Ref points at image A but version B -- construct directly, bypassing
        # the create-side guard, to prove the read side is independently safe.
        er = ExternalRef(
            ref_type="post", ref_id="f4", image_id=img_a.id, version_id=v_b.id, tenant_id=None
        )
        db_session.add(er)
        db_session.commit()

        r = client.get(
            "/external/assets/by-external-ref?ref_type=post&ref_id=f4",
            headers=auth_headers,
        )
        assert r.status_code == 404
        assert "url" not in r.json()

    def test_owned_ref_resolves(self, client, auth_headers, db_session, link_image_to_user):
        """A legit owned ref still resolves to a presigned URL through the
        create+read endpoints."""
        img = Image(sha256="f6" * 32, bytes=100, mime="image/jpeg", storage_key="k/f6")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/f6",
            visibility="private",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.post(
            "/external/refs",
            json={"ref_type": "post", "ref_id": "f6", "image_id": img.id, "version_id": v.id},
            headers=auth_headers,
        )
        assert r.status_code == 200

        r = client.get(
            "/external/assets/by-external-ref?ref_type=post&ref_id=f6",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["url"]

    def test_lookup_missing_ref_404(self, client, auth_headers):
        r = client.get(
            "/external/assets/by-external-ref?ref_type=nope&ref_id=nope",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_create_ref_without_version(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        img = Image(sha256="d2" * 32, bytes=100, mime="image/jpeg", storage_key="k/d2")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id,
            version_no=1,
            transform_spec={},
            mime="image/jpeg",
            width=200,
            height=200,
            bytes=100,
            storage_key="k/d2",
            visibility="public",
            age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        # Create ref without specifying version_id
        r = client.post(
            "/external/refs",
            json={"ref_type": "comment", "ref_id": "c-1", "image_id": img.id},
            headers=auth_headers,
        )
        assert r.status_code == 200

        # Lookup should still work (falls back to first version)
        r = client.get(
            "/external/assets/by-external-ref?ref_type=comment&ref_id=c-1",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["version_id"] == v.id
