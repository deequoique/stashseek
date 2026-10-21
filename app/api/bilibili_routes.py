"""Canonical browser adapters for Bilibili part preview, import and source."""

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.responses import RedirectResponse

from app.api.bilibili_schemas import BilibiliFavoritesPreviewRequest, BilibiliFavoritesPreviewResponse, BilibiliPartsBatchRequest, BilibiliPartsBatchResponse, BilibiliPartSaveResult, BilibiliPartsPreviewRequest, BilibiliPartsPreviewResponse
from app.api.library_routes import CsrfHeader, IdempotencyKey, PublicId, SafeValidationRoute, _batch_item, _request_key, _safe_service_errors, _session_cookie_schema
from app.api.library_schemas import ErrorResponse
from app.bilibili.public import BilibiliPublicError


@contextmanager
def safe_bilibili_errors():
    try:
        yield
    except BilibiliPublicError as exc:
        status = {"not_found": 404, "bilibili_invalid_url": 422, "bilibili_invalid_favorites_url": 422, "bilibili_favorites_private": 409, "bilibili_limit_exceeded": 422, "bilibili_rate_limited": 429, "bilibili_busy": 503, "source_unavailable": 409}.get(exc.error_code, 503)
        raise HTTPException(status_code=status, detail=exc.error_code) from None


def build_bilibili_router(service, *, scope_dependency, publish_budget_seconds: float, save_enabled: bool, enabled: bool = True):
    router = APIRouter(prefix="/api/v1", route_class=SafeValidationRoute, dependencies=[Security(_session_cookie_schema)], responses={422: {"model": ErrorResponse}})

    def require_enabled() -> None:
        """Kill switch: no Bilibili egress surface exists while this is off."""

        if not enabled:
            raise HTTPException(status_code=503, detail="bilibili_disabled")

    @router.post("/bilibili/favorites:preview", response_model=BilibiliFavoritesPreviewResponse)
    def favorites(body: BilibiliFavoritesPreviewRequest, _csrf_token: CsrfHeader, scope=Depends(scope_dependency)):
        require_enabled()
        with safe_bilibili_errors():
            return BilibiliFavoritesPreviewResponse.model_validate(service.preview_favorites(scope, body.url, page=body.page))

    @router.post("/bilibili/parts:preview", response_model=BilibiliPartsPreviewResponse)
    def preview(body: BilibiliPartsPreviewRequest, _csrf_token: CsrfHeader, scope=Depends(scope_dependency)):
        require_enabled()
        with safe_bilibili_errors():
            return BilibiliPartsPreviewResponse.model_validate(service.preview_parts(scope, body.url, page=body.page))

    @router.post("/bilibili/parts:batch", response_model=BilibiliPartsBatchResponse)
    def save(body: BilibiliPartsBatchRequest, idempotency_key: IdempotencyKey, _csrf_token: CsrfHeader, scope=Depends(scope_dependency)):
        require_enabled()
        if not save_enabled:
            raise HTTPException(status_code=503, detail="save_disabled")
        with safe_bilibili_errors(), _safe_service_errors():
            result = service.submit_parts(scope, body.selections, why_saved=body.why_saved, request_key=_request_key(scope, idempotency_key), publish_budget_seconds=publish_budget_seconds)
            rows = []
            for item in result.results:
                if item.status in {"part_ref_invalid", "part_ref_expired", "legacy_identity_unknown"}:
                    rows.append(BilibiliPartSaveResult(result_id=item.result_id, input_index=item.input_index, status=item.status, safe_error_code=item.safe_error_code))
                else:
                    rows.append(BilibiliPartSaveResult.model_validate(_batch_item(item).model_dump()))
            return BilibiliPartsBatchResponse(results=tuple(rows))

    @router.get("/library/items/{item_public_id}/source", response_class=RedirectResponse, status_code=303)
    def source(item_public_id: PublicId, t: int = Query(default=0, ge=0, le=2147483647), scope=Depends(scope_dependency)):
        require_enabled()
        with safe_bilibili_errors():
            return RedirectResponse(service.open_source(scope, item_public_id, timestamp=t), status_code=303, headers={"Cache-Control": "no-store"})

    return router
