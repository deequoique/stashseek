"""FastAPI adapter for framework-neutral library services."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import APIKeyCookie

from app.api.library_schemas import (
    BatchItemResponse,
    BatchSaveRequest,
    BatchSaveResponse,
    DispatchResponse,
    ErrorResponse,
    LibraryItemResponse,
    LibraryPageResponse,
    TranscriptPageResponse,
    WhySavedRequest,
)
from app.ingest.submission import BatchValidationError
from app.web.auth import SESSION_COOKIE_NAME
from app.web.library import LibraryConflict, LibraryNotFound
from app.web.transcript import TranscriptError


IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=200),
]
# Documented but optional at the route signature: CSRF enforcement lives in the
# app-level boundary (``app.api.app``) for cookie sessions, while an
# extension-session Bearer (no readable CSRF cookie) is a separate transport.
CsrfHeader = Annotated[
    str | None,
    Header(alias="X-CSRF-Token", max_length=200),
]
PublicId = Annotated[str, Path(min_length=1, max_length=200)]
_session_cookie_schema = APIKeyCookie(
    name=SESSION_COOKIE_NAME,
    scheme_name="SessionCookie",
    auto_error=False,
)


class SafeValidationRoute(APIRoute):
    """Remove rejected body/header values from validation responses."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "code": "validation_error",
                        "message": "请求参数无效",
                    },
                )

        return safe_handler


def _request_key(scope, raw_key: str) -> str:
    value = raw_key.strip()
    if not value:
        raise HTTPException(status_code=422, detail="invalid_idempotency_key")
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"web:{scope.app_user_id}:{digest}"


@contextmanager
def _safe_service_errors():
    try:
        yield
    except LibraryNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.error_code) from None
    except LibraryConflict as exc:
        if exc.error_code == "save_disabled":
            raise HTTPException(status_code=503, detail=exc.error_code) from None
        if exc.error_code == "quota_exceeded":
            raise HTTPException(
                status_code=429,
                detail=exc.error_code,
                headers={"Retry-After": "60"},
            ) from None
        raise HTTPException(status_code=409, detail=exc.error_code) from None
    except BatchValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.error_code) from None
    except TranscriptError as exc:
        if exc.error_code == "not_found":
            status = 404
        elif exc.error_code == "transcript_invalid":
            status = 422
        else:
            status = 409
        raise HTTPException(status_code=status, detail=exc.error_code) from None


def _item(value) -> LibraryItemResponse:
    return LibraryItemResponse.model_validate(value)


def _batch_item(value) -> BatchItemResponse:
    # Internal recycle-bin outcomes stay behind the stable browser contract.
    # A restored item is already present again; an item whose purge has begun
    # is a retryable add failure and must not leak an unsupported enum to the
    # generated frontend schema.
    if value.status == "restored":
        status = "already_exists"
    elif value.status == "purge_in_progress":
        status = "create_failed"
    else:
        status = value.status
    if status == "queued":
        lifecycle = "queued"
    elif status in {"queue_unavailable", "create_failed"}:
        lifecycle = "failed"
    elif status == "already_exists":
        lifecycle = "archived" if value.archived else {
                "ready": "ready",
                "needs_extension": "needs_action",
                "needs_asr": "needs_action",
                "no_text": "needs_action",
                "fetching": "processing",
                "chunking": "processing",
                "embedding": "processing",
                "pending": "queued",
                "failed": "failed",
            }.get(value.state)
    else:
        lifecycle = None
    return BatchItemResponse(
        result_id=value.result_id,
        input_index=value.input_index,
        status=status,
        item_public_id=value.item_public_id,
        lifecycle=lifecycle,
        safe_error_code=(
            "create_failed"
            if value.status == "purge_in_progress"
            else value.safe_error_code
        ),
    )


