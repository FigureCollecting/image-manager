from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DevTokenRequest(BaseModel):
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    aud: Optional[str] = Field(default=None, description="service:<name> for service tokens")


class DevTokenResponse(BaseModel):
    token: str

