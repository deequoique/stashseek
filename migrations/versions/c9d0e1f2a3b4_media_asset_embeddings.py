"""add tenant-scoped media asset identities and versioned WeMM vectors

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "d04fdae36884"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_content_item_id_user_id",
        "content_item",
        ["id", "user_id"],
    )
    op.create_table(
        "media_asset",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("app_user_id", sa.BigInteger(), nullable=False),
        sa.Column("content_item_id", sa.BigInteger(), nullable=False),
        sa.Column("segment_id", sa.BigInteger(), nullable=True),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("video_start_sec", sa.Numeric(12, 3), nullable=True),
        sa.Column("video_end_sec", sa.Numeric(12, 3), nullable=True),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "media_type IN ('image', 'video')",
            name="ck_media_asset_media_type",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'ready', 'failed', 'archived', 'deleted')",
            name="ck_media_asset_state",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-fA-F]{64}$'",
            name="ck_media_asset_sha256",
        ),
        sa.CheckConstraint(
            "(video_start_sec IS NULL AND video_end_sec IS NULL) OR "
            "(media_type = 'video' AND video_start_sec IS NOT NULL AND "
            "video_end_sec IS NOT NULL AND video_start_sec >= 0 AND "
            "video_end_sec >= video_start_sec)",
            name="ck_media_asset_video_range",
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"],
            ["app_user.id"],
            name="fk_media_asset_app_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id", "app_user_id"],
            ["content_item.id", "content_item.user_id"],
            name="fk_media_asset_content_item_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["segment.id"],
            name="fk_media_asset_segment_id_segment",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_asset"),
        sa.UniqueConstraint(
            "app_user_id", "sha256", name="uq_media_asset_user_sha256"
        ),
        sa.UniqueConstraint(
            "id", "app_user_id", name="uq_media_asset_id_app_user_id"
        ),
    )
    op.create_index(
        "ix_media_asset_user_state",
        "media_asset",
        ["app_user_id", "state", "id"],
    )
    op.create_index(
        "ix_media_asset_content_item_state",
        "media_asset",
        ["content_item_id", "state"],
    )

    op.create_table(
        "media_embedding",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("media_asset_id", sa.Text(), nullable=False),
        sa.Column("app_user_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("revision", sa.Text(), nullable=False),
        sa.Column("protocol_version", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), server_default="1024", nullable=False),
        sa.Column("embedding_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dimensions = 1024", name="ck_media_embedding_dimensions"
        ),
        sa.CheckConstraint(
            "embedding_version > 0", name="ck_media_embedding_version"
        ),
        sa.CheckConstraint("length(model) > 0", name="ck_media_embedding_model"),
        sa.CheckConstraint(
            "length(revision) > 0", name="ck_media_embedding_revision"
        ),
        sa.CheckConstraint(
            "length(protocol_version) > 0",
            name="ck_media_embedding_protocol_version",
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id", "app_user_id"],
            ["media_asset.id", "media_asset.app_user_id"],
            name="fk_media_embedding_media_asset_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["app_user_id"],
            ["app_user.id"],
            name="fk_media_embedding_app_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_embedding"),
        sa.UniqueConstraint(
            "media_asset_id",
            "model",
            "revision",
            "protocol_version",
            "dimensions",
            "embedding_version",
            name="uq_media_embedding_asset_space_version",
        ),
    )
    op.create_index(
        "ix_media_embedding_user_space",
        "media_embedding",
        ["app_user_id", "model", "revision", "protocol_version", "dimensions"],
    )


def downgrade() -> None:
    # Deliberately non-destructive: this preparation migration must not drop
    # future media identities or vectors during a rollback attempt.
    pass
