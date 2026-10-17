"""HTTP surface for instant page Q&A (extension side panel).

``POST /api/v1/page-qa/conversations/{conversation_id}/messages/stream`` streams
the answer over the existing public conversation event contract, restricted to
``started``, ``text_delta``, ``completed`` and ``error``.  Authentication is the
canonical app's: an extension-session Bearer, or the Web cookie (whose CSRF
check lives in the app middleware).  The ``capture:write`` Bearer never
authenticates here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Security
from fastapi.responses import StreamingResponse

from app.api.conversation_routes import (
    SESSION_COOKIE_SCHEMA,
    STREAM_ACTIVITY_LABELS,
    STREAM_MEDIA_TYPE,
    BrowserSessionIdentityResolver,
    ConversationCitationResponse,
    ConversationResponse,
    ConversationStreamEvent,
    CsrfHeader,
    encode_stream_event,
    resolve_session_identity,
    safe_stream_error_code,
)
from app.api.library_schemas import ErrorResponse
from app.page_qa import PAGE_QA_CONVERSATION_ID_PATTERN, PAGE_QA_STATUS_UNAVAILABLE
from app.page_qa.rate_limit import SlidingWindowRateLimiter
from app.page_qa.service import (
    PageQaCompleted,
    PageQaFailed,
    PageQaMessageInput,
    PageQaService,
    PageQaTextDelta,
)

logger = logging.getLogger(__name__)

_FAILURE_COPY = {
    "answer_unavailable": "暂时无法生成可靠回答，请稍后重试。",
    "timeout": "请求超时，请稍后重试。",
    "request_failed": "请求无法完成，请稍后重试。",
}


def build_page_qa_router(
    *,
    session_dependency: Callable,
    session_factory,
    settings,
    session_identity_resolver: BrowserSessionIdentityResolver | None = None,
    service: Any | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> APIRouter:
    """Build the page Q&A router behind the canonical app's authentication.

    ``service`` is injectable for tests; otherwise a ``PageQaService`` is built
    on first use so provider construction never happens at import or OpenAPI
    export time.  Without ``settings``/``session_factory`` (API-only
    compositions such as the exporter) the route is documented but fails
    closed with ``request_failed``.
    """

    router = APIRouter(prefix="/api/v1/page-qa", tags=["page-qa"])
    limiter = rate_limiter or SlidingWindowRateLimiter(
        int(getattr(settings, "page_qa_rate_limit", 30))
    )
    holder: dict[str, Any] = {"service": service}

    def authenticated_session(
        request: Request,
        _session_cookie: str | None = Security(SESSION_COOKIE_SCHEMA),
    ):
        return session_dependency(request)

    def service_or_unavailable():
        current = holder["service"]
        if current is None:
            if settings is None or session_factory is None:
                raise HTTPException(status_code=503, detail="request_failed")
            current = holder["service"] = PageQaService(settings, session_factory)
        return current

    @router.post(
        "/conversations/{conversation_id}/messages/stream",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "Server-sent public conversation events",
                "content": {"text/event-stream": {}},
            },
            401: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def stream_page_question(
        conversation_id: Annotated[
            str, Path(pattern=PAGE_QA_CONVERSATION_ID_PATTERN)
        ],
        payload: PageQaMessageInput,
        _csrf_token: CsrfHeader = None,
        session: object = Depends(authenticated_session),
    ) -> StreamingResponse:
        """Answer one question about the page from the supplied transcript.

        Nothing is ingested: the cues live only in this request and the model
        context.  The turn (question, answer, <=180-character cited excerpts)
        is saved to the user's conversation history after a validated answer.
        """

        try:
            identity = resolve_session_identity(
                session,
                session_factory=session_factory,
                session_identity_resolver=session_identity_resolver,
            )
        except ValueError:
            raise HTTPException(status_code=401, detail="session_invalid") from None
        page_service = service_or_unavailable()
        retry_after = limiter.acquire(identity.app_user_id)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="rate_limited",
                headers={"Retry-After": str(retry_after)},
            )

        request_id = uuid4().hex

        async def events() -> AsyncIterator[str]:
            sequence = 0

            def make(event_type: str, **fields: Any) -> str:
                nonlocal sequence
                sequence += 1
                return encode_stream_event(
                    ConversationStreamEvent(
                        type=event_type,  # type: ignore[arg-type]
                        request_id=request_id,
                        message_id=payload.message_id,
                        sequence=sequence,
                        **fields,
                    )
                )

            def failure(code: str) -> str:
                safe_code = safe_stream_error_code(code)
                copy = _FAILURE_COPY.get(safe_code, _FAILURE_COPY["request_failed"])
                return make(
                    "error",
                    activity="failed",
                    response=ConversationResponse(
                        status="failed", text=copy, error_code=safe_code
                    ),
                    error_code=safe_code,
                    message=copy,
                )

            yield make(
                "started",
                activity="preparing",
                message=STREAM_ACTIVITY_LABELS["preparing"],
            )
            try:
                async with aclosing(
                    page_service.stream(
                        tenant=identity.tenant,
                        conversation_id=conversation_id,
                        payload=payload,
                        request_id=request_id,
                    )
                ) as answer_events:
                    async for event in answer_events:
                        if isinstance(event, PageQaTextDelta):
                            if event.text:
                                yield make("text_delta", text=event.text)
                        elif isinstance(event, PageQaCompleted):
                            yield make(
                                "completed",
                                activity="completed",
                                response=ConversationResponse(
                                    status=(
                                        "not_found"
                                        if event.status == PAGE_QA_STATUS_UNAVAILABLE
                                        else "ok"
                                    ),
                                    text=event.text,
                                    citations=[
                                        ConversationCitationResponse(
                                            title=citation.title,
                                            excerpt=citation.excerpt,
                                            url=citation.url,
                                            start_sec=citation.start_sec,
                                        )
                                        for citation in event.citations
                                    ],
                                    thread_id=event.thread_id,
                                ),
                                message=STREAM_ACTIVITY_LABELS["completed"],
                            )
                            return
                        elif isinstance(event, PageQaFailed):
                            yield failure(event.error_code)
                            return
                # The service always ends with a terminal event; anything else
                # is a protocol bug and must not leave the client waiting.
                yield failure("request_failed")
            except Exception as exc:  # noqa: BLE001 - never echo details
                logger.warning(
                    "page_qa_stream_failed request_id=%s exception_type=%s",
                    request_id,
                    type(exc).__name__,
                )
                yield failure("request_failed")

        return StreamingResponse(
            events(),
            media_type=STREAM_MEDIA_TYPE,
            headers={
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Agent-Streaming": "enabled",
            },
        )

    return router
