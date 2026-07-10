"""Add figure_galleries table for figure-collector integration.

Revision ID: 0002_add_figure_galleries
Revises: 0001_init
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_add_figure_galleries"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "figure_galleries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("figure_id", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("image_id", sa.Integer, sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="mfc"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("figure_id", "source_url", name="uq_figure_source_url"),
    )
    op.create_index("ix_figure_galleries_figure_id", "figure_galleries", ["figure_id"])


def downgrade() -> None:
    op.drop_index("ix_figure_galleries_figure_id", table_name="figure_galleries")
    op.drop_table("figure_galleries")
