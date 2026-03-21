"""Tests for CORS middleware configuration."""


class TestCORSHeaders:
    def test_preflight_request_returns_cors_headers(self, client):
        """OPTIONS preflight should include CORS headers."""
        r = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") is not None

    def test_cors_allows_configured_origin(self, client):
        """GET with Origin header should echo back allowed origin."""
        r = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
        assert r.headers.get("access-control-allow-origin") is not None

    def test_cors_allows_common_methods(self, client):
        """Preflight should allow GET, POST, PUT, DELETE, PATCH."""
        r = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "DELETE",
            },
        )
        allowed = r.headers.get("access-control-allow-methods", "")
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            assert method in allowed

    def test_cors_allows_auth_header(self, client):
        """Preflight should allow Authorization header."""
        r = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        allowed_headers = r.headers.get("access-control-allow-headers", "")
        assert "authorization" in allowed_headers.lower()


class TestCORSSettings:
    def test_cors_origins_from_settings(self, test_settings):
        """Settings should have cors_origins field."""
        assert hasattr(test_settings, "cors_origins")

    def test_cors_origins_default_is_explicit_allowlist(self, test_settings):
        """Default cors_origins should be an explicit allow-list, not wildcard."""
        assert "*" not in test_settings.cors_origins
        assert "https://figurecollecting.com" in test_settings.cors_origins
