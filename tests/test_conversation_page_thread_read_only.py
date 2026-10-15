"""Page Q&A threads are read-only on the ordinary conversation routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlalchemy import select

from app.agent.types import AgentAnswer
from app.api.app import WebApiServices, create_app
from app.channels.conversations import get_or_create_thread, save_completed_turn
from app.channels.types import ChannelEnvelope
from app.models import ConversationThread
from app.page_qa import PAGE_QA_STATUS_GROUNDED
from tests.page_qa_support import CONVERSATION_ID, TENANT, sqlite_factory

ORIGIN = "https://kb.example.test"
EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32
BEARER = {"Authorization": "Bearer extension-session", "Origin": EXTENSION_ORIGIN}


class ExtensionSessions:
    def resolve(self, token):
        if token != "extension-session":
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        session = SimpleNamespace(
            tenant=TENANT, expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        return session, TENANT.app_user_id, "user@example.test"


class Channel:
    def __init__(self):
        self.envelopes = []

    async def handle(self, envelope):
        self.envelopes.append(envelope)
        return AgentAnswer(status="ok", text="answer")

    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        raise AssertionError("page threads must never reach the library agent")
        yield  # pragma: no cover


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service call: {name}")


def _setup():
    factory = sqlite_factory()
    with factory() as db:
        envelope = ChannelEnvelope(
            TENANT.channel, TENANT.account_id, TENANT.external_user_id,
            CONVERSATION_ID, "m-1", "视频讲了什么?",
        )
        thread = get_or_create_thread(db, TENANT, envelope)
        save_completed_turn(
            db,
            thread=thread,
            envelope=envelope,
            assistant_text="讲了梯度下降 [S1]",
            sources=[],
            model_messages=[
                ModelRequest(parts=[UserPromptPart("视频讲了什么?")]),
                ModelResponse(parts=[TextPart("讲了梯度下降 [P1]")]),
            ],
            answer_status=PAGE_QA_STATUS_GROUNDED,
        )
        db.commit()
        thread_public_id = thread.public_id
    channel = Channel()
    app = create_app(
        services=WebApiServices(
            web_auth=Unused(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            channel_service=channel,
            session_resolver=lambda _token: Unused(),
            session_identity_resolver=lambda _session: TENANT,
            session_factory=factory,
            settings=SimpleNamespace(
                agent_timeout_seconds=5.0,
                agent_streaming_enabled=True,
                browser_companion_allowed_origins=(EXTENSION_ORIGIN,),
                browser_companion_max_request_bytes=5_500_000,
            ),
            extension_sessions=ExtensionSessions(),
            include_page_qa_routes=False,
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
    )
    return TestClient(app, base_url=ORIGIN), channel, factory, thread_public_id


def assert_read_only(response):
    assert response.status_code == 409
    assert response.json() == {
        "code": "page_thread_read_only",
        "message": "这是视频页问答记录，请回到视频页继续提问。",
    }


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("messages", {"message_id": "m-2", "text": "再问一个"}),
        ("messages/stream", {"message_id": "m-2", "text": "再问一个"}),
        ("reset", None),
    ],
)
def test_page_threads_reject_messages_stream_and_reset(suffix, body):
    client, channel, _, _ = _setup()

    response = client.post(
        f"/api/v1/conversations/{CONVERSATION_ID}/{suffix}", json=body, headers=BEARER
    )

    assert_read_only(response)
    assert channel.envelopes == []


@pytest.mark.parametrize("suffix", ["messages", "messages/stream", "reset"])
def test_whitespace_padded_page_ids_are_rejected_too(suffix):
    client, channel, _, _ = _setup()
    body = None if suffix == "reset" else {"message_id": "m-2", "text": "hi"}

    response = client.post(
        f"/api/v1/conversations/%20{CONVERSATION_ID}/{suffix}", json=body, headers=BEARER
    )

    assert_read_only(response)
    assert channel.envelopes == []


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("messages", {"message_id": "m-2", "text": "hello"}),
        ("reset", None),
    ],
)
def test_ordinary_conversation_ids_are_unaffected(suffix, body):
    client, channel, _, _ = _setup()

    response = client.post(
        f"/api/v1/conversations/web-thread-1/{suffix}", json=body, headers=BEARER
    )

    assert response.status_code == 200, response.text
    assert len(channel.envelopes) == 1


def test_ids_that_merely_contain_page_are_not_page_threads():
    client, channel, _, _ = _setup()

    response = client.post(
        "/api/v1/conversations/my-page-notes/messages",
        json={"message_id": "m-2", "text": "hello"},
        headers=BEARER,
    )

    assert response.status_code == 200
    assert len(channel.envelopes) == 1


def test_page_threads_can_still_be_listed_read_and_deleted():
    client, channel, factory, thread_id = _setup()

    listing = client.get("/api/v1/conversations", headers=BEARER).json()
    assert [(item["thread_id"], item["page_qa"]) for item in listing["items"]] == [
        (thread_id, True)
    ]
    turns = client.get(f"/api/v1/conversations/{thread_id}/turns", headers=BEARER)
    assert turns.status_code == 200
    payload = turns.json()
    assert payload["page_qa"] is True
    assert [turn["user_text"] for turn in payload["turns"]] == ["视频讲了什么?"]

    deleted = client.delete(f"/api/v1/conversations/{thread_id}", headers=BEARER)

    assert deleted.status_code == 204
    with factory() as db:
        assert db.scalars(select(ConversationThread)).all() == []
    assert channel.envelopes == []
