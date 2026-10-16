"""Real isolated PostgreSQL source constraints, source opening and admission races."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import os
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.bilibili_schemas import BilibiliPartsBatchRequest, BilibiliPartsPreviewResponse
from app.bilibili.public import BilibiliPart, BilibiliPartMap, BilibiliPublicError
from app.bilibili.refs import PartRefSigner
from app.bilibili.service import BilibiliService
from app.channels.types import UserScope
from app.ingest.submission import IngestSubmissionService
from app.models import AppUser, Base, ChannelIdentity, ContentItem, ConversationThread, IngestDispatch


BV = "BV1xx411c7mD"


@pytest.fixture
def system():
    url = os.environ.get("STASHSEEK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit isolated STASHSEEK_TEST_DATABASE_URL required")
    schema = "test_bilibili_parts_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    tables = [model.__table__ for model in (AppUser, ChannelIdentity, ContentItem, ConversationThread, IngestDispatch)]
    with engine.begin() as db:
        Base.metadata.create_all(db, tables=tables)
    def factory():
        return Session(engine, expire_on_commit=False)
    with factory() as db:
        user = AppUser()
        other = AppUser()
        db.add_all([user, other]); db.commit()
        scope, foreign = UserScope(user.id), UserScope(other.id)
    published = []
    submission = IngestSubmissionService(factory, lambda item: published.append(item), retention_days=30, max_active_per_tenant=10, daily_new_item_limit=50)
    signer = PartRefSigner("s" * 32)
    class Gate:
        def __init__(self):
            self.calls = 0
        def admit(self, tenant):
            self.calls += 1
        @contextmanager
        def upstream(self):
            yield
    gate = Gate()
    class Public:
        def __init__(self):
            self.parts = (BilibiliPart(BV, "101", 1, "One"), BilibiliPart(BV, "102", 2, "Two"))
            self.calls = 0
        def get_parts(self, bvid):
            assert bvid == BV
            self.calls += 1
            return BilibiliPartMap(BV, "Video", self.parts)
    public = Public()
    service = BilibiliService(factory, submission, public, signer, gate)
    try:
        yield SimpleNamespace(factory=factory, scope=scope, foreign=foreign, published=published, submission=submission, signer=signer, service=service, public=public, engine=engine, schema=schema, admin=admin)
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def body(system, cid="101", page=1, *, overlap=False):
    return BilibiliPartsBatchRequest(selections=[{"part_ref": system.signer.issue(system.scope.app_user_id, BilibiliPart(BV, cid, page)), "allow_legacy_overlap": overlap}])


def submit(system, data, key="test"):
    return system.service.submit_parts(system.scope, data.selections, why_saved="#study", request_key=key, publish_budget_seconds=2).results[0]


def seed(system, **overrides):
    values = dict(public_id=uuid4().hex, user_id=system.scope.app_user_id, platform="bilibili", platform_id=BV, kind="video", url=f"https://www.bilibili.com/video/{BV}", state="ready", why_saved="keep old note")
    values.update(overrides)
    with system.factory() as db:
        item = ContentItem(**values)
        db.add(item); db.commit()
        return item


def test_preview_has_no_writes_or_queue_and_reports_legacy(system):
    seed(system)
    with system.factory() as db:
        before = db.scalar(select(func.count()).select_from(ContentItem))
    result = BilibiliPartsPreviewResponse.model_validate(system.service.preview_parts(system.scope, BV))
    assert len(result.parts) == 2 and all(row.has_unverified_legacy_item for row in result.parts)
    assert result.requested_page is None
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(ContentItem)) == before
        assert db.scalar(select(func.count()).select_from(IngestDispatch)) == 0
    assert not system.published


def test_preview_direct_p_local_pagination_and_explicit_selection_none(system):
    system.public.parts = tuple(BilibiliPart(BV, str(100 + p), p, f"Part{p}") for p in range(1, 43))
    result = system.service.preview_parts(system.scope, f"https://www.bilibili.com/video/{BV}?p=42")
    assert result["page"] == 3 and result["requested_page"] == 42
    assert [row["page"] for row in result["parts"]] == [41, 42]
    assert not result["has_more"]
    assert not system.published


def test_unknown_legacy_rejected_until_each_entry_consents_then_history_preserved(system):
    legacy = seed(system)
    denied = submit(system, body(system))
    assert denied.status == "legacy_identity_unknown" and not system.published
    created = submit(system, body(system, overlap=True))
    assert created.status == "queued" and created.item_public_id != legacy.public_id
    with system.factory() as db:
        unchanged = db.get(ContentItem, legacy.id)
        assert unchanged.bilibili_cid is None and unchanged.why_saved == "keep old note"
        verified = db.get(ContentItem, created.item_id)
        assert (verified.bilibili_bvid, verified.bilibili_cid, verified.bilibili_page) == (BV, "101", 1)
        assert verified.platform_id == BV + "_cid101"
        assert db.scalar(select(func.count()).select_from(ContentItem)) == 2


def test_known_legacy_cid_is_reused_without_relabel_or_dispatch(system):
    legacy = seed(system, bilibili_bvid=BV, bilibili_cid="101", bilibili_page=2)
    result = submit(system, body(system))
    assert result.status == "already_exists" and result.item_public_id == legacy.public_id
    assert not system.published


def test_known_cid_same_owner_concurrent_save_one_item_and_dispatch(system):
    start = Barrier(2)
    data = body(system)
    def attempt(index):
        start.wait(timeout=5)
        return submit(system, data, key=f"concurrent:{index}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert {row.status for row in results} <= {"queued", "already_exists"}
    assert results[0].item_public_id == results[1].item_public_id
    assert len(system.published) == 1
    with system.factory() as db:
        assert db.scalar(select(func.count()).select_from(ContentItem)) == 1
        assert db.scalar(select(func.count()).select_from(IngestDispatch)) == 1


def test_distinct_parts_and_replayed_operation_keep_independent_ids(system):
    first = submit(system, body(system), key="same")
    again = submit(system, body(system), key="same")
    second = submit(system, body(system, "102", 2), key="other")
    assert first.item_public_id == again.item_public_id != second.item_public_id
    assert len(system.published) == 2


def test_other_tenant_proof_never_creates_item(system):
    forged = BilibiliPartsBatchRequest(selections=[{"part_ref": system.signer.issue(system.foreign.app_user_id, BilibiliPart(BV, "101", 1))}])
    assert submit(system, forged).status == "part_ref_invalid"
    assert not system.published


def test_source_resolves_current_cid_position_and_keeps_bounded_local_time(system):
    result = submit(system, body(system, "102", 2))
    system.public.parts = (BilibiliPart(BV, "102", 1), BilibiliPart(BV, "101", 2))
    assert system.service.open_source(system.scope, result.item_public_id, timestamp=42) == f"https://www.bilibili.com/video/{BV}?p=1&t=42"
    with pytest.raises(BilibiliPublicError, match="not_found"):
        system.service.open_source(system.foreign, result.item_public_id)
    system.public.parts = (BilibiliPart(BV, "101", 1),)
    with pytest.raises(BilibiliPublicError, match="source_unavailable"):
        system.service.open_source(system.scope, result.item_public_id)


@pytest.mark.parametrize("values", [
    {"bilibili_bvid": BV}, {"bilibili_bvid": BV, "bilibili_cid": "101", "bilibili_page": 0},
    {"bilibili_bvid": BV, "bilibili_cid": "001", "bilibili_page": 1},
    {"bilibili_bvid": BV, "bilibili_cid": "101", "bilibili_page": 1001},
    {"platform": "youtube", "bilibili_bvid": BV, "bilibili_cid": "101", "bilibili_page": 1},
])
def test_database_rejects_partial_or_invalid_typed_source(system, values):
    with pytest.raises(IntegrityError):
        seed(system, **values)


def test_source_migration_roundtrip_retains_legacy_null_and_is_single_head(system):
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_current_head() == "f8a9b0c1d2e3"
    revision = importlib.import_module("migrations.versions.f8a9b0c1d2e3_bilibili_part_sources")
    schema = system.schema + "_migration"
    with system.admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
        db.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        db.execute(text("CREATE TABLE content_item (id BIGINT PRIMARY KEY, user_id BIGINT NOT NULL, platform TEXT NOT NULL, title TEXT)"))
        db.execute(text("INSERT INTO content_item VALUES (1,1,'bilibili','historical title')"))
        with Operations.context(MigrationContext.configure(db)):
            revision.upgrade()
        row = db.execute(text("SELECT title,bilibili_bvid,bilibili_cid,bilibili_page FROM content_item")).one()
        assert row == ("historical title", None, None, None)
        db.execute(text("INSERT INTO content_item VALUES (2,1,'bilibili','verified',:bv,'101',1)"), {"bv": BV})
        with pytest.raises(RuntimeError, match="preserving source schema"):
            with Operations.context(MigrationContext.configure(db)):
                revision.downgrade()
        db.execute(text("DELETE FROM content_item WHERE id=2"))
        with Operations.context(MigrationContext.configure(db)):
            revision.downgrade()
            revision.upgrade()
        assert db.execute(text("SELECT bilibili_cid FROM content_item")).scalar() is None
        db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
