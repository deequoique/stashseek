"""Nullable stable Bilibili part sources; leave all historical rows unpinned.

Revision ID: f8a9b0c1d2e3
Revises: e2f3a4b5c6d7
"""

from alembic import op
import sqlalchemy as sa

revision = "f8a9b0c1d2e3"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_item", sa.Column("bilibili_bvid", sa.String(12)))
    op.add_column("content_item", sa.Column("bilibili_cid", sa.String(20)))
    op.add_column("content_item", sa.Column("bilibili_page", sa.Integer()))
    op.create_check_constraint(
        "ck_content_item_bilibili_source", "content_item",
        "(bilibili_bvid IS NULL AND bilibili_cid IS NULL AND bilibili_page IS NULL) OR "
        "(bilibili_bvid IS NOT NULL AND bilibili_cid IS NOT NULL AND bilibili_page IS NOT NULL "
        "AND platform = 'bilibili' AND bilibili_bvid ~ '^BV[0-9A-Za-z]{10}$' "
        "AND bilibili_cid ~ '^[1-9][0-9]{0,19}$' AND bilibili_page BETWEEN 1 AND 1000)",
    )
    op.create_index("uq_content_item_user_bilibili_cid", "content_item", ["user_id", "bilibili_bvid", "bilibili_cid"], unique=True, postgresql_where=sa.text("bilibili_cid IS NOT NULL"))


def downgrade() -> None:
    # This intentionally refuses to destroy provenance once any pinned source
    # exists. An application rollback must retain these columns and records.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM content_item WHERE bilibili_cid IS NOT NULL)")):
        raise RuntimeError("pinned Bilibili sources require preserving source schema")
    op.drop_index("uq_content_item_user_bilibili_cid", table_name="content_item")
    op.drop_constraint("ck_content_item_bilibili_source", "content_item", type_="check")
    op.drop_column("content_item", "bilibili_page")
    op.drop_column("content_item", "bilibili_cid")
    op.drop_column("content_item", "bilibili_bvid")
