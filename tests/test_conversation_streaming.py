from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.types import AgentAnswer
from app.agent.streaming import AgentPlanItem, AgentStreamEvent
from app.agent.types import Citation
from app.config import Settings
from app.models import AppUser, ChannelIdentity, WebAuthChallenge, WebSession
from app.web.auth import CSRF_COOKIE_NAME
from app.web_api import build_web_auth_service, create_web_app
from app.web_auth import InMemoryEmailSender, InMemoryLoginRateLimiter, WebAuthService


ORIGIN = "https://streaming.example.test"


class _ChannelService:
    def __init__(self, answer: AgentAnswer | None = None):
        self.envelopes = []
        self.answer = answer or AgentAnswer(
            status="ok",
            text="这是一个分段传输的安全答案。",
        )

    async def handle(self, envelope):
        self.envelopes.append(envelope)
        return self.answer


class _BrokenChannel(_ChannelService):
    async def handle(self, envelope):
        self.envelopes.append(envelope)
        raise RuntimeError("provider payload PRIVATE_SENTINEL must not reach browser")


class _SlowChannel(_ChannelService):
    async def handle(self, envelope):
        self.envelopes.append(envelope)
        await asyncio.sleep(0.05)
        return self.answer


class _SectionStreamingChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        citation = Citation(
            item_id=7,
            segment_id=11,
            title="公开来源",
            excerpt="字幕依据",
            url="https://example.test/video?t=11",
            start_sec=11,
        )
        yield AgentStreamEvent(
            "activity", envelope.request_id, envelope.message_id, activity="planning_answer"
        )
        yield AgentStreamEvent(
            "section_started", envelope.request_id, envelope.message_id,
            section_id="section-1", status="grounded", citation_ids=(11,), citations=(citation,)
        )
        yield AgentStreamEvent(
            "text_delta", envelope.request_id, envelope.message_id,
            section_id="section-1", text="第一段"
        )
        yield AgentStreamEvent(
            "text_delta", envelope.request_id, envelope.message_id,
            section_id="section-1", text="第二段"
        )
        yield AgentStreamEvent(
            "section_completed", envelope.request_id, envelope.message_id,
            section_id="section-1", status="grounded"
        )
        yield AgentStreamEvent(
            "section_started", envelope.request_id, envelope.message_id,
            section_id="section-2", status="unsupported"
        )
        yield AgentStreamEvent(
            "text_delta", envelope.request_id, envelope.message_id,
            section_id="section-2", text="当前检索证据不足以确认该部分。"
        )
        yield AgentStreamEvent(
            "section_completed", envelope.request_id, envelope.message_id,
            section_id="section-2", status="unsupported"
        )
        yield AgentStreamEvent(
            "completed", envelope.request_id, envelope.message_id,
            answer=AgentAnswer(
                status="ok",
                text="第一段第二段\n\n当前检索证据不足以确认该部分。",
                citations=[citation],
                thread_id="thread-1",
            ),
            new_messages=(),
        )


class _CompletedOnlyChannel(_ChannelService):
    """Simulate a real channel whose provider only supports whole answers."""

    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield AgentStreamEvent(
            "completed",
            envelope.request_id,
            envelope.message_id,
            answer=self.answer,
        )


class _StepStreamingChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield AgentStreamEvent(
            "step_started",
            envelope.request_id,
            envelope.message_id,
            step_id="step-1",
            step_code="searching_library",
        )
        yield AgentStreamEvent(
            "step_completed",
            envelope.request_id,
            envelope.message_id,
            step_id="step-1",
            step_code="searching_library",
            step_outcome="completed",
            result_count=5,
        )
        yield AgentStreamEvent(
            "plan_updated",
            envelope.request_id,
            envelope.message_id,
            plan=(
                AgentPlanItem("find", "检索相关视频", "completed"),
                AgentPlanItem("answer", "整理回答", "in_progress"),
            ),
        )
        yield AgentStreamEvent(
            "completed",
            envelope.request_id,
            envelope.message_id,
            answer=self.answer,
        )


