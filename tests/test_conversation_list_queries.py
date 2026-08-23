from datetime import UTC, datetime, timedelta

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


def _runtime(thread_count: int):
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
            channel_service=None,
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
