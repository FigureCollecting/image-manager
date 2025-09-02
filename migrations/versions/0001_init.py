from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # enable extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Use SQLAlchemy models to create tables
    bind = op.get_bind()
    from app.models import Base  # lazy import to avoid alembic import issues

    Base.metadata.create_all(bind)

    # Additional indexes for search
    op.execute("CREATE INDEX IF NOT EXISTS idx_album_title_trgm ON albums USING gin (title gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_album_desc_trgm ON albums USING gin (description gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_album_desc_trgm")
    op.execute("DROP INDEX IF EXISTS idx_album_title_trgm")
    # Drop all tables via metadata
    bind = op.get_bind()
    from app.models import Base

    Base.metadata.drop_all(bind)