def _section_started(envelope, *, request_id: str | None = None):
    citation = Citation(
        item_id=7,
        segment_id=11,
        title="公开来源",
        excerpt="字幕依据",
        url="https://example.test/video?t=11",
        start_sec=11,
    )
    return AgentStreamEvent(
        "section_started",
        request_id or envelope.request_id,
        envelope.message_id,
        section_id="section-1",
        status="grounded",
        citation_ids=(11,),
        citations=(citation,),
    )


class _MismatchedCorrelationChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield _section_started(envelope)
        yield AgentStreamEvent(
            "text_delta",
            "wrong-request",
            envelope.message_id,
            section_id="section-1",
            text="不应公开",
        )


class _OpenSectionEofChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield _section_started(envelope)


class _OpenSectionSlowChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield _section_started(envelope)
        await asyncio.sleep(0.05)


class _OpenStepEofChannel(_ChannelService):
    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        yield AgentStreamEvent(
            "step_started",
            envelope.request_id,
            envelope.message_id,
            step_id="step-1",
            step_code="searching_library",
        )


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite://",
        "notebook_agent_env": "development",
        "web_auth_enabled": True,
        "web_public_origin": ORIGIN,
        "web_auth_secret": "x" * 32,
        "browser_companion_allowed_origins": (),
        "email_provider": None,
        "agent_streaming_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def _app(settings: Settings, channel: _ChannelService):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        AppUser.__table__,
        ChannelIdentity.__table__,
        WebAuthChallenge.__table__,
        WebSession.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    sender = InMemoryEmailSender()
    auth: WebAuthService = WebAuthService(
        factory, settings, sender, InMemoryLoginRateLimiter(settings)
    )
    return create_web_app(
        settings=settings,
        session_factory=factory,
        auth_service=auth,
        channel_service=channel,
    ), sender


async def _authenticated_client(settings: Settings, channel: _ChannelService):
    app, sender = _app(settings, channel)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url=ORIGIN)
    origin = {"Origin": ORIGIN}
    challenge = await client.post(
        "/api/v1/auth/challenges",
        json={"email": "streaming@example.test"},
        headers=origin,
    )
    assert challenge.status_code == 200
    verify = await client.post(
        "/api/v1/auth/verify",
        json={"email": "streaming@example.test", "code": sender.messages[-1].code},
        headers=origin,
    )
    assert verify.status_code == 200
    return client, origin, client.cookies.get(CSRF_COOKIE_NAME), channel


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_agent_streaming_enabled_accepts_true_environment_values(monkeypatch, value):
    monkeypatch.setenv("AGENT_STREAMING_ENABLED", value)
    assert Settings(database_url="sqlite://", notebook_agent_env="development").agent_streaming_enabled


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_agent_streaming_enabled_accepts_false_environment_values(monkeypatch, value):
    monkeypatch.setenv("AGENT_STREAMING_ENABLED", value)
    assert not Settings(database_url="sqlite://", notebook_agent_env="development").agent_streaming_enabled


