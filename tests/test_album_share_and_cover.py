"""Tests for album CRUD, sharing, and cover generation."""

from app.models import Album, AlbumItem, Image, ImageVersion


class TestAlbumCRUD:
    def test_create_album(self, client, auth_headers):
        r = client.post("/albums", json={"title": "Vacation"}, headers=auth_headers)
        assert r.status_code == 200
        assert "id" in r.json()

    def test_get_album(self, client, auth_headers, db_session):
        album = Album(title="Test", default_visibility="private")
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Test"
        assert data["items"] == []

    def test_update_album(self, client, auth_headers, db_session):
        album = Album(title="Old", default_visibility="private")
        db_session.add(album)
        db_session.commit()

        r = client.put(f"/albums/{album.id}", json={"title": "New"}, headers=auth_headers)
        assert r.status_code == 200
        db_session.refresh(album)
        assert album.title == "New"

    def test_get_missing_album_404(self, client, auth_headers):
        r = client.get("/albums/99999", headers=auth_headers)
        assert r.status_code == 404


class TestAlbumItems:
    def test_add_item(self, client, auth_headers, db_session):
        album = Album(title="Album")
        img = Image(sha256="e" * 64, bytes=50, mime="image/jpeg", storage_key="k/e")
        db_session.add_all([album, img])
        db_session.commit()

        r = client.post(
            f"/albums/{album.id}/items",
            json={"image_id": img.id},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["position"] == 0

    def test_add_multiple_items_auto_position(self, client, auth_headers, db_session):
        album = Album(title="Album")
        img1 = Image(sha256="f" * 64, bytes=50, mime="image/jpeg", storage_key="k/f")
        img2 = Image(sha256="0" * 64, bytes=50, mime="image/jpeg", storage_key="k/0")
        db_session.add_all([album, img1, img2])
        db_session.commit()

        r1 = client.post(f"/albums/{album.id}/items", json={"image_id": img1.id}, headers=auth_headers)
        r2 = client.post(f"/albums/{album.id}/items", json={"image_id": img2.id}, headers=auth_headers)
        assert r1.json()["position"] == 0
        assert r2.json()["position"] == 1

    def test_reorder_items(self, client, auth_headers, db_session):
        album = Album(title="Album")
        img = Image(sha256="11" * 32, bytes=50, mime="image/jpeg", storage_key="k/11")
        db_session.add_all([album, img])
        db_session.commit()

        # Add item at position 0
        client.post(f"/albums/{album.id}/items", json={"image_id": img.id, "position": 0}, headers=auth_headers)

        # Reorder: move position 0 to position 5
        r = client.put(
            f"/albums/{album.id}/items/reorder",
            json={"items": [{"from_position": 0, "to_position": 5}]},
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestAlbumSharing:
    def test_enable_sharing(self, client, auth_headers, db_session):
        album = Album(title="Share Me")
        db_session.add(album)
        db_session.commit()

        r = client.post(f"/albums/{album.id}/share", json={"enable": True}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["share_url"] is not None
        assert data["share_url"].startswith("/p/albums/")

    def test_disable_sharing(self, client, auth_headers, db_session):
        album = Album(title="Unshare")
        db_session.add(album)
        db_session.commit()

        # Enable first
        client.post(f"/albums/{album.id}/share", json={"enable": True}, headers=auth_headers)
        # Disable
        r = client.post(f"/albums/{album.id}/share", json={"enable": False}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["share_url"] is None

    def test_share_with_age_threshold(self, client, auth_headers, db_session):
        album = Album(title="Rated")
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
        album = Album(title="Cover Test")
        db_session.add(album)
        db_session.commit()

        r = client.get(f"/albums/cover/{album.id}", headers=auth_headers)
        assert r.status_code == 200
        assert "storage_key" in r.json()
