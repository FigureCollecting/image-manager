"""Tests for consistent error handling across the application."""

from unittest.mock import MagicMock

from app.auth import create_token
from app.config import Settings
from app.db import get_db
from app.main import app


def _make_auth_headers():
    """Create valid auth headers for error handling tests."""
    settings = Settings(
        database_url="sqlite://",
        redis_url="redis://localhost:6379/15",
        s3_endpoint_url="http://localhost:9000",
        environment="test",
        jwt_secret="test-secret",
        allow_dev_tokens=True,
    )
    token = create_token(
        settings,
        subject="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        tenant_id="11111111-2222-3333-4444-555555555555",
    )
    return {"Authorization": f"Bearer {token}"}


class TestValidationErrors:
    def test_invalid_body_returns_422_with_detail(self, client, auth_headers):
        """Pydantic validation errors should return structured 422 response."""
        r = client.post(
            "/images/initiate-upload",
            json={"sha256": "bad", "bytes": -1, "mime": "image/jpeg"},
            headers=auth_headers,
        )
        assert r.status_code == 422
        data = r.json()
        assert "detail" in data

    def test_validation_error_has_consistent_shape(self, client, auth_headers):
        """422 errors should include field-level error info."""
        r = client.post(
            "/images/initiate-upload",
            json={"sha256": "short", "bytes": 0, "mime": "image/jpeg"},
            headers=auth_headers,
        )
        assert r.status_code == 422
        data = r.json()
        assert "detail" in data
        assert isinstance(data["detail"], list)


class TestNotFoundErrors:
    def test_404_returns_json_detail(self, client, auth_headers):
        """404 errors should return JSON with detail field."""
        r = client.get("/images/99999", headers=auth_headers)
        assert r.status_code == 404
        data = r.json()
        assert "detail" in data

    def test_unknown_route_returns_json(self, client):
        """Non-existent routes should return JSON, not HTML."""
        r = client.get("/no-such-route")
        assert r.status_code in (404, 405)
        data = r.json()
        assert "detail" in data


class TestUnhandledErrors:
    def test_unhandled_exception_returns_500_json(self):
        """Unhandled exceptions should return 500 with JSON body."""
        mock_session = MagicMock()
        mock_session.get.side_effect = RuntimeError("unexpected DB error")
        mock_session.execute.side_effect = RuntimeError("unexpected DB error")

        def _broken_db():
            yield mock_session

        app.dependency_overrides[get_db] = _broken_db
        try:
            from fastapi.testclient import TestClient

            headers = _make_auth_headers()
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get("/images/1", headers=headers)
            assert r.status_code == 500
            data = r.json()
            assert "detail" in data
            assert "Traceback" not in data["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_500_includes_request_id(self):
        """500 responses should include request ID for debugging."""
        mock_session = MagicMock()
        mock_session.get.side_effect = RuntimeError("unexpected DB error")
        mock_session.execute.side_effect = RuntimeError("unexpected DB error")

        def _broken_db():
            yield mock_session

        app.dependency_overrides[get_db] = _broken_db
        try:
            from fastapi.testclient import TestClient

            headers = _make_auth_headers()
            c = TestClient(app, raise_server_exceptions=False)
            r = c.get("/images/1", headers=headers)
            assert r.status_code == 500
            assert "x-request-id" in r.headers
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestAuthErrors:
    def test_401_returns_json_detail(self, client):
        """Missing auth should return 401 JSON with detail."""
        r = client.get("/images/1")
        assert r.status_code == 401
        data = r.json()
        assert "detail" in data
