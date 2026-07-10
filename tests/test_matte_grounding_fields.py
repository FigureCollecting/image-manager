"""Tests for the matte/grounding derivative columns on ImageVersion.

Covers:
  1. Model-level: ImageVersion.__table__ exposes the six new columns with the
     right SQLAlchemy types and nullability.
  2. Migration-level: the new Alembic migration's upgrade()/downgrade() add
     and remove the six columns cleanly, in isolation (no dependency on the
     full migration chain, which requires Postgres for 0001_init).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Boolean, Float, String
from sqlalchemy import create_engine, inspect, text

from app.models import ImageVersion

NEW_COLUMNS = {
    "matted",
    "bottom_margin_frac",
    "contact_band_center_x_frac",
    "contact_band_width_frac",
    "thumbhash",
    "dominant_color",
}

MIGRATION_FILENAME = "0003_add_matte_grounding_fields.py"


def _load_migration(filename: str):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location("migration_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_image_version_model_has_matte_grounding_columns():
    columns = ImageVersion.__table__.columns

    for name in NEW_COLUMNS:
        assert name in columns, f"missing column {name!r} on ImageVersion"

    assert isinstance(columns["matted"].type, Boolean)
    assert columns["matted"].nullable is False

    for name in (
        "bottom_margin_frac",
        "contact_band_center_x_frac",
        "contact_band_width_frac",
    ):
        assert isinstance(columns[name].type, Float), f"{name} should be Float"
        assert columns[name].nullable is True

    assert isinstance(columns["thumbhash"].type, String)
    assert columns["thumbhash"].type.length == 64
    assert columns["thumbhash"].nullable is True

    assert isinstance(columns["dominant_color"].type, String)
    assert columns["dominant_color"].type.length == 32
    assert columns["dominant_color"].nullable is True


def test_migration_upgrade_and_downgrade_are_reversible():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE image_versions (id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL, "
                "version_no INTEGER NOT NULL, storage_key VARCHAR(512) NOT NULL, "
                "visibility VARCHAR(16) NOT NULL DEFAULT 'private')"
            )
        )

    migration = _load_migration(MIGRATION_FILENAME)

    # NOTE: Operations.context() takes the MigrationContext itself, not an
    # Operations instance. Passing an Operations instance (e.g.
    # Operations.context(Operations(ctx))) silently corrupts
    # SchemaObjects.migration_context (it ends up pointing at the Operations
    # object rather than the real MigrationContext), which later blows up
    # with `AttributeError: 'Operations' object has no attribute 'opts'`
    # inside batch_alter_table's metadata() lookup.
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()
        cols = {c["name"] for c in inspect(engine).get_columns("image_versions")}
        assert NEW_COLUMNS <= cols

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.downgrade()
        cols = {c["name"] for c in inspect(engine).get_columns("image_versions")}
        assert not (NEW_COLUMNS & cols)