def test_agent_streaming_enabled_defaults_on_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("AGENT_STREAMING_ENABLED", raising=False)
    assert Settings(database_url="sqlite://", notebook_agent_env="development").agent_streaming_enabled
    monkeypatch.setenv("AGENT_STREAMING_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="AGENT_STREAMING_ENABLED"):
        Settings(database_url="sqlite://", notebook_agent_env="development")


@pytest.mark.asyncio
async def test_stream_emits_ordered_safe_sse_events_and_keeps_json_route():
    settings = _settings()
    channel = _ChannelService()
    client, origin, csrf, channel = await _authenticated_client(settings, channel)
    headers = {**origin, "X-CSRF-Token": csrf}
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-1", "text": "hello"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        records = [
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        events = [json.loads(record) for record in records]
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "activity",
            "text_delta",
            "completed",
        ]
        assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
        assert len({event["request_id"] for event in events}) == 1
        assert events[-1]["response"]["text"] == "这是一个分段传输的安全答案。"
        assert events[1]["activity"] == "retrieving"
        assert "PRIVATE" not in response.text

        json_response = await client.post(
            "/api/v1/conversations/browser-thread/messages",
            json={"message_id": "message-2", "text": "hello"},
            headers=headers,
        )
        assert json_response.status_code == 200
        assert json_response.json()["text"] == "这是一个分段传输的安全答案。"
        assert len(channel.envelopes) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_projects_section_lifecycle_and_citations_before_deltas():
    settings = _settings()
    channel = _SectionStreamingChannel()
    client, origin, csrf, channel = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-sections", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "activity",
            "section_started",
            "text_delta",
            "text_delta",
            "section_completed",
            "section_started",
            "text_delta",
            "section_completed",
            "completed",
        ]
        first_delta = next(index for index, event in enumerate(events) if event["type"] == "text_delta")
        assert events[first_delta - 1]["type"] == "section_started"
        assert events[first_delta - 1]["citation_ids"] == [11]
        assert events[first_delta - 1]["citations"][0]["title"] == "公开来源"
        assert events[first_delta]["section_id"] == "section-1"
        assert events[first_delta + 1]["text"] == "第二段"
        unsupported = next(event for event in events if event.get("section_id") == "section-2" and event["type"] == "section_started")
        assert unsupported["status"] == "unsupported"
        assert unsupported["citation_ids"] == []
        assert events[-1]["response"]["text"] == "第一段第二段\n\n当前检索证据不足以确认该部分。"
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert len(channel.envelopes) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_provider_fallback_keeps_one_delta_with_handle_stream():
    settings = _settings()
    channel = _CompletedOnlyChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-fallback", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "text_delta",
            "completed",
        ]
        assert events[-2]["text"] == "这是一个分段传输的安全答案。"
        assert len(channel.envelopes) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_projects_safe_execution_steps_and_plan():
    settings = _settings()
    channel = _StepStreamingChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-steps", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]

        assert response.status_code == 200
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "step_started",
            "step_completed",
            "plan_updated",
            "text_delta",
            "completed",
        ]
        assert events[2] == {
            "type": "step_started",
            "request_id": events[0]["request_id"],
            "message_id": "message-steps",
            "sequence": 3,
            "step_id": "step-1",
            "step_code": "searching_library",
            "citation_ids": [],
            "citations": [],
        }
        assert events[3]["step_outcome"] == "completed"
        assert events[3]["result_count"] == 5
        assert events[4]["plan"] == [
            {"id": "find", "title": "检索相关视频", "status": "completed"},
            {"id": "answer", "title": "整理回答", "status": "in_progress"},
        ]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_rejects_internal_correlation_mismatch_after_aborting_section():
    settings = _settings()
    channel = _MismatchedCorrelationChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-correlation", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == [
            "started",
            "activity",
            "section_started",
            "section_aborted",
            "error",
        ]
        assert events[-2]["reason"] == "provider_failure"
        assert events[-1]["error_code"] == "request_failed"
        assert "不应公开" not in response.text
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_eof_after_open_section_aborts_before_error():
    settings = _settings()
    channel = _OpenSectionEofChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-eof", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-2]["type"] == "section_aborted"
        assert events[-1]["type"] == "error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_eof_closes_open_step_before_error():
    settings = _settings()
    channel = _OpenStepEofChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-step-eof", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["type"] for event in events[-3:]] == [
            "step_started",
            "step_completed",
            "error",
        ]
        assert events[-2]["step_outcome"] == "failed"
    finally:
        await client.aclose()


def test_plan_item_rejects_server_owned_data():
    with pytest.raises(ValueError):
        AgentPlanItem("read", "读取 item_id=7 的 raw payload", "in_progress")


