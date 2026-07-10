"""Add matte/grounding derivative columns to image_versions.

Revision ID: 0003_add_matte_grounding_fields
Revises: 0002_add_figure_galleries
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_add_matte_grounding_fields"
down_revision = "0002_add_figure_galleries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_versions") as batch_op:
        batch_op.add_column(
            sa.Column("matted", sa.Boolean, nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("bottom_margin_frac", sa.Float, nullable=True))
        batch_op.add_column(
            sa.Column("contact_band_center_x_frac", sa.Float, nullable=True)
        )
        batch_op.add_column(
            sa.Column("contact_band_width_frac", sa.Float, nullable=True)
        )
        batch_op.add_column(sa.Column("thumbhash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("dominant_color", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("image_versions") as batch_op:
        batch_op.drop_column("dominant_color")
        batch_op.drop_column("thumbhash")
        batch_op.drop_column("contact_band_width_frac")
        batch_op.drop_column("contact_band_center_x_frac")
        batch_op.drop_column("bottom_margin_frac")
        batch_op.drop_column("matted")
