"""Tests for enhanced health check with DB connectivity verification."""

from unittest.mock import MagicMock

from app.db import get_db
from app.main import app
from sqlalchemy.exc import OperationalError


class TestHealthEndpoint:
    def test_healthz_returns_ok(self, client):
        """Basic healthz should return 200 with status ok."""
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_healthz_includes_db_status(self, client):
        """Health response should include db connectivity status."""
        r = client.get("/healthz")
        data = r.json()
        assert "db" in data
        assert data["db"] == "ok"

    def test_healthz_db_failure_returns_degraded(self):
        """When DB is unreachable, healthz should return 503 with db error."""
        mock_session = MagicMock()
        mock_session.execute.side_effect = OperationalError("connection refused", None, None)

        def _broken_db():
            yield mock_session

        app.dependency_overrides[get_db] = _broken_db
        try:
            from fastapi.testclient import TestClient

            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/healthz")
            assert r.status_code == 503
            data = r.json()
            assert data["status"] == "degraded"
            assert data["db"] == "error"
        finally:
            app.dependency_overrides.pop(get_db, None)
