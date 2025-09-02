from __future__ import annotations

import logging
import uuid
from typing import Generator

from fastapi import Header

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


def get_app_settings() -> Settings:
    return get_settings()


def get_request_id(x_request_id: str | None = Header(default=None)) -> str:
    # Generate or propagate an x-request-id
    rid = x_request_id or str(uuid.uuid4())
    return rid


def db_session() -> Generator[None, None, None]:  # placeholder; real session added later
    # Will be replaced in DB integration step
    yield None

