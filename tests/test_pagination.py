"""Tests for cursor-based pagination on list endpoints."""

from app.models import Album, Image


class TestImageSearchPagination:
    def test_default_limit(self, client, auth_headers, db_session):
        for i in range(5):
            db_session.add(Image(sha256=f"{i:064d}", bytes=100, mime="image/jpeg", storage_key=f"k/pg{i}"))
        db_session.commit()

        r = client.get("/search/images", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert "next_cursor" in data

    def test_limit_param(self, client, auth_headers, db_session):
        for i in range(10):
            db_session.add(Image(sha256=f"lim{i:060d}", bytes=100, mime="image/jpeg", storage_key=f"k/lim{i}"))
        db_session.commit()

        r = client.get("/search/images?limit=3", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 3
        assert data["next_cursor"] is not None

    def test_cursor_pagination(self, client, auth_headers, db_session):
        for i in range(5):
            db_session.add(Image(sha256=f"cur{i:060d}", bytes=100, mime="image/jpeg", storage_key=f"k/cur{i}"))
        db_session.commit()

        # First page
        r1 = client.get("/search/images?limit=2", headers=auth_headers)
        d1 = r1.json()
        assert len(d1["results"]) == 2
        cursor = d1["next_cursor"]
        assert cursor is not None

        # Second page
        r2 = client.get(f"/search/images?limit=2&after={cursor}", headers=auth_headers)
        d2 = r2.json()
        assert len(d2["results"]) == 2

        # No overlap
        ids1 = {r["id"] for r in d1["results"]}
        ids2 = {r["id"] for r in d2["results"]}
        assert ids1.isdisjoint(ids2)

    def test_last_page_null_cursor(self, client, auth_headers, db_session):
        db_session.add(Image(sha256="last" + "0" * 60, bytes=100, mime="image/jpeg", storage_key="k/last"))
        db_session.commit()

        r = client.get("/search/images?limit=100", headers=auth_headers)
        data = r.json()
        assert data["next_cursor"] is None


class TestAlbumSearchPagination:
    def test_limit_param(self, client, auth_headers, db_session):
        for i in range(5):
            db_session.add(Album(title=f"Album {i}"))
        db_session.commit()

        r = client.get("/search/albums?limit=2", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 2
        assert data["next_cursor"] is not None

    def test_cursor_pagination(self, client, auth_headers, db_session):
        for i in range(4):
            db_session.add(Album(title=f"Page {i}"))
        db_session.commit()

        r1 = client.get("/search/albums?limit=2", headers=auth_headers)
        d1 = r1.json()
        cursor = d1["next_cursor"]

        r2 = client.get(f"/search/albums?limit=2&after={cursor}", headers=auth_headers)
        d2 = r2.json()

        ids1 = {r["id"] for r in d1["results"]}
        ids2 = {r["id"] for r in d2["results"]}
        assert ids1.isdisjoint(ids2)
