from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request, Response

from .config import Settings, get_settings
from .logging import setup_logging
from .routes.auth_routes import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("app_start", extra={"env": settings.environment})
    yield
    logging.getLogger(__name__).info("app_stop")


app = FastAPI(title="image-manager", lifespan=lifespan)


@app.middleware("http")
async def add_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("x-request-id")
    if not request_id:
        # generate a simple UUID-like value without import overhead
        import uuid

        request_id = str(uuid.uuid4())
    response: Response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.get("/healthz")
async def healthz(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


app.include_router(auth_router)
