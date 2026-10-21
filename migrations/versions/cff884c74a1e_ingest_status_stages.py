"""add enrichment stage table and ASR hand-off marker for ingest status

Revision ID: cff884c74a1e
Revises: d04fdae36884
Create Date: 2026-10-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "cff884c74a1e"
down_revision: Union[str, Sequence[str], None] = "d04fdae36884"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One row per (item, post-body stage).  The body stage is projected from
    # ``content_item.state`` and the latest dispatch and is never stored.
    op.create_table(
        "content_item_stage",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage <> 'body'",
            name="ck_content_item_stage_not_body",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'skipped')",
            name="ck_content_item_stage_status",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name="ck_content_item_stage_failed_error",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["content_item.id"],
            name="fk_content_item_stage_item_id_content_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "stage", name="pk_content_item_stage"),
    )
    # Set when the dispatch is handed to the asr queue; the durable signal that
    # a URL-imported item is being transcribed rather than fetched.
    op.add_column(
        "ingest_dispatch",
        sa.Column("asr_handoff_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Nothing writes content_item_stage until the enrichment tasks land, and
    # asr_handoff_at is only a progress hint, so a rollback loses no user data.
    op.drop_column("ingest_dispatch", "asr_handoff_at")
    op.drop_table("content_item_stage")
