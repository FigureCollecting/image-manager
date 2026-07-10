"""Tests to raise policy.py coverage to ≥85%.

Covers: all branches of can_view_version including catalog, tenant match/mismatch,
safe_mode threshold, share_threshold, and age-gating denial.
"""

from __future__ import annotations

from app.policy import AuthCtx, can_view_version


class TestCanViewVersionPublic:
    def test_public_visible_to_anyone(self) -> None:
        assert can_view_version(None, "public", None, 0) is True

    def test_public_visible_without_ctx(self) -> None:
        assert can_view_version(None, "public", "tenant-1", 5) is True


class TestCanViewVersionCatalog:
    def test_catalog_requires_service_with_scope(self) -> None:
        ctx = AuthCtx(subject="service:be", tenant_id=None, is_service=True, scopes=["assets:read"])
        assert can_view_version(ctx, "catalog", None, 0) is True

    def test_catalog_denied_without_ctx(self) -> None:
        assert can_view_version(None, "catalog", None, 0) is False

    def test_catalog_denied_for_non_service(self) -> None:
        ctx = AuthCtx(subject="user1", tenant_id=None, is_service=False, scopes=["assets:read"])
        assert can_view_version(ctx, "catalog", None, 0) is False

    def test_catalog_denied_without_scope(self) -> None:
        ctx = AuthCtx(subject="service:be", tenant_id=None, is_service=True, scopes=[])
        assert can_view_version(ctx, "catalog", None, 0) is False

    def test_catalog_denied_wrong_scope(self) -> None:
        ctx = AuthCtx(subject="service:be", tenant_id=None, is_service=True, scopes=["other:scope"])
        assert can_view_version(ctx, "catalog", None, 0) is False


class TestCanViewVersionTenant:
    def test_tenant_match_allows(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id="t1", is_service=False, scopes=[])
        assert can_view_version(ctx, "tenant", "t1", 0) is True

    def test_tenant_mismatch_denies(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id="t1", is_service=False, scopes=[])
        assert can_view_version(ctx, "tenant", "t2", 0) is False

    def test_tenant_no_ctx_denies(self) -> None:
        assert can_view_version(None, "tenant", "t1", 0) is False

    def test_tenant_no_tenant_id_in_ctx_denies(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id=None, is_service=False, scopes=[])
        assert can_view_version(ctx, "tenant", "t1", 0) is False

    def test_tenant_no_owner_tenant_denies(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id="t1", is_service=False, scopes=[])
        assert can_view_version(ctx, "tenant", None, 0) is False


class TestCanViewVersionAgeGating:
    def test_private_no_age_passes(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id=None, is_service=False, scopes=[])
        assert can_view_version(ctx, "private", None, 0) is True

    def test_safe_mode_raises_threshold(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id=None, is_service=False, scopes=[], safe_mode=True)
        # age_rating=1, safe_mode threshold=1 → 1 > 1 is False → allowed
        assert can_view_version(ctx, "private", None, 1) is True
        # age_rating=2, safe_mode threshold=1 → 2 > 1 → denied
        assert can_view_version(ctx, "private", None, 2) is False

    def test_share_threshold_gates(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id=None, is_service=False, scopes=[])
        # share_threshold=3, age=3 → 3 > 3 is False → allowed
        assert can_view_version(ctx, "private", None, 3, share_threshold=3) is True
        # share_threshold=2, age=3 → 3 > 2 → denied
        assert can_view_version(ctx, "private", None, 3, share_threshold=2) is False

    def test_safe_mode_and_share_threshold_uses_max(self) -> None:
        ctx = AuthCtx(subject="u1", tenant_id=None, is_service=False, scopes=[], safe_mode=True)
        # safe_mode threshold=1, share_threshold=3 → max=3, age=3 → allowed
        assert can_view_version(ctx, "private", None, 3, share_threshold=3) is True
        # safe_mode threshold=1, share_threshold=3 → max=3, age=4 → denied
        assert can_view_version(ctx, "private", None, 4, share_threshold=3) is False

    def test_no_ctx_no_threshold(self) -> None:
        # private with no ctx, age=0 → passes (private enforced at query time)
        assert can_view_version(None, "private", None, 0) is True

    def test_no_ctx_age_above_zero_denied(self) -> None:
        # age=1 > threshold=0 → denied
        assert can_view_version(None, "private", None, 1) is False