def build_library_router(
    library,
    submission,
    transcript,
    *,
    publish_budget_seconds: float,
    save_enabled: bool = True,
    scope_dependency: Callable,
) -> APIRouter:
    """Build routes with an explicit authenticated-scope injection point."""

    if publish_budget_seconds <= 0:
        raise ValueError("publish budget must be positive")
    router = APIRouter(
        prefix="/api/v1",
        route_class=SafeValidationRoute,
        dependencies=[Security(_session_cookie_schema)],
        responses={422: {"model": ErrorResponse}},
    )

    def require_save_enabled() -> None:
        if not save_enabled:
            raise HTTPException(status_code=503, detail="save_disabled")

    @router.get("/library/items", response_model=LibraryPageResponse)
    def list_items(
        search: str | None = Query(default=None, max_length=200),
        collection: str | None = Query(default=None, min_length=1, max_length=20),
        lifecycle: Literal[
            "archived", "ready", "needs_action", "failed", "processing", "queued"
        ]
        | None = None,
        include_archived: bool = False,
        sort: Literal["saved_desc", "saved_asc", "title_asc"] = "saved_desc",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        scope=Depends(scope_dependency),
    ) -> LibraryPageResponse:
        with _safe_service_errors():
            result = library.list_items(
                scope,
                search=search,
                collection=collection,
                lifecycle=lifecycle,
                include_archived=include_archived,
                sort=sort,
                page=page,
                page_size=page_size,
            )
            return LibraryPageResponse(
                items=tuple(_item(value) for value in result.items),
                total=result.total,
                page=result.page,
                page_size=result.page_size,
                is_true_first_empty=result.is_true_first_empty,
            )

    @router.post(
        "/library/items:batch",
        response_model=BatchSaveResponse,
        responses={503: {"model": ErrorResponse}},
    )
    def save_batch(
        body: BatchSaveRequest,
        idempotency_key: IdempotencyKey,
        _csrf_token: CsrfHeader = None,
        scope=Depends(scope_dependency),
    ) -> BatchSaveResponse:
        require_save_enabled()
        with _safe_service_errors():
            result = submission.submit_urls(
                scope,
                body.urls,
                why_saved=body.why_saved,
                request_key=_request_key(scope, idempotency_key),
                publish_budget_seconds=publish_budget_seconds,
            )
            return BatchSaveResponse(
                results=tuple(_batch_item(value) for value in result.results)
            )

    @router.get("/library/items/{item_public_id}", response_model=LibraryItemResponse)
    def detail(
        item_public_id: PublicId,
        scope=Depends(scope_dependency),
    ) -> LibraryItemResponse:
        with _safe_service_errors():
            return _item(library.get_item(scope, item_public_id))

    @router.patch("/library/items/{item_public_id}", response_model=LibraryItemResponse)
    def update(
        item_public_id: PublicId,
        body: WhySavedRequest,
        _csrf_token: CsrfHeader = None,
        scope=Depends(scope_dependency),
    ) -> LibraryItemResponse:
        with _safe_service_errors():
            return _item(
                library.update_why_saved(scope, item_public_id, body.why_saved)
            )

    @router.post(
        "/library/items/{item_public_id}:archive",
        response_model=LibraryItemResponse,
    )
    def archive(
        item_public_id: PublicId,
        _csrf_token: CsrfHeader = None,
        scope=Depends(scope_dependency),
    ) -> LibraryItemResponse:
        with _safe_service_errors():
            return _item(library.archive(scope, item_public_id))

    @router.post(
        "/library/items/{item_public_id}:restore",
        response_model=LibraryItemResponse,
    )
    def restore(
        item_public_id: PublicId,
        _csrf_token: CsrfHeader = None,
        scope=Depends(scope_dependency),
    ) -> LibraryItemResponse:
        with _safe_service_errors():
            return _item(library.restore(scope, item_public_id))

    @router.post(
        "/library/items/{item_public_id}:retry",
        response_model=LibraryItemResponse,
        responses={
            429: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def retry(
        item_public_id: PublicId,
        idempotency_key: IdempotencyKey,
        _csrf_token: CsrfHeader = None,
        scope=Depends(scope_dependency),
    ) -> LibraryItemResponse:
        require_save_enabled()
        with _safe_service_errors():
            return _item(
                library.retry(
                    scope,
                    item_public_id,
                    request_key=_request_key(scope, idempotency_key),
                    publish_budget_seconds=publish_budget_seconds,
                )
            )

    @router.get(
        "/ingest-dispatches/{dispatch_public_id}",
        response_model=DispatchResponse,
    )
    def dispatch(
        dispatch_public_id: PublicId,
        scope=Depends(scope_dependency),
    ) -> DispatchResponse:
        with _safe_service_errors():
            return DispatchResponse.model_validate(
                library.get_dispatch(scope, dispatch_public_id)
            )

    @router.get(
        "/library/items/{item_public_id}/transcript",
        response_model=TranscriptPageResponse,
    )
    def transcript_page(
        item_public_id: PublicId,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=2000),
        scope=Depends(scope_dependency),
    ) -> TranscriptPageResponse:
        with _safe_service_errors():
            return TranscriptPageResponse.model_validate(
                transcript.get(
                    scope,
                    item_public_id,
                    limit=limit,
                    cursor=cursor,
                )
            )

    return router