@pytest.mark.asyncio
async def test_stream_timeout_after_open_section_aborts_before_error(monkeypatch):
    import app.api.conversation_routes as conversation_routes

    monkeypatch.setattr(
        conversation_routes,
        "_web_agent_transport_timeout",
        lambda _settings: 0.01,
    )
    settings = _settings()
    channel = _OpenSectionSlowChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-timeout-section", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-2]["type"] == "section_aborted"
        assert events[-2]["reason"] == "timeout"
        assert events[-1]["type"] == "error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_disabled_returns_safe_negotiation_error_without_invoking_service():
    settings = _settings(agent_streaming_enabled=False)
    channel = _ChannelService()
    client, origin, csrf, channel = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-disabled", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 406
        assert response.json()["code"] == "streaming_disabled"
        assert channel.envelopes == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_cancelled_answer_has_terminal_cancelled_event():
    class CancelledChannel(_ChannelService):
        async def handle(self, envelope):
            self.envelopes.append(envelope)
            raise asyncio.CancelledError

    settings = _settings()
    channel = CancelledChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-cancelled", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["type"] == "cancelled"
        assert events[-1]["error_code"] == "cancelled"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_provider_failure_is_safe_and_terminal():
    settings = _settings()
    channel = _BrokenChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-provider-failed", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "request_failed"
        assert "PRIVATE_SENTINEL" not in response.text
        assert events[-1]["response"]["text"] == "请求无法完成，请稍后重试。"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_projects_unknown_answer_error_code_to_safe_value():
    settings = _settings()
    channel = _ChannelService(
        answer=AgentAnswer(
            status="failed",
            text="请求无法完成，请稍后重试。",
            error_code="PRIVATE_ERROR_CODE",
        )
    )
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-private-error", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert "PRIVATE_ERROR_CODE" not in response.text
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "request_failed"
        assert events[-1]["response"]["error_code"] == "request_failed"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_timeout_is_safe_and_terminal_without_partial_answer():
    settings = _settings(agent_timeout_seconds=0.001)
    channel = _SlowChannel()
    client, origin, csrf, _ = await _authenticated_client(settings, channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": "message-timeout", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "timeout"
        assert events[-1]["response"]["text"] == "请求超时，请稍后重试。"
        assert all(event["type"] != "completed" for event in events)
    finally:
        await client.aclose()


def _numbered_citation(segment_id: int, *, url: str | None = None) -> Citation:
    return Citation(
        item_id=7,
        segment_id=segment_id,
        title=f"来源{segment_id}",
        excerpt=f"字幕{segment_id}",
        url=url or f"https://example.test/video?t={segment_id}",
        start_sec=float(segment_id),
    )


def _sources_block(citations) -> str:
    lines = ["", "", "来源：", "- 视频"]
    lines.extend(f"  - [S{value.segment_id}] {value.url} — {value.excerpt}" for value in citations)
    return "\n".join(lines)


class _MultiSectionChannel(_ChannelService):
    """Two grounded sections; ``dropped`` lists segment IDs whose link is unsafe."""

    def __init__(self, sections, *, final_text: str):
        super().__init__()
        self.sections = sections
        self.final_text = final_text

    async def handle_stream(self, envelope):
        self.envelopes.append(envelope)
        all_citations: list[Citation] = []
        for index, citations in enumerate(self.sections, start=1):
            section_id = f"section-{index}"
            all_citations.extend(citations)
            yield AgentStreamEvent(
                "section_started", envelope.request_id, envelope.message_id,
                section_id=section_id, status="grounded",
                citation_ids=tuple(value.segment_id for value in citations),
                citations=tuple(citations),
            )
            yield AgentStreamEvent(
                "text_delta", envelope.request_id, envelope.message_id,
                section_id=section_id, text=f"第{index}段",
            )
            yield AgentStreamEvent(
                "section_completed", envelope.request_id, envelope.message_id,
                section_id=section_id, status="grounded",
            )
        yield AgentStreamEvent(
            "completed", envelope.request_id, envelope.message_id,
            answer=AgentAnswer(
                status="ok",
                text=self.final_text + _sources_block(all_citations),
                citations=all_citations,
                thread_id="thread-1",
            ),
            new_messages=(),
        )


async def _stream_events(channel: _ChannelService, message_id: str) -> list[dict]:
    client, origin, csrf, _ = await _authenticated_client(_settings(), channel)
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages/stream",
            json={"message_id": message_id, "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        return [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_numbers_markers_by_public_citation_position_across_sections():
    sections = [
        [_numbered_citation(101), _numbered_citation(102)],
        [_numbered_citation(203), _numbered_citation(204)],
    ]
    channel = _MultiSectionChannel(
        sections, final_text="第1段 [S101] [S102]\n\n第2段 [S203] [S204] [S101]"
    )

    events = await _stream_events(channel, "message-numbered")

    started = [event for event in events if event["type"] == "section_started"]
    assert [[value["title"] for value in event["citations"]] for event in started] == [
        ["来源101", "来源102"],
        ["来源203", "来源204"],
    ]
    final = events[-1]["response"]
    assert events[-1]["type"] == "completed"
    assert [value["title"] for value in final["citations"]] == [
        "来源101", "来源102", "来源203", "来源204",
    ]
    assert final["text"].startswith("第1段 [^1] [^2]\n\n第2段 [^3] [^4] [^1]")
    assert "[S" not in final["text"]
    # Source block markers are rewritten with the same ordinals.
    assert "  - [^3] https://example.test/video?t=203" in final["text"]
    # No internal segment identifier reaches the browser in the text.
    assert "101" not in final["text"].split("来源：")[0]


@pytest.mark.asyncio
async def test_stream_text_delta_markers_use_the_same_ordinals():
    class _MarkerDeltaChannel(_ChannelService):
        async def handle_stream(self, envelope):
            self.envelopes.append(envelope)
            first = [_numbered_citation(31), _numbered_citation(32)]
            second = [_numbered_citation(33), _numbered_citation(34)]
            for index, citations in enumerate((first, second), start=1):
                yield AgentStreamEvent(
                    "section_started", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", status="grounded",
                    citation_ids=tuple(value.segment_id for value in citations),
                    citations=tuple(citations),
                )
                yield AgentStreamEvent(
                    "text_delta", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}",
                    text=f"文字 [S{citations[1].segment_id}] [S{citations[0].segment_id}]",
                )
                yield AgentStreamEvent(
                    "section_completed", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", status="grounded",
                )
            yield AgentStreamEvent(
                "completed", envelope.request_id, envelope.message_id,
                answer=AgentAnswer(
                    status="ok", text="文字 [S32] [S31] 文字 [S34] [S33]",
                    citations=first + second, thread_id="thread-1",
                ),
                new_messages=(),
            )

    events = await _stream_events(_MarkerDeltaChannel(), "message-delta-markers")

    deltas = [event["text"] for event in events if event["type"] == "text_delta"]
    assert deltas == ["文字 [^2] [^1]", "文字 [^4] [^3]"]
    assert all("[S" not in value for value in deltas)
    assert events[-1]["response"]["text"] == "文字 [^2] [^1] 文字 [^4] [^3]"


@pytest.mark.asyncio
async def test_stream_final_numbering_stays_contiguous_when_a_citation_is_filtered():
    unsafe = _numbered_citation(302, url="javascript:alert(1)")

    class _FilteredFinalChannel(_ChannelService):
        async def handle_stream(self, envelope):
            self.envelopes.append(envelope)
            for index, citation in enumerate(
                (_numbered_citation(301), _numbered_citation(303)), start=1
            ):
                yield AgentStreamEvent(
                    "section_started", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", status="grounded",
                    citation_ids=(citation.segment_id,), citations=(citation,),
                )
                yield AgentStreamEvent(
                    "text_delta", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", text="文字",
                )
                yield AgentStreamEvent(
                    "section_completed", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", status="grounded",
                )
            final = [_numbered_citation(301), unsafe, _numbered_citation(303)]
            yield AgentStreamEvent(
                "completed", envelope.request_id, envelope.message_id,
                answer=AgentAnswer(
                    status="ok",
                    text="甲 [S301] [S302]\n\n乙 [S303]" + _sources_block(final),
                    citations=final, thread_id="thread-1",
                ),
                new_messages=(),
            )

    events = await _stream_events(_FilteredFinalChannel(), "message-filtered")

    final = events[-1]["response"]
    assert events[-1]["type"] == "completed"
    assert [value["title"] for value in final["citations"]] == ["来源301", "来源303"]
    # The dropped citation's marker disappears; the rest are consecutive and
    # each ordinal points at the right public citation.
    assert final["text"].startswith("甲 [^1]\n\n乙 [^2]")
    assert "[^3]" not in final["text"]
    assert "[S" not in final["text"]


@pytest.mark.asyncio
async def test_stream_final_list_follows_stream_ordinals_when_final_order_differs():
    first = [_numbered_citation(401), _numbered_citation(402)]
    second = [_numbered_citation(403)]

    class _ReorderedFinalChannel(_ChannelService):
        async def handle_stream(self, envelope):
            self.envelopes.append(envelope)
            for index, citations in enumerate((first, second), start=1):
                yield AgentStreamEvent(
                    "section_started", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", status="grounded",
                    citation_ids=tuple(value.segment_id for value in citations),
                    citations=tuple(citations),
                )
                yield AgentStreamEvent(
                    "text_delta", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", text="文字",
                )
                yield AgentStreamEvent(
                    "section_completed", envelope.request_id, envelope.message_id,
                    section_id=f"section-{index}", status="grounded",
                )
            yield AgentStreamEvent(
                "completed", envelope.request_id, envelope.message_id,
                answer=AgentAnswer(
                    status="ok", text="甲 [S401] [S402]\n\n乙 [S403]",
                    # Same citations, different order, plus one never streamed.
                    citations=[
                        _numbered_citation(403), _numbered_citation(405),
                        _numbered_citation(402), _numbered_citation(401),
                    ],
                    thread_id="thread-1",
                ),
                new_messages=(),
            )

    events = await _stream_events(_ReorderedFinalChannel(), "message-reordered")

    final = events[-1]["response"]
    assert [value["title"] for value in final["citations"]] == [
        "来源401", "来源402", "来源403", "来源405",
    ]
    assert final["text"] == "甲 [^1] [^2]\n\n乙 [^3]"


@pytest.mark.asyncio
async def test_compatibility_paths_renumber_the_whole_answer_delta_and_response():
    citations = [_numbered_citation(501), _numbered_citation(502)]
    answer = AgentAnswer(
        status="ok",
        text="答案 [S502] [S501]" + _sources_block(citations),
        citations=citations,
        thread_id="thread-1",
    )
    for channel in (_ChannelService(answer), _CompletedOnlyChannel(answer)):
        events = await _stream_events(channel, "message-compat")
        deltas = [event for event in events if event["type"] == "text_delta"]
        assert len(deltas) == 1
        assert deltas[0]["text"].startswith("答案 [^2] [^1]")
        assert "[S" not in deltas[0]["text"]
        final = events[-1]["response"]
        assert final["text"] == deltas[0]["text"]
        assert [value["title"] for value in final["citations"]] == ["来源501", "来源502"]


@pytest.mark.asyncio
async def test_json_route_renumbers_markers_and_drops_unaddressable_ones():
    citations = [_numbered_citation(601), _numbered_citation(602, url="ftp://bad.example/x")]
    answer = AgentAnswer(
        status="ok", text="甲 [S601] [S602]", citations=citations, thread_id="thread-1"
    )
    client, origin, csrf, _ = await _authenticated_client(_settings(), _ChannelService(answer))
    try:
        response = await client.post(
            "/api/v1/conversations/browser-thread/messages",
            json={"message_id": "message-json", "text": "hello"},
            headers={**origin, "X-CSRF-Token": csrf},
        )
    finally:
        await client.aclose()
    body = response.json()
    assert body["text"] == "甲 [^1]"
    assert [value["title"] for value in body["citations"]] == ["来源601"]
