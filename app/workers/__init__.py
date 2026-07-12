from __future__ import annotations

from celery import Celery

from ..config import get_settings


def make_celery() -> Celery:
    s = get_settings()
    app = Celery(
        "media_manager",
        broker=s.redis_url,
        backend=s.redis_url,
        include=["app.workers.tasks"],
    )
    app.conf.update(
        task_serializer="json", accept_content=["json"], result_serializer="json", timezone="UTC"
    )
    return app


celery_app = make_celery()
