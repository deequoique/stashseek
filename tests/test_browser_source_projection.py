from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.web.source_projection import (
    has_verified_bilibili_source, item_source_url, project_browser_sources,
)


class DB:
    def __init__(self, rows=()):
        self.rows = rows
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return self.rows


def source(segment=11, item=7, **changes):
    return {
        "item_id": item, "segment_id": segment,
        "title": "父视频 · P2 课程", "excerpt": "字幕",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=12",
        "start_sec": 12.9, **changes,
    }


def test_only_owned_provenance_can_project_a_browser_resolver():
    db = DB([(11, 7, "item-public", "bilibili", "BV1xx411c7mD", "123", 2)])
    projected = project_browser_sources(db, 9, [source(), source(item=8), source(segment=12)])
    assert projected[0]["url"] == "/api/v1/library/items/item-public/source?t=12"
    assert set(projected[0]) == {"title", "excerpt", "url", "start_sec"}
    assert projected[1:] == [None, None]
    assert len(db.statements) == 1
    sql = str(db.statements[0])
    assert "content_item.user_id =" in sql
    assert "content_item.deleted_at IS NULL" in sql
    assert "content_item.id = segment.item_id" in sql


def test_model_url_and_historical_ordinal_never_supply_cid_identity():
    db = DB([(11, 7, "item-public", "bilibili", "BV1xx411c7mD", "123", 6)])
    relative = source(url="/api/v1/library/items/forged/source?t=12")
    values = project_browser_sources(db, 9, [source(), relative])
    assert values[0]["url"] == "/api/v1/library/items/item-public/source?t=12"
    assert values[1] is None


def test_legacy_source_without_cid_retains_honest_absolute_link():
    db = DB([(11, 7, "legacy", "bilibili", None, None, None)])
    value = source()
    values = project_browser_sources(db, 9, [value, source(segment=None, item=None)])
    assert [item["url"] for item in values] == [value["url"], value["url"]]
    assert len(db.statements) == 1


def test_non_bilibili_generic_sources_do_not_add_database_work():
    db = DB()
    values = project_browser_sources(db, 9, [source(url="https://youtu.be/example?t=12")])
    assert values[0]["url"] == "https://youtu.be/example?t=12"
    assert db.statements == []


def test_history_projection_batches_repeated_and_large_source_sets():
    db = DB()
    project_browser_sources(db, 9, [source(segment=index + 1) for index in range(1200)])
    assert len(db.statements) == 3
    db = DB()
    project_browser_sources(db, 9, [source()] * 50)
    assert len(db.statements) == 1


def test_source_descriptor_and_time_bounds_are_honest():
    assert not has_verified_bilibili_source(SimpleNamespace(platform="bilibili", bilibili_page=1))
    assert has_verified_bilibili_source(SimpleNamespace(platform="bilibili", bilibili_bvid="BV1xx411c7mD", bilibili_cid="123", bilibili_page=2))
    assert item_source_url("safe/id", float("inf")) == "/api/v1/library/items/safe%2Fid/source?t=0"
    assert item_source_url("item", 3_000_000_000) == "/api/v1/library/items/item/source?t=2147483647"


def test_real_owned_segment_join_omits_foreign_deleted_and_forged_pairs():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE content_item (id INTEGER PRIMARY KEY, public_id TEXT, platform TEXT, user_id INTEGER, deleted_at TEXT, bilibili_bvid TEXT, bilibili_cid TEXT, bilibili_page INTEGER)")
        connection.exec_driver_sql("CREATE TABLE segment (id INTEGER PRIMARY KEY, item_id INTEGER)")
        connection.exec_driver_sql("INSERT INTO content_item VALUES (7, 'owned', 'bilibili', 9, NULL, 'BV1xx411c7mD', '123', 2), (8, 'foreign', 'bilibili', 10, NULL, 'BV1xx411c7mD', '456', 3), (9, 'deleted', 'bilibili', 9, '2026-10-16', 'BV1xx411c7mD', '789', 4)")
        connection.exec_driver_sql("INSERT INTO segment VALUES (11, 7), (12, 8), (13, 9)")
    with Session(engine) as db:
        projected = project_browser_sources(db, 9, [source(), source(segment=12, item=8), source(segment=13, item=9), source(item=8)])
    assert projected[0]["url"] == "/api/v1/library/items/owned/source?t=12"
    assert projected[1:] == [None, None, None]
    engine.dispose()
