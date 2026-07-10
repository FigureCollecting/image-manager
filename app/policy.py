from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuthCtx:
    subject: str
    tenant_id: str | None
    is_service: bool
    scopes: list[str]
    safe_mode: bool = False


def can_view_version(ctx: AuthCtx | None, version_visibility: str, owner_tenant_id: str | None, version_age: int, share_threshold: int | None = None) -> bool:
    # Public visible to anyone
    if version_visibility == "public":
        return True
    # Catalog requires service token scope
    if version_visibility == "catalog":
        return bool(ctx and ctx.is_service and ("assets:read" in ctx.scopes))
    # Tenant visibility requires matching tenant
    if version_visibility == "tenant":
        if ctx and ctx.tenant_id and owner_tenant_id and ctx.tenant_id == owner_tenant_id:
            pass
        else:
            return False
    # private requires a link which is enforced at query time

    # Age gating with safe mode or share threshold
    threshold = 0
    if ctx and ctx.safe_mode:
        threshold = max(threshold, 1)
    if share_threshold is not None:
        threshold = max(threshold, share_threshold)
    return version_age <= threshold

