from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_engine
from app.ingest.stages import (
    BODY_STAGE,
    ENRICHMENT_STAGES,
    KNOWN_STAGES,
    mark_stage,
    stage_sort_key,
)
from app.models import AppUser, Base, ContentItem, ContentItemStage


class NoDatabase:
    def execute(self, *_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("an invalid stage write must not reach the database")


def test_registry_orders_body_first_and_enables_nothing_yet():
    assert KNOWN_STAGES == ("body", "classify", "summary", "movie")
    assert BODY_STAGE == KNOWN_STAGES[0]
    assert ENRICHMENT_STAGES == ()
    assert sorted(["movie", "zzz", "body", "summary", "classify"], key=stage_sort_key) == [
        "body",
        "classify",
        "summary",
        "movie",
        "zzz",
    ]


@pytest.mark.parametrize(
    ("stage", "status", "error_code"),
    [
        ("body", "running", None),  # body is projected, never stored
        ("not_a_stage", "running", None),
        ("classify", "unknown", None),
        ("classify", "failed", None),  # failed needs a code
        ("classify", "failed", "not_in_the_reason_table"),
        ("classify", "done", "ingestion_failed"),  # only failed carries a code
        ("classify", "pending", "ingestion_failed"),
    ],
)
def test_mark_stage_rejects_invalid_writes_before_touching_the_database(
    stage, status, error_code
):
    with pytest.raises(ValueError):
        mark_stage(NoDatabase(), 1, stage, status, error_code=error_code)


@pytest.fixture
def stage_factory():
    engine = get_engine()
    schema = f"test_stage_{uuid4().hex}"
    tables = [AppUser.__table__, ContentItem.__table__, ContentItemStage.__table__]
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            Base.metadata.create_all(connection, tables=tables, checkfirst=False)
    except Exception as exc:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            pytest.skip(f"isolated PostgreSQL schema unavailable: {type(exc).__name__}")

    def factory():
        db = Session(bind=engine, expire_on_commit=False)
        db.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        return db

    try:
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _add_item(factory) -> int:
    with factory() as db:
        user = AppUser()
        db.add(user)
        db.flush()
        item = ContentItem(
            public_id=uuid4().hex,
            user_id=user.id,
            platform="youtube",
            platform_id=uuid4().hex[:11],
            kind="video",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            state="ready",
        )
        db.add(item)
        db.commit()
        return item.id


def _rows(factory, item_id):
    with factory() as db:
        return {
            row.stage: row
            for row in db.scalars(
                select(ContentItemStage).where(ContentItemStage.item_id == item_id)
            )
        }


def test_mark_stage_upserts_one_row_per_item_and_stage(stage_factory):
    item_id = _add_item(stage_factory)

    with stage_factory() as db:
        mark_stage(db, item_id, "classify", "running")
        mark_stage(db, item_id, "summary", "pending")
        db.commit()
    first = _rows(stage_factory, item_id)
    assert {k: v.status for k, v in first.items()} == {
        "classify": "running",
        "summary": "pending",
    }

    with stage_factory() as db:
        mark_stage(db, item_id, "classify", "failed", error_code="asr_provider_failed")
        db.commit()
    failed = _rows(stage_factory, item_id)
    assert (failed["classify"].status, failed["classify"].error_code) == (
        "failed",
        "asr_provider_failed",
    )
    assert failed["classify"].updated_at >= first["classify"].updated_at
    assert failed["summary"].status == "pending"

    # A later success clears the stored failure code (the CHECK couples them).
    with stage_factory() as db:
        mark_stage(db, item_id, "classify", "done")
        db.commit()
    done = _rows(stage_factory, item_id)
    assert (done["classify"].status, done["classify"].error_code) == ("done", None)
    assert len(done) == 2


def test_stage_rows_are_removed_with_their_item(stage_factory):
    item_id = _add_item(stage_factory)
    with stage_factory() as db:
        mark_stage(db, item_id, "classify", "done")
        db.commit()

    with stage_factory() as db:
        db.execute(text("DELETE FROM content_item WHERE id = :id"), {"id": item_id})
        db.commit()

    assert _rows(stage_factory, item_id) == {}


@pytest.mark.parametrize(
    ("stage", "status", "error_code"),
    [
        ("body", "done", None),
        ("classify", "bogus", None),
        ("classify", "failed", None),
        ("classify", "done", "ingestion_failed"),
    ],
)
def test_database_constraints_back_the_same_rules(
    stage_factory, stage, status, error_code
):
    item_id = _add_item(stage_factory)

    with stage_factory() as db:
        db.add(
            ContentItemStage(
                item_id=item_id, stage=stage, status=status, error_code=error_code
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
