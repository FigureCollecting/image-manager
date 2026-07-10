"""Tests for rate limiting middleware."""

from unittest.mock import patch

from app.config import Settings


class TestRateLimitSettings:
    def test_rate_limit_fields_exist(self, test_settings):
        """Settings should have rate_limit_per_minute field."""
        assert hasattr(test_settings, "rate_limit_per_minute")

    def test_rate_limit_default(self, test_settings):
        """Default rate limit should be reasonable (e.g. 120/min)."""
        assert test_settings.rate_limit_per_minute > 0


class TestRateLimitMiddleware:
    def test_normal_request_not_throttled(self, client, auth_headers):
        """Normal requests should succeed with 200."""
        r = client.get("/healthz")
        assert r.status_code == 200

    def test_rate_limit_header_present(self, client):
        """Response should include X-RateLimit-Limit header."""
        r = client.get("/healthz")
        assert "x-ratelimit-limit" in r.headers

    def test_rate_limit_remaining_header(self, client):
        """Response should include X-RateLimit-Remaining header."""
        r = client.get("/healthz")
        assert "x-ratelimit-remaining" in r.headers

    def test_exceeding_rate_limit_returns_429(self, client):
        """Exceeding rate limit should return 429 Too Many Requests."""
        # Patch settings to have a very low rate limit for testing
        with patch("app.rate_limit.get_settings") as mock_settings:
            mock_settings.return_value = Settings(
                database_url="sqlite://",
                redis_url="redis://localhost:6379/15",
                s3_endpoint_url="http://localhost:9000",
                environment="test",
                jwt_secret="test-secret",
                allow_dev_tokens=True,
                rate_limit_per_minute=3,
            )
            # Clear the rate limiter state
            from app.rate_limit import _buckets

            _buckets.clear()

            for _ in range(3):
                r = client.get("/healthz")
                assert r.status_code == 200

            r = client.get("/healthz")
            assert r.status_code == 429

    def test_429_response_has_retry_after(self, client):
        """429 response should include Retry-After header."""
        with patch("app.rate_limit.get_settings") as mock_settings:
            mock_settings.return_value = Settings(
                database_url="sqlite://",
                redis_url="redis://localhost:6379/15",
                s3_endpoint_url="http://localhost:9000",
                environment="test",
                jwt_secret="test-secret",
                allow_dev_tokens=True,
                rate_limit_per_minute=1,
            )
            from app.rate_limit import _buckets

            _buckets.clear()

            client.get("/healthz")
            r = client.get("/healthz")
            assert r.status_code == 429
            assert "retry-after" in r.headers
