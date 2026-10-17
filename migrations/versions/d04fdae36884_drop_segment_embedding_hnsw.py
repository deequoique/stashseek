"""drop the global segment embedding HNSW index (tenant filter-first vector search)

Revision ID: d04fdae36884
Revises: b8c9d0e1f2a3
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d04fdae36884"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # vector_search now resolves the tenant's eligible item IDs first, then
    # ranks exactly over `segment.item_id = ANY(:ids)` (see
    # app/retrieval/search.py:filter_first_vector_rank). A global ANN index
    # with a tenant post-filter silently truncates results once the tenant's
    # own rows fall outside the index's approximate candidate window, so
    # nothing may use this index anymore.
    #
    # Plain transactional DDL (no CONCURRENTLY): dropping an index only takes
    # a brief ACCESS EXCLUSIVE lock, the deploy procedure already stops writes
    # before the one-shot migration unit runs, and it keeps this migration
    # compatible with externally managed migration connections such as
    # tests/test_migration_roundtrip_postgres.py.
    op.execute("DROP INDEX IF EXISTS ix_segment_embedding_hnsw")


def downgrade() -> None:
    # Rebuilding blocks writes on `segment` for roughly a minute at ~61k rows
    # (measured 32 s for 41k rows with 512MB maintenance_work_mem); run the
    # downgrade inside the same write-stopped maintenance window.
    op.execute("SET LOCAL maintenance_work_mem = '512MB'")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_segment_embedding_hnsw "
        "ON segment USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
