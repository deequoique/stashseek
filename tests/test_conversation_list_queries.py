from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.conversation_routes import build_conversation_router
from app.channels.types import TenantContext
from app.config import Settings
from app.models import ConversationThread, ConversationTurn
from app.web_auth import AuthenticatedWebSession, _token_hash
from app.agent.types import AgentAnswer, Citation
from app.agent.streaming import AgentStreamEvent


def _runtime(thread_count: int, channel_service=None):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE conversation_thread (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                app_user_id INTEGER NOT NULL,
                channel_identity_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_conversation_id TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE conversation_turn (
                id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                sources TEXT NOT NULL,
                model_messages TEXT NOT NULL,
                answer_status TEXT NOT NULL DEFAULT 'legacy',
                error_code TEXT,
                action_results TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'completed',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(thread_id, message_id)
            )
            """
        )
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    with factory() as db:
        for index in range(thread_count):
            thread_id = index + 1
            db.add(
                ConversationThread(
                    id=thread_id,
                    public_id=f"thread-{thread_id}",
                    app_user_id=7,
                    channel_identity_id=11,
                    channel="web",
                    account_id="web",
                    external_conversation_id=f"conversation-{thread_id}",
                    updated_at=now - timedelta(minutes=index),
                )
            )
            if index == 0:
                db.add_all(
                    [
                        ConversationTurn(
                            id=1,
                            thread_id=thread_id,
                            message_id="completed",
                            user_text="保留的问题",
                            assistant_text="保留的答案",
                            sources=[],
                            model_messages=[],
                            answer_status="ok",
                            action_results=[],
                            status="completed",
                            created_at=now - timedelta(seconds=2),
                        ),
                        ConversationTurn(
                            id=2,
                            thread_id=thread_id,
                            message_id="failed-later",
                            user_text="不应展示的问题",
                            assistant_text="不应展示的答案",
                            sources=[],
                            model_messages=[],
                            answer_status="failed",
                            action_results=[],
                            status="failed",
                            created_at=now - timedelta(seconds=1),
                        ),
                    ]
                )
        db.add(
            ConversationThread(
                id=10_000,
                public_id="other-tenant-thread",
                app_user_id=8,
                channel_identity_id=12,
                channel="web",
                account_id="web",
                external_conversation_id="other-conversation",
                updated_at=now + timedelta(hours=1),
            )
        )
        db.commit()

    session = AuthenticatedWebSession(
        3,
        TenantContext(7, 11, "web", "web", "person@example.test"),
        now + timedelta(hours=1),
        "session-public",
        _token_hash("csrf"),
    )
    app = FastAPI()
    app.include_router(
        build_conversation_router(
            channel_service=channel_service,
            session_dependency=lambda _request: session,
            session_factory=factory,
            settings=Settings(
                database_url="sqlite://",
                notebook_agent_env="development",
                web_auth_secret="x" * 32,
            ),
        )
    )
    return app, engine


def _part_source_tables(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE content_item (id INTEGER PRIMARY KEY, public_id TEXT, platform TEXT, user_id INTEGER, deleted_at TEXT, bilibili_bvid TEXT, bilibili_cid TEXT, bilibili_page INTEGER)")
        connection.exec_driver_sql("CREATE TABLE segment (id INTEGER PRIMARY KEY, item_id INTEGER)")
        connection.exec_driver_sql("INSERT INTO content_item VALUES (41, 'verified-part', 'bilibili', 7, NULL, 'BV1xx411c7mD', '123', 6), (42, 'foreign-part', 'bilibili', 8, NULL, 'BV1xx411c7mD', '456', 2)")
        connection.exec_driver_sql("INSERT INTO segment VALUES (51, 41), (52, 42)")


def _part_citation():
    return Citation(item_id=41, segment_id=51, title="父视频 · P2 课程", excerpt="来源",
                    url="https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=12", start_sec=12)


@pytest.mark.asyncio
async def test_reopened_history_uses_batched_owned_segment_links_without_rewriting_generic_history():
    app, engine = _runtime(1)
    _part_source_tables(engine)
    stored = [_part_citation().model_dump()] * 8
    stored.append({**stored[0], "item_id": 42, "segment_id": 52})
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE conversation_turn SET sources=? WHERE id=1", (json.dumps(stored),))
    statements = []
    @event.listens_for(engine, "before_cursor_execute")
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.test") as client:
        response = await client.get("/api/v1/conversations/thread-1/turns")
    assert response.status_code == 200
    citations = response.json()["turns"][0]["citations"]
    assert len(citations) == 9
    owned = [value for value in citations if value["url"].startswith("/api/v1/library/items/verified-part/source")]
    foreign = [value for value in citations if not value["url"].startswith("/api/v1/library/items/verified-part/source")]
    assert len(owned) == 8
    assert all(value["url"] == "/api/v1/library/items/verified-part/source?t=12" for value in owned)
    assert [value["url"] for value in foreign] == ["https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=12"]
    assert all(set(value) == {"title", "excerpt", "url", "start_sec"} for value in citations)
    assert len(statements) == 3  # owned thread, public turns, one source join
    with engine.connect() as connection:
        assert json.loads(connection.exec_driver_sql("SELECT sources FROM conversation_turn WHERE id=1").scalar_one()) == stored
    engine.dispose()


@pytest.mark.asyncio
async def test_current_json_stream_sections_and_final_project_the_same_owned_part():
    class Channel:
        async def handle(self, _envelope):
            return AgentAnswer(status="ok", text="回答", citations=[_part_citation()])

        async def handle_stream(self, envelope):
            yield AgentStreamEvent("section_started", envelope.request_id, envelope.message_id,
                section_id="s1", status="grounded", citation_ids=(51,), citations=(_part_citation(),))
            yield AgentStreamEvent("text_delta", envelope.request_id, envelope.message_id,
                section_id="s1", text="回答")
            yield AgentStreamEvent("section_completed", envelope.request_id, envelope.message_id,
                section_id="s1", status="grounded")
            yield AgentStreamEvent("completed", envelope.request_id, envelope.message_id,
                answer=AgentAnswer(status="ok", text="回答", citations=[_part_citation()]))
    app, engine = _runtime(1, Channel())
    _part_source_tables(engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.test") as client:
        direct = await client.post("/api/v1/conversations/conversation-1/messages", json={"message_id": "part-json", "text": "提问"}, headers={"X-CSRF-Token": "csrf"})
        streamed = await client.post("/api/v1/conversations/conversation-1/messages/stream", json={"message_id": "part-stream", "text": "提问"}, headers={"X-CSRF-Token": "csrf"})
    assert direct.status_code == 200
    assert direct.json()["citations"][0]["url"] == "/api/v1/library/items/verified-part/source?t=12"
    events = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith("data: ")]
    section = next(value for value in events if value["type"] == "section_started")
    final = next(value for value in events if value["type"] == "completed")
    assert section["citations"][0]["url"] == direct.json()["citations"][0]["url"]
    assert final["response"]["citations"][0]["url"] == direct.json()["citations"][0]["url"]
    assert "item_id" not in direct.json()["citations"][0]
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("thread_count", "expected_items"), [(0, 0), (1, 1), (30, 30)])
async def test_conversation_page_uses_one_projection_query_at_any_page_size(
    thread_count: int,
    expected_items: int,
):
    app, engine = _runtime(thread_count)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://app.example.test",
    ) as client:
        response = await client.get("/api/v1/conversations?limit=30")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == expected_items
    assert all(item["thread_id"] != "other-tenant-thread" for item in body["items"])
    assert len(statements) == 1
    assert "conversation_page" in statements[0]
    if thread_count:
        assert body["items"][0]["title"] == "保留的问题"
        assert body["items"][0]["preview"] == "保留的答案"


@pytest.mark.asyncio
async def test_conversation_cursor_page_adds_only_one_ownership_query():
    app, engine = _runtime(3)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://app.example.test",
    ) as client:
        first = await client.get("/api/v1/conversations?limit=1")
        assert first.status_code == 200
        cursor = first.json()["next_cursor"]
        statements.clear()
        second = await client.get(
            "/api/v1/conversations",
            params={"limit": 1, "cursor": cursor},
        )

    assert second.status_code == 200
    assert second.json()["items"][0]["thread_id"] == "thread-2"
    assert len(statements) == 2


@pytest.mark.asyncio
async def test_public_transcript_omits_provider_history_without_extra_queries():
    app, engine = _runtime(1)
    with engine.begin() as connection:
        # This internal column must not be transferred or JSON-decoded while
        # rendering the public transcript, even when it contains huge data.
        connection.exec_driver_sql(
            "UPDATE conversation_turn SET model_messages = ? WHERE id = 1",
            ("private provider history " * 100_000,),
        )
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://app.example.test",
    ) as client:
        response = await client.get("/api/v1/conversations/thread-1/turns")

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "thread-1"
    assert body["conversation_id"] == "conversation-1"
    assert len(body["turns"]) == 1
    assert body["turns"][0]["user_text"] == "保留的问题"
    assert body["turns"][0]["assistant_text"] == "保留的答案"
    assert body["turns"][0]["status"] == "ok"
    assert body["turns"][0]["citations"] == []
    assert "model_messages" not in response.text
    assert len(statements) == 2
    assert all("model_messages" not in statement for statement in statements)


@pytest.mark.asyncio
async def test_public_transcript_keeps_completed_order_and_rejects_foreign_threads():
    app, engine = _runtime(1)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """INSERT INTO conversation_turn
            (id, thread_id, message_id, user_text, assistant_text, sources,
             model_messages, answer_status, status, created_at)
            VALUES (3, 1, 'later-completed', '第二个问题', '第二个答案', '[]',
                    '[]', 'ok', 'completed', '2026-08-22 12:00:01')"""
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://app.example.test",
    ) as client:
        own = await client.get("/api/v1/conversations/thread-1/turns")
        foreign = await client.get("/api/v1/conversations/other-tenant-thread/turns")
        missing = await client.get("/api/v1/conversations/missing/turns")

    assert own.status_code == 200
    assert [turn["user_text"] for turn in own.json()["turns"]] == [
        "保留的问题", "第二个问题",
    ]
    assert foreign.status_code == missing.status_code == 404


def _stored_source(segment_id: int, *, url: str | None = None, **extra) -> dict:
    return {
        "item_id": 7,
        "segment_id": segment_id,
        "title": f"来源{segment_id}",
        "excerpt": f"字幕{segment_id}",
        "url": url or f"https://example.test/video?t={segment_id}",
        "start_sec": float(segment_id),
        **extra,
    }


@pytest.mark.asyncio
async def test_history_markers_are_renumbered_by_public_citation_position():
    app, engine = _runtime(1)
    stored = [
        _stored_source(11),
        _stored_source(12, url="ftp://bad.example/x"),  # dropped by the projection
        _stored_source(13, start_sec=None, kind="title"),
        _stored_source(14),
    ]
    text = (
        "答 [S11] [S12] [S13]\n\n又 [S14] [S11]\n\n来源：\n- 视频\n"
        "  - [S11] https://example.test/video?t=11 — 字幕11\n"
        "  - [S14] https://example.test/video?t=14 — 字幕14"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE conversation_turn SET assistant_text=?, sources=? WHERE id=1",
            (text, json.dumps(stored)),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.test"
    ) as client:
        response = await client.get("/api/v1/conversations/thread-1/turns")
    assert response.status_code == 200
    turn = response.json()["turns"][0]
    assert [value["title"] for value in turn["citations"]] == ["来源11", "来源13", "来源14"]
    assert turn["assistant_text"].startswith("答 [^1] [^2]\n\n又 [^3] [^1]")
    assert "[S" not in turn["assistant_text"]
    assert "  - [^3] https://example.test/video?t=14 — 字幕14" in turn["assistant_text"]
    assert all(set(value) == {"title", "excerpt", "url", "start_sec"} for value in turn["citations"])
    # Persisted text is untouched: the numbering is a pure projection.
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT assistant_text FROM conversation_turn WHERE id=1"
        ).scalar_one() == text
    engine.dispose()


@pytest.mark.asyncio
async def test_history_without_stored_segment_ids_never_leaks_markers():
    app, engine = _runtime(1)
    legacy = {key: value for key, value in _stored_source(21).items() if key != "segment_id"}
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE conversation_turn SET assistant_text=?, sources=? WHERE id=1",
            ("旧回答 [S21] 结尾", json.dumps([legacy])),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.test"
    ) as client:
        response = await client.get("/api/v1/conversations/thread-1/turns")
    turn = response.json()["turns"][0]
    assert turn["assistant_text"] == "旧回答 结尾"
    assert len(turn["citations"]) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_conversation_preview_strips_internal_markers():
    app, engine = _runtime(1)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE conversation_turn SET assistant_text=? WHERE id=1",
            ("结论 [S31] [S32] 补充 [S31]",),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.example.test"
    ) as client:
        response = await client.get("/api/v1/conversations?limit=5")
    assert response.status_code == 200
    preview = response.json()["items"][0]["preview"]
    assert preview == "结论 补充"
    engine.dispose()
