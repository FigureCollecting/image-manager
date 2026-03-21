"""Tests for soft delete: images, albums, and image versions.

TDD: These tests should FAIL initially, then pass after implementing soft delete.
"""

from app.models import Album, Image, ImageVersion


class TestDeleteImage:
    def test_delete_image(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="del1" * 16, bytes=100, mime="image/jpeg", storage_key="k/del1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        r = client.delete(f"/images/{img.id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_deleted_image_not_in_get(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="del2" * 16, bytes=100, mime="image/jpeg", storage_key="k/del2")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        client.delete(f"/images/{img.id}", headers=auth_headers)
        r = client.get(f"/images/{img.id}", headers=auth_headers)
        assert r.status_code == 404

    def test_deleted_image_not_in_search(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="del3" * 16, bytes=100, mime="image/jpeg", storage_key="k/del3")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        client.delete(f"/images/{img.id}", headers=auth_headers)
        r = client.get("/search/images", headers=auth_headers)
        ids = [row["id"] for row in r.json()["results"]]
        assert img.id not in ids

    def test_delete_missing_image_404(self, client, auth_headers):
        r = client.delete("/images/99999", headers=auth_headers)
        assert r.status_code == 404

    def test_delete_is_soft(self, client, auth_headers, db_session, link_image_to_user):
        """The record still exists in DB with deleted_at set."""
        img = Image(sha256="del4" * 16, bytes=100, mime="image/jpeg", storage_key="k/del4")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        client.delete(f"/images/{img.id}", headers=auth_headers)
        db_session.expire_all()
        row = db_session.get(Image, img.id)
        assert row is not None
        assert row.deleted_at is not None


class TestDeleteAlbum:
    def test_delete_album(self, client, auth_headers, db_session):
        album = Album(title="Delete Me")
        db_session.add(album)
        db_session.commit()

        r = client.delete(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_deleted_album_not_in_get(self, client, auth_headers, db_session):
        album = Album(title="Gone")
        db_session.add(album)
        db_session.commit()

        client.delete(f"/albums/{album.id}", headers=auth_headers)
        r = client.get(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 404

    def test_deleted_album_not_in_search(self, client, auth_headers, db_session):
        album = Album(title="UniqueDeleteTest123")
        db_session.add(album)
        db_session.commit()

        client.delete(f"/albums/{album.id}", headers=auth_headers)
        r = client.get("/search/albums?query=UniqueDeleteTest123", headers=auth_headers)
        ids = [row["id"] for row in r.json()["results"]]
        assert album.id not in ids

    def test_delete_missing_album_404(self, client, auth_headers):
        r = client.delete("/albums/99999", headers=auth_headers)
        assert r.status_code == 404


class TestDeleteVersion:
    def test_delete_version(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="dv1" * 22, bytes=100, mime="image/jpeg", storage_key="k/dv1")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/dv1", visibility="private", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        r = client.delete(f"/images/{img.id}/versions/{v.id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_deleted_version_not_in_image_detail(self, client, auth_headers, db_session, link_image_to_user):
        img = Image(sha256="dv2" * 22, bytes=100, mime="image/jpeg", storage_key="k/dv2")
        db_session.add(img)
        db_session.flush()
        link_image_to_user(img.id)
        v = ImageVersion(
            image_id=img.id, version_no=1, transform_spec={}, mime="image/jpeg",
            width=100, height=100, bytes=100, storage_key="k/dv2", visibility="private", age_rating=0,
        )
        db_session.add(v)
        db_session.commit()

        client.delete(f"/images/{img.id}/versions/{v.id}", headers=auth_headers)
        r = client.get(f"/images/{img.id}", headers=auth_headers)
        assert r.status_code == 200
        version_ids = [ver["id"] for ver in r.json()["versions"]]
        assert v.id not in version_ids
