"""Tests for worker session management — proper context managers with rollback."""

import inspect

from app.db import worker_session
from app.models import Image
from sqlalchemy.orm import sessionmaker


class TestWorkerSessionContextManager:
    def test_worker_session_commits_on_success(self, db_engine):
        """worker_session should auto-commit when block succeeds."""
        factory = sessionmaker(
            bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        with worker_session(session_factory=factory) as db:
            img = Image(sha256="ws1" * 22, bytes=100, mime="image/jpeg", storage_key="k/ws1")
            db.add(img)
        # Should be persisted after context exit
        check = factory()
        try:
            row = check.query(Image).filter(Image.sha256 == "ws1" * 22).first()
            assert row is not None
        finally:
            check.close()

    def test_worker_session_rollback_on_exception(self, db_engine):
        """worker_session should rollback on exception and re-raise."""
        factory = sessionmaker(
            bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        try:
            with worker_session(session_factory=factory) as db:
                img = Image(sha256="ws2" * 22, bytes=100, mime="image/jpeg", storage_key="k/ws2")
                db.add(img)
                db.flush()
                raise ValueError("test error")
        except ValueError:
            pass

        check = factory()
        try:
            row = check.query(Image).filter(Image.sha256 == "ws2" * 22).first()
            assert row is None  # Should have been rolled back
        finally:
            check.close()

    def test_worker_session_closes_session(self, db_engine):
        """Session should be closed after context exit."""
        factory = sessionmaker(
            bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        with worker_session(session_factory=factory) as db:
            pass
        assert db is not None

    def test_worker_session_reraises_exception(self, db_engine):
        """worker_session should re-raise the original exception."""
        factory = sessionmaker(
            bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        raised = False
        try:
            with worker_session(session_factory=factory):
                raise RuntimeError("boom")
        except RuntimeError as e:
            assert str(e) == "boom"
            raised = True
        assert raised


class TestWorkerTasksUseContextManager:
    def test_verify_task_uses_worker_session(self):
        """verify_and_register_object should use worker_session context manager."""
        import app.workers.tasks as tasks_module

        source = inspect.getsource(tasks_module.verify_and_register_object)
        assert "worker_session" in source

    def test_transform_task_uses_worker_session(self):
        """create_transformed_version should use worker_session context manager."""
        import app.workers.tasks as tasks_module

        source = inspect.getsource(tasks_module.create_transformed_version)
        assert "worker_session" in source

    def test_album_cover_task_uses_worker_session(self):
        """generate_album_cover should use worker_session context manager."""
        import app.workers.tasks as tasks_module

        source = inspect.getsource(tasks_module.generate_album_cover)
        assert "worker_session" in source
