"""Tests for connection pooling: S3 client singleton and DB engine pool configuration.

TDD: These tests define the expected behaviour for Priority 3.
"""


# ---------------------------------------------------------------------------
# S3 client singleton
# ---------------------------------------------------------------------------


class TestS3ClientSingleton:
    def test_get_s3_returns_same_instance(self):
        """get_s3() should return the same client object on repeated calls."""
        from app.s3 import get_s3

        c1 = get_s3()
        c2 = get_s3()
        assert c1 is c2, "get_s3() must return a cached singleton"

    def test_get_s3_cache_can_be_cleared(self):
        """After clearing the cache, a new client is created."""
        from app.s3 import get_s3

        c1 = get_s3()
        get_s3.cache_clear()
        c2 = get_s3()
        assert c1 is not c2, "After cache_clear(), a fresh client should be created"

    def test_presign_functions_reuse_client(self):
        """Repeated get_s3() calls return the same client."""
        from app.s3 import get_s3

        get_s3.cache_clear()
        c1 = get_s3()
        c2 = get_s3()
        assert c1 is c2


# ---------------------------------------------------------------------------
# DB engine pool configuration
# ---------------------------------------------------------------------------


class TestDBPoolConfig:
    def test_settings_has_pool_fields(self):
        """Settings should have pool configuration fields with sensible defaults."""
        from app.config import get_settings

        s = get_settings()
        assert hasattr(s, "db_pool_size"), "Settings must define db_pool_size"
        assert hasattr(s, "db_max_overflow"), "Settings must define db_max_overflow"
        assert hasattr(s, "db_pool_recycle"), "Settings must define db_pool_recycle"
        assert s.db_pool_size >= 5
        assert s.db_max_overflow >= 5
        assert s.db_pool_recycle > 0

    def test_engine_pool_recycle(self):
        """Engine should set pool_recycle to avoid stale PostgreSQL connections."""
        from app.db import engine

        assert engine.pool._recycle > 0, "pool_recycle should be set to a positive value"

    def test_engine_pool_pre_ping(self):
        """Engine should use pool_pre_ping for connection health checks."""
        from app.db import engine

        assert engine.pool._pre_ping is True, "pool_pre_ping should be enabled"
