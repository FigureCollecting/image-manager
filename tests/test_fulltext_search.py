"""Tests for full-text search improvements.

Validates that the search helpers and route-level search work correctly.
SQLite tests exercise the ILIKE fallback; tsvector is PostgreSQL-only.
"""

from app.models import Album, Image, UserImageLink
from app.search import build_text_filter, is_postgres

_USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_TENANT_ID = "11111111-2222-3333-4444-555555555555"


class TestSearchHelper:
    def test_is_postgres_sqlite(self, db_session):
        """In test env (SQLite), is_postgres should return False."""
        assert is_postgres(db_session) is False

    def test_build_text_filter_returns_clause(self, db_session):
        """build_text_filter should return a usable SQLAlchemy clause."""
        clause = build_text_filter(db_session, "hello", Image.mime, Image.storage_key)
        assert clause is not None


class TestImageFullTextSearch:
    def _add_linked(self, db_session, img):
        db_session.add(img)
        db_session.flush()
        db_session.add(
            UserImageLink(user_id=_USER_ID, tenant_id=_TENANT_ID, image_id=img.id, role="owner")
        )

    def test_search_partial_match(self, client, auth_headers, db_session):
        """ILIKE fallback should match substrings."""
        img = Image(sha256="ft1" * 22, bytes=100, mime="image/png", storage_key="photos/beach.png")
        self._add_linked(db_session, img)
        db_session.commit()

        r = client.get("/search/images?query=beach", headers=auth_headers)
        assert r.status_code == 200
        ids = [row["id"] for row in r.json()["results"]]
        assert img.id in ids

    def test_search_case_insensitive(self, client, auth_headers, db_session):
        img = Image(
            sha256="ft2" * 22, bytes=100, mime="image/jpeg", storage_key="photos/SUNSET.jpg"
        )
        self._add_linked(db_session, img)
        db_session.commit()

        r = client.get("/search/images?query=sunset", headers=auth_headers)
        ids = [row["id"] for row in r.json()["results"]]
        assert img.id in ids

    def test_search_by_mime_type(self, client, auth_headers, db_session):
        img = Image(sha256="ft3" * 22, bytes=100, mime="image/webp", storage_key="k/ft3")
        self._add_linked(db_session, img)
        db_session.commit()

        r = client.get("/search/images?query=webp", headers=auth_headers)
        ids = [row["id"] for row in r.json()["results"]]
        assert img.id in ids

    def test_empty_query_returns_all(self, client, auth_headers, db_session):
        img = Image(sha256="ft4" * 22, bytes=100, mime="image/jpeg", storage_key="k/ft4")
        self._add_linked(db_session, img)
        db_session.commit()

        r = client.get("/search/images", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()["results"]) >= 1


class TestAlbumFullTextSearch:
    def test_search_title_case_insensitive(self, client, auth_headers, db_session):
        album = Album(title="Mountain Adventures", tenant_id=_TENANT_ID)
        db_session.add(album)
        db_session.commit()

        r = client.get("/search/albums?query=mountain", headers=auth_headers)
        ids = [row["id"] for row in r.json()["results"]]
        assert album.id in ids

    def test_search_description_partial(self, client, auth_headers, db_session):
        album = Album(
            title="X",
            description="Incredible sunset photography collection",
            tenant_id=_TENANT_ID,
        )
        db_session.add(album)
        db_session.commit()

        r = client.get("/search/albums?query=sunset", headers=auth_headers)
        ids = [row["id"] for row in r.json()["results"]]
        assert album.id in ids

    def test_search_no_match(self, client, auth_headers):
        r = client.get("/search/albums?query=zzz_no_match_xyz", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["results"] == []
