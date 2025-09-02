from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import Settings
from .models import ServiceClient

logger = logging.getLogger(__name__)


def create_token(
    settings: Settings,
    *,
    subject: str,
    tenant_id: Optional[str] = None,
    scopes: Optional[list[str]] = None,
    audience: Optional[str] = None,
    ttl_minutes: Optional[int] = None,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(minutes=ttl_minutes or settings.token_exp_minutes)
    payload: Dict[str, Any] = {
        "sub": subject,
        "exp": int(exp.timestamp()),
        "iat": int(now.timestamp()),
    }
    if tenant_id:
        payload["ten"] = tenant_id
    if scopes:
        payload["scopes"] = scopes
    if audience:
        payload["aud"] = audience
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token


def decode_token(settings: Settings, token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        logger.warning("jwt_decode_failed", extra={"error": str(e)})
        return None


def issue_dev_token(
    db: Session,
    settings: Settings,
    *,
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    aud: Optional[str] = None,
) -> str:
    if aud and aud.startswith("service:"):
        # service token with scopes looked up from DB
        name = aud.split(":", 1)[1]
        svc = db.query(ServiceClient).filter(ServiceClient.name == name).one_or_none()
        scopes = []
        if svc and svc.scopes:
            scopes = [s.strip() for s in svc.scopes.split(" ") if s.strip()]
        return create_token(settings, subject=f"service:{name}", audience=aud, scopes=scopes)
    # user token
    if not user_id:
        raise ValueError("user_id required for user tokens")
    return create_token(settings, subject=user_id, tenant_id=tenant_id, scopes=[])

