"""Tests for search endpoints and tag management."""

from app.models import Album, Image, Tag


class TestCreateTag:
    def test_create_global_tag(self, client, auth_headers):
        r = client.post(
            "/tags", json={"name": "landscape", "scope": "global"}, headers=auth_headers
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "landscape"
        assert "id" in data

    def test_create_tenant_tag(self, client, auth_headers):
        r = client.post(
            "/tags",
            json={
                "name": "internal",
                "scope": "tenant",
                "tenant_id": "11111111-2222-3333-4444-555555555555",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestTagImage:
    def test_tag_image_by_id(self, client, auth_headers, db_session):
        img = Image(sha256="aa" * 32, bytes=50, mime="image/jpeg", storage_key="k/aa")
        tag = Tag(name="sunset", scope="global")
        db_session.add_all([img, tag])
        db_session.commit()

        r = client.post(
            f"/tags/images/{img.id}",
            json={"tag_ids": [tag.id]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_tag_image_by_name(self, client, auth_headers, db_session):
        img = Image(sha256="bb" * 32, bytes=50, mime="image/jpeg", storage_key="k/bb")
        tag = Tag(name="nature", scope="global")
        db_session.add_all([img, tag])
        db_session.commit()

        r = client.post(
            f"/tags/images/{img.id}",
            json={"tag_ids": [], "names": ["nature"]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_tag_image_deduplicates(self, client, auth_headers, db_session):
        img = Image(sha256="cc" * 32, bytes=50, mime="image/jpeg", storage_key="k/cc")
        tag = Tag(name="dup", scope="global")
        db_session.add_all([img, tag])
        db_session.commit()

        # Tag twice
        client.post(f"/tags/images/{img.id}", json={"tag_ids": [tag.id]}, headers=auth_headers)
        r = client.post(f"/tags/images/{img.id}", json={"tag_ids": [tag.id]}, headers=auth_headers)
        assert r.status_code == 200


class TestTagAlbum:
    def test_tag_album(self, client, auth_headers, db_session):
        album = Album(title="Tagged Album")
        tag = Tag(name="travel", scope="global")
        db_session.add_all([album, tag])
        db_session.commit()

        r = client.post(
            f"/tags/albums/{album.id}",
            json={"tag_ids": [tag.id]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["count"] == 1


class TestSearchImages:
    def test_search_by_mime(self, client, auth_headers, db_session):
        img = Image(sha256="dd" * 32, bytes=50, mime="image/png", storage_key="k/dd")
        db_session.add(img)
        db_session.commit()

        r = client.get("/search/images?query=png", headers=auth_headers)
        assert r.status_code == 200
        results = r.json()["results"]
        assert any(row["id"] == img.id for row in results)

    def test_search_no_results(self, client, auth_headers):
        r = client.get("/search/images?query=nonexistent_xyz", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_search_by_tags(self, client, auth_headers, db_session):
        img = Image(sha256="ee" * 32, bytes=50, mime="image/jpeg", storage_key="k/ee")
        tag = Tag(name="searchable", scope="global")
        db_session.add_all([img, tag])
        db_session.commit()

        # Tag the image
        client.post(f"/tags/images/{img.id}", json={"tag_ids": [tag.id]}, headers=auth_headers)

        r = client.get("/search/images?tags=searchable", headers=auth_headers)
        assert r.status_code == 200
        results = r.json()["results"]
        assert any(row["id"] == img.id for row in results)

    def test_search_returns_all_when_no_filter(self, client, auth_headers, db_session):
        img = Image(sha256="ff" * 32, bytes=50, mime="image/jpeg", storage_key="k/ff")
        db_session.add(img)
        db_session.commit()

        r = client.get("/search/images", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()["results"]) >= 1


class TestSearchAlbums:
    def test_search_by_title(self, client, auth_headers, db_session):
        album = Album(title="Unique Summer Vacation")
        db_session.add(album)
        db_session.commit()

        r = client.get("/search/albums?query=Summer", headers=auth_headers)
        assert r.status_code == 200
        results = r.json()["results"]
        assert any(row["id"] == album.id for row in results)

    def test_search_by_description(self, client, auth_headers, db_session):
        album = Album(title="X", description="Beach photos from Hawaii")
        db_session.add(album)
        db_session.commit()

        r = client.get("/search/albums?query=Hawaii", headers=auth_headers)
        assert r.status_code == 200
        results = r.json()["results"]
        assert any(row["id"] == album.id for row in results)

    def test_search_albums_by_tags(self, client, auth_headers, db_session):
        album = Album(title="Tagged")
        tag = Tag(name="album_tag", scope="global")
        db_session.add_all([album, tag])
        db_session.commit()

        client.post(f"/tags/albums/{album.id}", json={"tag_ids": [tag.id]}, headers=auth_headers)

        r = client.get("/search/albums?tags=album_tag", headers=auth_headers)
        assert r.status_code == 200
        results = r.json()["results"]
        assert any(row["id"] == album.id for row in results)
