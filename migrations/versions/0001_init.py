"""Initial schema.

Revision ID: 0001_init

Emits explicit, frozen DDL for the schema as it existed at this revision
(NOT app.models.Base.metadata.create_all). Later revisions (e.g. 0002's
figure_galleries table, 0003's matte/grounding columns) alter live models
independently, so this migration must not re-derive its shape from the
current models or it will collide with those revisions when the full
chain is replayed against an empty database.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT, UUID

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # enable extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "images",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sha256", sa.String(64), unique=True, nullable=False),
        sa.Column("phash", sa.String(64), nullable=True),
        sa.Column("mime", sa.String(100), nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("bytes", sa.BigInteger, nullable=True),
        sa.Column("storage_key", sa.String(512), unique=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "image_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "image_id", sa.Integer, sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("derived_from_version", sa.Integer, nullable=True),
        sa.Column("transform_spec", sa.JSON, nullable=False),
        sa.Column("mime", sa.String(100), nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("bytes", sa.BigInteger, nullable=True),
        sa.Column("storage_key", sa.String(512), unique=True, nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("age_rating", sa.SmallInteger, nullable=False),
        sa.Column("alt_for_version_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("image_id", "version_no", name="uq_image_version_no"),
        sa.CheckConstraint(
            "visibility in ('private','tenant','public','catalog')", name="ck_visibility"
        ),
    )
    op.create_index(
        "ix_image_versions_image_id_visibility", "image_versions", ["image_id", "visibility"]
    )

    op.create_table(
        "user_image_links",
        sa.Column("user_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=False), nullable=True),
        sa.Column(
            "image_id", sa.Integer, sa.ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "current_version_id",
            sa.Integer,
            sa.ForeignKey("image_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("actor_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("actor_service", sa.String(100), nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=False), nullable=True),
        sa.Column("image_id", sa.Integer, nullable=True),
        sa.Column("version_id", sa.Integer, nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "albums",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=False), nullable=True),
        sa.Column("owner_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("default_visibility", sa.String(16), nullable=False),
        sa.Column("is_shareable", sa.Boolean, nullable=False),
        sa.Column("allow_item_override", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("share_token_hash", sa.String(128), nullable=True),
        sa.Column("share_age_threshold", sa.SmallInteger, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "default_visibility in ('private','tenant','public')", name="ck_album_visibility"
        ),
    )
    op.create_index("ix_album_title_trgm", "albums", ["title"])
    op.create_index("ix_album_desc_trgm", "albums", ["description"])

    op.create_table(
        "album_items",
        sa.Column(
            "album_id", sa.Integer, sa.ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("position", sa.Integer, primary_key=True),
        sa.Column(
            "image_id", sa.Integer, sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version_id", sa.Integer, nullable=True),
        sa.Column("item_visibility", sa.String(16), nullable=True),
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", CITEXT(), unique=True, nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=False), nullable=True),
        sa.Column("owner_user_id", UUID(as_uuid=False), nullable=True),
        sa.CheckConstraint("scope in ('global','tenant','user')", name="ck_tag_scope"),
    )

    op.create_table(
        "image_tags",
        sa.Column(
            "image_id", sa.Integer, sa.ForeignKey("images.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("tenant_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_user_id", UUID(as_uuid=False), nullable=True),
    )

    op.create_table(
        "album_tags",
        sa.Column(
            "album_id", sa.Integer, sa.ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
        ),
    )

    op.create_table(
        "external_refs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ref_type", sa.String(50), nullable=False),
        sa.Column("ref_id", sa.String(200), nullable=False),
        sa.Column(
            "image_id", sa.Integer, sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version_id", sa.Integer, nullable=True),
        sa.Column("tenant_id", UUID(as_uuid=False), nullable=True),
        sa.UniqueConstraint("ref_type", "ref_id", name="uq_ref_type_id"),
    )

    op.create_table(
        "service_clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("secret_hash", sa.String(200), nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
    )

    # Additional indexes for search
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_album_title_trgm ON albums USING gin (title gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_album_desc_trgm ON albums USING gin (description gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_album_desc_trgm")
    op.execute("DROP INDEX IF EXISTS idx_album_title_trgm")

    op.drop_table("service_clients")
    op.drop_table("external_refs")
    op.drop_table("album_tags")
    op.drop_table("image_tags")
    op.drop_table("tags")
    op.drop_table("album_items")
    op.drop_index("ix_album_desc_trgm", table_name="albums")
    op.drop_index("ix_album_title_trgm", table_name="albums")
    op.drop_table("albums")
    op.drop_table("audit_events")
    op.drop_table("user_image_links")
    op.drop_index("ix_image_versions_image_id_visibility", table_name="image_versions")
    op.drop_table("image_versions")
    op.drop_table("images")
