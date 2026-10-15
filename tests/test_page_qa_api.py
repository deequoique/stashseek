"""Contract tests for ``POST /api/v1/page-qa/conversations/{id}/messages/stream``."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.api.conversation_routes import ConversationStreamEvent
from app.page_qa import PAGE_QA_STATUS_GROUNDED
from app.page_qa.service import (
    PageQaCompleted,
    PageQaFailed,
    PageQaService,
    PageQaTextDelta,
)
from app.agent.types import Citation
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, WebAuthError
from tests.page_qa_support import (
    CONVERSATION_ID,
    TENANT,
    ScriptedModel,
    make_settings,
    message,
    sqlite_factory,
    youtube_page,
)

ORIGIN = "https://kb.example.test"
EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32
OTHER_EXTENSION_ORIGIN = "chrome-extension://" + "b" * 32
STREAM_PATH = f"/api/v1/page-qa/conversations/{CONVERSATION_ID}/messages/stream"
PARAGRAPHS = [
    "Welcome to the course on gradient descent.",
    "Today we cover learning rates and why they matter.",
    "A learning rate that is too large makes training diverge.",
]
BEARER = {"Authorization": "Bearer extension-session", "Origin": EXTENSION_ORIGIN}


def _session():
    return SimpleNamespace(
        tenant=TENANT, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )


class CookieAuth:
    def resolve_session(self, token):
        if token != "session-secret":
            raise WebAuthError("session_invalid")
        return _session()

    def validate_csrf(self, _session_token, csrf_token):
        if csrf_token != "csrf-secret":
            raise WebAuthError("csrf_invalid")


class ExtensionSessions:
    """Only the extension session token resolves; a capture token does not."""

    def resolve(self, token):
        if token != "extension-session":
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        return _session(), TENANT.app_user_id, "user@example.test"


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"page Q&A must not touch ingestion/library: {name}")


class StubService:
    def __init__(self, events=None):
        self.events = events if events is not None else [
            PageQaTextDelta("Hello "),
            PageQaTextDelta("world [S1]."),
            PageQaCompleted(
                "Hello world [S1].\n\n来源：\n- Test Video\n  - [S1] https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0 — Welcome",
                (
                    Citation(
                        item_id=0,
                        segment_id=1,
                        title="Test Video",
                        excerpt="Welcome",
                        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0",
                        start_sec=0,
                    ),
                ),
                PAGE_QA_STATUS_GROUNDED,
                "thread-public-id",
            ),
        ]
        self.calls = []

    async def stream(self, *, tenant, conversation_id, payload, request_id):
        self.calls.append((tenant, conversation_id, payload, request_id))
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


def build_client(
    *,
    service=None,
    settings=None,
    session_factory=None,
    enabled: bool = True,
):
    settings = settings or make_settings(
        browser_companion_allowed_origins=(EXTENSION_ORIGIN,),
        page_qa_max_request_bytes=300_000,
        page_qa_rate_limit=100,
    )
    app = create_app(
        services=WebApiServices(
            web_auth=CookieAuth(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            browser_capture_submission=None,
            channel_service=Unused(),
            session_resolver=CookieAuth().resolve_session,
            session_identity_resolver=lambda _session: TENANT,
            session_factory=session_factory,
            settings=settings,
            extension_sessions=ExtensionSessions(),
            include_page_qa_routes=enabled,
            page_qa_service=service,
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
    )
    return TestClient(app, base_url=ORIGIN)


def sse_events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def valid_body(**overrides):
    return message(youtube_page(PARAGRAPHS), **overrides)


# --- success paths -----------------------------------------------------------------------


def test_extension_bearer_streams_a_valid_public_event_sequence():
    service = StubService()
    client = build_client(service=service)

    response = client.post(STREAM_PATH, json=valid_body(), headers=BEARER)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response)
    assert [event["type"] for event in events] == [
        "started",
        "text_delta",
        "text_delta",
        "completed",
    ]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert len({event["request_id"] for event in events}) == 1
    assert {event["message_id"] for event in events} == {"message-1"}
    for event in events:  # every record satisfies the shared event validators
        ConversationStreamEvent.model_validate(event)
    final = events[-1]["response"]
    assert final["status"] == "ok" and final["thread_id"] == "thread-public-id"
    assert final["citations"] == [
        {
            "title": "Test Video",
            "excerpt": "Welcome",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0",
            "start_sec": 0.0,
        }
    ]
    assert final["text"].startswith("Hello world [S1].\n\n来源：\n")
    tenant, conversation_id, payload, _ = service.calls[0]
    assert tenant == TENANT and conversation_id == CONVERSATION_ID
    assert payload.question == "What does the speaker say?"


def test_cookie_session_with_csrf_succeeds_and_without_it_is_rejected():
    client = build_client(service=StubService())
    client.cookies.set(SESSION_COOKIE_NAME, "session-secret", domain="kb.example.test", path="/")
    client.cookies.set(CSRF_COOKIE_NAME, "csrf-secret", domain="kb.example.test", path="/")

    missing = client.post(STREAM_PATH, json=valid_body(), headers={"Origin": ORIGIN})
    wrong = client.post(
        STREAM_PATH, json=valid_body(), headers={"Origin": ORIGIN, "X-CSRF-Token": "nope"}
    )
    ok = client.post(
        STREAM_PATH,
        json=valid_body(),
        headers={"Origin": ORIGIN, "X-CSRF-Token": "csrf-secret"},
    )

    assert missing.status_code == 403 and missing.json()["code"] == "csrf_invalid"
    assert wrong.status_code == 403
    assert ok.status_code == 200
    assert sse_events(ok)[-1]["type"] == "completed"


def test_allowed_extension_origin_gets_cors_headers():
    client = build_client(service=StubService())

    response = client.post(STREAM_PATH, json=valid_body(), headers=BEARER)

    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    preflight = client.options(
        STREAM_PATH,
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Authorization": "Bearer extension-session",
        },
    )
    assert preflight.status_code == 204


# --- authentication --------------------------------------------------------------------


def test_capture_bearer_is_rejected_and_never_reaches_the_service():
    service = StubService()
    client = build_client(service=service)

    response = client.post(
        STREAM_PATH,
        json=valid_body(),
        headers={"Authorization": "Bearer capture-token", "Origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "extension_session_invalid"
    assert service.calls == []


def test_missing_credentials_are_unauthorized():
    service = StubService()
    client = build_client(service=service)

    response = client.post(STREAM_PATH, json=valid_body(), headers={"Origin": ORIGIN})

    assert response.status_code in (401, 403)
    assert service.calls == []


def test_disallowed_extension_origin_is_forbidden_even_with_a_valid_session():
    service = StubService()
    client = build_client(service=service)

    response = client.post(
        STREAM_PATH,
        json=valid_body(),
        headers={"Authorization": "Bearer extension-session", "Origin": OTHER_EXTENSION_ORIGIN},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "extension_origin_invalid"
    assert "access-control-allow-origin" not in response.headers
    assert service.calls == []


# --- request validation -----------------------------------------------------------------


def test_oversized_body_is_rejected_with_the_page_qa_ceiling_not_the_default():
    service = StubService()
    client = build_client(service=service)  # ceiling 300 KB, default 64 KiB

    # ~200 KB of captions: far above the 64 KiB default, below the page ceiling.
    large = message(youtube_page([f"paragraph {n} " + "word " * 120 for n in range(100)]))
    assert 150_000 < len(json.dumps(large)) < 300_000
    allowed = client.post(STREAM_PATH, json=large, headers=BEARER)
    oversized = client.post(
        STREAM_PATH,
        content=json.dumps(valid_body()) + " " * 400_000,
        headers={**BEARER, "Content-Type": "application/json"},
    )

    assert allowed.status_code == 200
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "request_too_large"
    assert len(service.calls) == 1


def test_chunked_oversized_body_is_rejected_before_parsing():
    service = StubService()
    client = build_client(service=service)

    response = client.post(
        STREAM_PATH,
        content=iter((b" " * 200_000, b" " * 200_000)),
        headers={**BEARER, "Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert service.calls == []


def test_other_paths_keep_the_small_default_ceiling():
    client = build_client(service=StubService())
    body = json.dumps(valid_body()) + " " * 100_000

    invalid_id = client.post(
        "/api/v1/page-qa/conversations/not-a-page-id/messages/stream",
        content=body,
        headers={**BEARER, "Content-Type": "application/json"},
    )

    assert invalid_id.status_code == 413


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(extra_field=1),
        lambda body: body["page"].update(content_hash="0" * 64),
        lambda body: body["page"].update(platform="bilibili"),
        lambda body: body["page"].update(platform="vimeo"),
        lambda body: body["page"].update(platform_id="nope"),
        lambda body: body.update(question=" "),
        lambda body: body.update(playback_sec=-3),
        lambda body: body["page"].update(caption={"status": "unavailable"}, platform="ntu_kaltura",
                                         platform_id="1_abc123",
                                         canonical_url="https://ntulearnvideo.ntu.edu.sg/media/t/1_abc123"),
        lambda body: body["page"]["caption"]["cues"].reverse(),
    ],
)
def test_invalid_bodies_are_rejected_with_the_safe_validation_error(mutate):
    service = StubService()
    client = build_client(service=service)
    body = valid_body()
    mutate(body)

    response = client.post(STREAM_PATH, json=body, headers=BEARER)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert service.calls == []


@pytest.mark.parametrize(
    "conversation_id",
    ["page-short", "thread-" + "a" * 32, "page-" + "A" * 32, "page-" + "g" * 32],
)
def test_conversation_id_must_be_a_page_thread_id(conversation_id):
    client = build_client(service=StubService())

    response = client.post(
        f"/api/v1/page-qa/conversations/{conversation_id}/messages/stream",
        json=valid_body(),
        headers=BEARER,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- rate limit and feature flag ----------------------------------------------------------


def test_per_user_rate_limit_returns_429_before_any_work():
    service = StubService()
    settings = make_settings(
        browser_companion_allowed_origins=(EXTENSION_ORIGIN,), page_qa_rate_limit=2
    )
    client = build_client(service=service, settings=settings)

    statuses = [
        client.post(STREAM_PATH, json=valid_body(message_id=f"m-{n}"), headers=BEARER)
        for n in range(3)
    ]

    assert [response.status_code for response in statuses] == [200, 200, 429]
    limited = statuses[2]
    assert limited.json() == {"code": "rate_limited", "message": "提问过于频繁，请稍后重试"}
    assert int(limited.headers["retry-after"]) >= 1
    assert len(service.calls) == 2


def test_validation_failures_do_not_consume_the_rate_limit():
    settings = make_settings(
        browser_companion_allowed_origins=(EXTENSION_ORIGIN,), page_qa_rate_limit=1
    )
    client = build_client(service=StubService(), settings=settings)

    for _ in range(3):
        bad = valid_body()
        bad["extra"] = True
        assert client.post(STREAM_PATH, json=bad, headers=BEARER).status_code == 422

    assert client.post(STREAM_PATH, json=valid_body(), headers=BEARER).status_code == 200


def test_disabled_flag_unmounts_the_route_and_hides_the_capability():
    disabled = build_client(service=StubService(), enabled=False)
    enabled = build_client(service=StubService())

    response = disabled.post(STREAM_PATH, json=valid_body(), headers=BEARER)

    assert response.status_code == 404
    assert disabled.get("/api/v1/capabilities").json()["page_qa"] is False
    assert enabled.get("/api/v1/capabilities").json()["page_qa"] is True
    assert STREAM_PATH.replace(CONVERSATION_ID, "{conversation_id}") not in (
        disabled.get("/api/v1/openapi.json").json()["paths"]
    )


def test_unconfigured_service_fails_closed_with_a_safe_error():
    app_client = build_client(service=None, session_factory=None)

    response = app_client.post(STREAM_PATH, json=valid_body(), headers=BEARER)

    assert response.status_code == 503
    assert response.json()["code"] == "request_failed"


# --- failures surface only closed codes -----------------------------------------------------


@pytest.mark.parametrize("code", ["answer_unavailable", "timeout", "request_failed"])
def test_service_failure_becomes_a_safe_terminal_error_event(code):
    client = build_client(
        service=StubService([PageQaTextDelta("partial "), PageQaFailed(code)])
    )

    response = client.post(STREAM_PATH, json=valid_body(), headers=BEARER)

    events = sse_events(response)
    assert [event["type"] for event in events] == ["started", "text_delta", "error"]
    assert events[-1]["error_code"] == code
    assert events[-1]["response"]["status"] == "failed"
    for event in events:
        ConversationStreamEvent.model_validate(event)


def test_unexpected_exception_inside_the_stream_never_leaks_details():
    client = build_client(
        service=StubService(
            [PageQaTextDelta("ok "), RuntimeError("SECRET provider payload sk-123")]
        )
    )

    response = client.post(STREAM_PATH, json=valid_body(), headers=BEARER)

    assert "SECRET" not in response.text and "sk-123" not in response.text
    events = sse_events(response)
    assert events[-1]["type"] == "error" and events[-1]["error_code"] == "request_failed"


# --- OpenAPI -----------------------------------------------------------------------------------


def test_openapi_documents_the_route_with_safe_error_schemas():
    client = build_client(service=StubService())
    document = client.get("/api/v1/openapi.json").json()

    operation = document["paths"][
        "/api/v1/page-qa/conversations/{conversation_id}/messages/stream"
    ]["post"]
    for status in ("401", "413", "422", "429", "503"):
        reference = operation["responses"][status]["content"]["application/json"]["schema"]["$ref"]
        assert reference.endswith("/ErrorResponse")
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["conversation_id"]["schema"]["pattern"] == "^page-[0-9a-f]{32}$"
    assert parameters["X-CSRF-Token"].get("required", False) is False
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    assert "HTTPValidationError" not in document["components"]["schemas"]


# --- end to end: real service, real persistence, privacy ------------------------------------------


SENTINEL = "SENTINEL-API-CUE-4d2b8e"


def test_end_to_end_turn_is_persisted_in_history_and_leaks_no_cue_text(caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    factory = sqlite_factory()
    model = ScriptedModel([["The speaker explains it [P1] and the rate [P2]."]])
    settings = make_settings(
        browser_companion_allowed_origins=(EXTENSION_ORIGIN,), page_qa_rate_limit=100
    )
    service = PageQaService(settings, factory, model=model.build())
    client = build_client(service=service, settings=settings, session_factory=factory)
    page = youtube_page(
        [f"{SENTINEL} opens the lecture on gradient descent.", "Learning rates matter a great deal."]
    )

    response = client.post(STREAM_PATH, json=message(page), headers=BEARER)

    events = sse_events(response)
    assert [event["type"] for event in events][0] == "started"
    assert events[-1]["type"] == "completed"
    for event in events:
        ConversationStreamEvent.model_validate(event)
    streamed = "".join(event["text"] for event in events if event["type"] == "text_delta")
    assert streamed == "The speaker explains it [S1] and the rate [S2]."
    final = events[-1]["response"]
    assert [citation["start_sec"] for citation in final["citations"]] == [0.0, 30.0]
    assert all(len(citation["excerpt"]) <= 180 for citation in final["citations"])
    assert SENTINEL in final["citations"][0]["excerpt"]  # allowed: short cited excerpt

    # The turn shows up in the history APIs for the same (Bearer) identity.
    listing = client.get("/api/v1/conversations", headers=BEARER).json()
    [item] = listing["items"]
    assert item["page_qa"] is True and item["conversation_id"] == CONVERSATION_ID
    assert item["title"] == "What does the speaker say?"
    transcript = client.get(
        f"/api/v1/conversations/{item['thread_id']}/turns", headers=BEARER
    ).json()
    assert transcript["page_qa"] is True
    [turn] = transcript["turns"]
    assert turn["user_text"] == "What does the speaker say?"
    assert turn["status"] == PAGE_QA_STATUS_GROUNDED
    assert turn["assistant_text"] == final["text"]
    assert len(turn["citations"]) == 2

    # Privacy: no log record anywhere carries cue text; the response never
    # echoes it outside the cited excerpt; Unused() guards ingestion/library.
    assert SENTINEL not in caplog.text
    visible, _, _ = final["text"].partition("\n\n来源：\n")
    assert SENTINEL not in visible
    # The sidebar title is the question; its preview is the first 160
    # characters of the saved answer (which may start inside a cited excerpt).
    assert SENTINEL not in item["title"] and len(item["preview"]) <= 160


def test_ordinary_conversations_are_not_flagged_as_page_qa():
    from app.channels.conversations import get_or_create_thread
    from app.channels.types import ChannelEnvelope

    factory = sqlite_factory()
    with factory() as db:
        get_or_create_thread(
            db, TENANT, ChannelEnvelope("web", "web", "user-7", "plain-thread", "m", "hi")
        )
        db.commit()
    client = build_client(service=StubService(), session_factory=factory)

    listing = client.get("/api/v1/conversations", headers=BEARER).json()

    assert [item["page_qa"] for item in listing["items"]] == [False]
