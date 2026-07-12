"""Tests for album CRUD, sharing, cover generation, and access control."""

from app.models import Album, AlbumItem, Image
from sqlalchemy import select

_USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_TENANT_ID = "11111111-2222-3333-4444-555555555555"


def _own_album(title: str, **kw) -> Album:
    """An album the auth_headers user may access (tenant match)."""
    return Album(title=title, tenant_id=_TENANT_ID, owner_user_id=_USER_ID, **kw)


class TestAlbumCRUD:
    def test_create_album(self, client, auth_headers):
        r = client.post("/albums", json={"title": "Vacation"}, headers=auth_headers)
        assert r.status_code == 200
        assert "id" in r.json()

    def test_get_album(self, client, auth_headers, db_session):
        album = _own_album("Test", default_visibility="private")
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Test"
        assert data["items"] == []

    def test_update_album(self, client, auth_headers, db_session):
        album = _own_album("Old", default_visibility="private")
        db_session.add(album)
        db_session.commit()

        r = client.put(f"/albums/{album.id}", json={"title": "New"}, headers=auth_headers)
        assert r.status_code == 200
        db_session.refresh(album)
        assert album.title == "New"

    def test_get_missing_album_404(self, client, auth_headers):
        r = client.get("/albums/99999", headers=auth_headers)
        assert r.status_code == 404


class TestAlbumAccessControl:
    """A null-tenant album must fail CLOSED: only a service token, a tenant
    match, or an owner match grants access -- an unrelated caller gets 404."""

    def test_null_tenant_album_404_for_unrelated_caller(self, client, auth_headers, db_session):
        album = Album(title="Orphan", tenant_id=None, owner_user_id=None)
        img = Image(sha256="ac" * 32, bytes=50, mime="image/jpeg", storage_key="k/ac")
        db_session.add_all([album, img])
        db_session.commit()

        assert client.get(f"/albums/{album.id}", headers=auth_headers).status_code == 404
        assert (
            client.put(f"/albums/{album.id}", json={"title": "x"}, headers=auth_headers).status_code
            == 404
        )
        assert (
            client.post(
                f"/albums/{album.id}/items", json={"image_id": img.id}, headers=auth_headers
            ).status_code
            == 404
        )
        assert (
            client.put(
                f"/albums/{album.id}/items/reorder",
                json={"items": [{"from_position": 0, "to_position": 1}]},
                headers=auth_headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/albums/{album.id}/share", json={"enable": True}, headers=auth_headers
            ).status_code
            == 404
        )
        assert client.get(f"/albums/cover/{album.id}", headers=auth_headers).status_code == 404
        assert client.delete(f"/albums/{album.id}", headers=auth_headers).status_code == 404

    def test_other_tenant_album_404(self, client, auth_headers, db_session):
        album = Album(title="Foreign", tenant_id="99999999-8888-7777-6666-555555555555")
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 404

    def test_owner_can_access_null_tenant_album(self, client, auth_headers, db_session):
        album = Album(title="Personal", tenant_id=None, owner_user_id=_USER_ID)
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["title"] == "Personal"

    def test_service_can_access_null_tenant_album(self, client, service_headers, db_session):
        album = Album(title="Svc Visible", tenant_id=None, owner_user_id=None)
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/{album.id}", headers=service_headers)
        assert r.status_code == 200

    def test_add_item_unowned_image_404(self, client, auth_headers, db_session):
        # Even on an album the caller controls, binding an image they do NOT
        # own must be denied and write nothing.
        album = _own_album("Mine")
        img = Image(sha256="ad" * 32, bytes=50, mime="image/jpeg", storage_key="k/ad")
        db_session.add_all([album, img])
        db_session.commit()

        r = client.post(
            f"/albums/{album.id}/items", json={"image_id": img.id}, headers=auth_headers
        )
        assert r.status_code == 404
        items = (
            db_session.execute(select(AlbumItem).where(AlbumItem.album_id == album.id))
            .scalars()
            .all()
        )
        assert items == []


class TestAlbumItems:
    def test_add_item(self, client, auth_headers, db_session, link_image_to_user):
        album = _own_album("Album")
        img = Image(sha256="e" * 64, bytes=50, mime="image/jpeg", storage_key="k/e")
        db_session.add_all([album, img])
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        r = client.post(
            f"/albums/{album.id}/items",
            json={"image_id": img.id},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["position"] == 0

    def test_add_multiple_items_auto_position(
        self, client, auth_headers, db_session, link_image_to_user
    ):
        album = _own_album("Album")
        img1 = Image(sha256="f" * 64, bytes=50, mime="image/jpeg", storage_key="k/f")
        img2 = Image(sha256="0" * 64, bytes=50, mime="image/jpeg", storage_key="k/0")
        db_session.add_all([album, img1, img2])
        db_session.flush()
        link_image_to_user(img1.id)
        link_image_to_user(img2.id)
        db_session.commit()

        r1 = client.post(
            f"/albums/{album.id}/items", json={"image_id": img1.id}, headers=auth_headers
        )
        r2 = client.post(
            f"/albums/{album.id}/items", json={"image_id": img2.id}, headers=auth_headers
        )
        assert r1.json()["position"] == 0
        assert r2.json()["position"] == 1

    def test_reorder_items(self, client, auth_headers, db_session, link_image_to_user):
        album = _own_album("Album")
        img = Image(sha256="11" * 32, bytes=50, mime="image/jpeg", storage_key="k/11")
        db_session.add_all([album, img])
        db_session.flush()
        link_image_to_user(img.id)
        db_session.commit()

        # Add item at position 0
        client.post(
            f"/albums/{album.id}/items",
            json={"image_id": img.id, "position": 0},
            headers=auth_headers,
        )

        # Reorder: move position 0 to position 5
        r = client.put(
            f"/albums/{album.id}/items/reorder",
            json={"items": [{"from_position": 0, "to_position": 5}]},
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestAlbumSharing:
    def test_enable_sharing(self, client, auth_headers, db_session):
        album = _own_album("Share Me")
        db_session.add(album)
        db_session.commit()

        r = client.post(f"/albums/{album.id}/share", json={"enable": True}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["share_url"] is not None
        assert data["share_url"].startswith("/p/albums/")

    def test_disable_sharing(self, client, auth_headers, db_session):
        album = _own_album("Unshare")
        db_session.add(album)
        db_session.commit()

        # Enable first
        client.post(f"/albums/{album.id}/share", json={"enable": True}, headers=auth_headers)
        # Disable
        r = client.post(f"/albums/{album.id}/share", json={"enable": False}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["share_url"] is None

    def test_share_with_age_threshold(self, client, auth_headers, db_session):
        album = _own_album("Rated")
        db_session.add(album)
        db_session.commit()

        r = client.post(
            f"/albums/{album.id}/share",
            json={"enable": True, "share_age_threshold": 18},
            headers=auth_headers,
        )
        assert r.status_code == 200
        db_session.refresh(album)
        assert album.share_age_threshold == 18


class TestAlbumCover:
    def test_cover_returns_key(self, client, auth_headers, db_session):
        album = _own_album("Cover Test")
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/cover/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        assert "storage_key" in r.json()
