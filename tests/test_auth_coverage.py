"""Tests to raise auth.py coverage to ≥85%.

Covers: token with audience/scopes, expired token decoding, malformed token,
issue_dev_token service path with DB lookup, missing user_id error.
"""

from __future__ import annotations

import datetime as dt
import time

import pytest
from app.auth import create_token, decode_token, issue_dev_token
from app.config import Settings
from app.models import ServiceClient
from jose import jwt


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        redis_url="redis://localhost:6379/15",
        s3_endpoint_url="http://localhost:9000",
        environment="test",
        jwt_secret="test-secret",
        allow_dev_tokens=True,
    )


class TestCreateToken:
    def test_includes_audience_when_provided(self, settings: Settings) -> None:
        token = create_token(settings, subject="user1", audience="service:backend")
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            audience="service:backend",
        )
        assert payload["aud"] == "service:backend"

    def test_includes_scopes_when_provided(self, settings: Settings) -> None:
        token = create_token(settings, subject="svc", scopes=["assets:read", "assets:write"])
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["scopes"] == ["assets:read", "assets:write"]

    def test_includes_tenant_id_when_provided(self, settings: Settings) -> None:
        token = create_token(settings, subject="u1", tenant_id="t1")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["ten"] == "t1"

    def test_omits_optional_fields_when_none(self, settings: Settings) -> None:
        token = create_token(settings, subject="u1")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert "ten" not in payload
        assert "scopes" not in payload
        assert "aud" not in payload

    def test_custom_ttl(self, settings: Settings) -> None:
        token = create_token(settings, subject="u1", ttl_minutes=5)
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        # exp should be ~5 min from iat
        assert payload["exp"] - payload["iat"] == 300


class TestDecodeToken:
    def test_valid_token(self, settings: Settings) -> None:
        token = create_token(settings, subject="u1")
        result = decode_token(settings, token)
        assert result is not None
        assert result["sub"] == "u1"

    def test_expired_token_returns_none(self, settings: Settings) -> None:
        token = create_token(settings, subject="u1", ttl_minutes=-1)
        result = decode_token(settings, token)
        assert result is None

    def test_malformed_token_returns_none(self, settings: Settings) -> None:
        result = decode_token(settings, "not.a.valid.jwt")
        assert result is None

    def test_wrong_secret_returns_none(self, settings: Settings) -> None:
        token = create_token(settings, subject="u1")
        other = Settings(
            database_url="sqlite://",
            redis_url="redis://localhost:6379/15",
            s3_endpoint_url="http://localhost:9000",
            jwt_secret="different-secret",
        )
        result = decode_token(other, token)
        assert result is None

    def test_empty_string_returns_none(self, settings: Settings) -> None:
        result = decode_token(settings, "")
        assert result is None


class TestIssueDevToken:
    def test_user_token_requires_user_id(self, settings: Settings, db_session) -> None:
        with pytest.raises(ValueError, match="user_id required"):
            issue_dev_token(db_session, settings)

    def test_user_token_with_tenant(self, settings: Settings, db_session) -> None:
        token = issue_dev_token(db_session, settings, user_id="u1", tenant_id="t1")
        payload = decode_token(settings, token)
        assert payload is not None
        assert payload["sub"] == "u1"
        assert payload["ten"] == "t1"

    def test_service_token_no_db_record(self, settings: Settings, db_session) -> None:
        """Service token for name not in DB gets empty scopes."""
        token = issue_dev_token(db_session, settings, aud="service:unknown")
        # Decode directly with audience since decode_token doesn't pass audience
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            audience="service:unknown",
        )
        assert payload["sub"] == "service:unknown"
        assert payload.get("scopes", []) == []

    def test_service_token_with_db_scopes(self, settings: Settings, db_session) -> None:
        """Service token for name in DB gets scopes from record."""
        svc = ServiceClient(name="backend", secret_hash="hash", scopes="assets:read assets:write")
        db_session.add(svc)
        db_session.flush()

        token = issue_dev_token(db_session, settings, aud="service:backend")
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            audience="service:backend",
        )
        assert payload["sub"] == "service:backend"
        assert set(payload["scopes"]) == {"assets:read", "assets:write"}

    def test_service_token_empty_scopes_string(self, settings: Settings, db_session) -> None:
        """Service client with empty scopes string yields empty list."""
        svc = ServiceClient(name="empty-svc", secret_hash="hash", scopes="")
        db_session.add(svc)
        db_session.flush()

        token = issue_dev_token(db_session, settings, aud="service:empty-svc")
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            audience="service:empty-svc",
        )
        assert payload.get("scopes", []) == []
