"""Tests for image version creation, visibility, safe-alt, and external refs."""

from app.models import Image, ImageVersion


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
        from sqlalchemy import select

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
    def test_create_and_lookup(self, client, auth_headers, db_session):
        img = Image(sha256="d1" * 32, bytes=100, mime="image/jpeg", storage_key="k/d1")
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

    def test_lookup_missing_ref_404(self, client, auth_headers):
        r = client.get(
            "/external/assets/by-external-ref?ref_type=nope&ref_id=nope",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_create_ref_without_version(self, client, auth_headers, db_session):
        img = Image(sha256="d2" * 32, bytes=100, mime="image/jpeg", storage_key="k/d2")
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
