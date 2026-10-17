"""HTTP routes for extension pairing, device control, and caption capture."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.api.library_schemas import LibraryItemResponse, LibraryPageResponse
from app.browser_capture import BrowserCaptureRequest
from app.browser_capture_submission import BrowserCaptureSubmissionError
from app.browser_companion import BrowserCompanionError
from app.channels.types import UserScope


class BrowserCompanionApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairingCreateRequest(BrowserCompanionApiModel):
    challenge: str = Field(min_length=43, max_length=128)
    client_label: str = Field(min_length=1, max_length=200)
    client_version: str = Field(min_length=1, max_length=64)


class PairingCreateResponse(BrowserCompanionApiModel):
    pairing_id: str
    approval_url: str
    expires_at: datetime


class PairingStatusResponse(BrowserCompanionApiModel):
    status: Literal["pending", "approved", "expired", "used"]


class PairingExchangeRequest(BrowserCompanionApiModel):
    verifier: str = Field(min_length=43, max_length=128)


class PairingExchangeResponse(BrowserCompanionApiModel):
    device_id: str
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime


class PairingApprovalResponse(BrowserCompanionApiModel):
    pairing_id: str
    status: Literal["approved"] = "approved"
    expires_at: datetime


class ExtensionLoginRequest(BrowserCompanionApiModel):
    """Email proof used by the extension when no Web cookie exists."""

    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=6, max_length=6)


class BrowserDeviceResponse(BrowserCompanionApiModel):
    device_id: str
    client_label: str
    client_version: str
    expires_at: datetime
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class BrowserDeviceListResponse(BrowserCompanionApiModel):
    devices: tuple[BrowserDeviceResponse, ...]


class BrowserCaptureResponse(BrowserCompanionApiModel):
    capture_id: str | None
    item_public_id: str
    platform: str
    status: str
    lifecycle: str
    safe_error_code: str | None = None


BearerDocument = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(HTTPBearer(auto_error=False, scheme_name="BrowserCompanionBearer")),
]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
]


def build_browser_companion_router(
    companion,
    capture_submission,
    *,
    scope_dependency: Callable,
    web_origin: str,
    publish_budget_seconds: float,
    email_auth=None,
    library=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/browser-companion", tags=["browser-companion"])

    @router.post(
        "/extension/pairings",
        response_model=PairingCreateResponse,
        status_code=201,
    )
    def create_pairing(payload: PairingCreateRequest) -> PairingCreateResponse:
        try:
            pairing = companion.create_pairing(
                payload.challenge,
                client_label=payload.client_label,
                client_version=payload.client_version,
            )
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None
        return PairingCreateResponse(
            pairing_id=pairing.public_id,
            # The 0.2.0+ extension approves inside its own side panel and never
            # opens this URL; 0.1.x installs still need the Web approval page.
            approval_url=f"{web_origin}/account/browser-companion?pairing={pairing.public_id}",
            expires_at=pairing.expires_at,
        )

    @router.get(
        "/extension/pairings/{pairing_id}",
        response_model=PairingStatusResponse,
    )
    def pairing_status(pairing_id: str) -> PairingStatusResponse:
        try:
            status = companion.pairing_status(pairing_id)
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None
        return PairingStatusResponse(status=status)

    @router.post(
        "/extension/pairings/{pairing_id}:exchange",
        response_model=PairingExchangeResponse,
    )
    def exchange_pairing(
        pairing_id: str, payload: PairingExchangeRequest
    ) -> PairingExchangeResponse:
        try:
            grant = companion.exchange(pairing_id, payload.verifier)
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None
        return PairingExchangeResponse(
            device_id=grant.device_id,
            access_token=grant.raw_token,
            expires_at=grant.expires_at,
        )

    @router.post(
        "/pairings/{pairing_id}:approve",
        response_model=PairingApprovalResponse,
    )
    def approve_pairing(
        pairing_id: str,
        scope=Depends(scope_dependency),
    ) -> PairingApprovalResponse:
        try:
            pairing = companion.approve(scope, pairing_id)
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None
        return PairingApprovalResponse(
            pairing_id=pairing.public_id,
            expires_at=pairing.expires_at,
        )

    @router.post(
        "/extension/pairings/{pairing_id}:approve",
        response_model=PairingApprovalResponse,
    )
    def approve_pairing_from_extension(
        pairing_id: str, payload: ExtensionLoginRequest
    ) -> PairingApprovalResponse:
        """Approve a pairing directly from the extension's email login flow."""
        if email_auth is None:
            raise _safe_error("extension_pairing_invalid")
        try:
            verified = email_auth.verify(payload.email, payload.code)
            pairing = companion.approve(
                UserScope(verified.session.tenant.app_user_id), pairing_id
            )
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None
        except Exception:
            raise _safe_error("extension_pairing_invalid") from None
        return PairingApprovalResponse(
            pairing_id=pairing.public_id,
            expires_at=pairing.expires_at,
        )

    @router.get("/devices", response_model=BrowserDeviceListResponse)
    def list_devices(scope=Depends(scope_dependency)) -> BrowserDeviceListResponse:
        devices = companion.list_devices(scope)
        return BrowserDeviceListResponse(
            devices=tuple(
                BrowserDeviceResponse(**device.__dict__) for device in devices
            )
        )

    @router.delete("/devices/{device_id}", status_code=204)
    def revoke_device(device_id: str, scope=Depends(scope_dependency)) -> None:
        try:
            companion.revoke_device(scope, device_id)
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None

    @router.post(
        "/extension/captures",
        response_model=BrowserCaptureResponse,
    )
    def submit_capture(
        payload: BrowserCaptureRequest,
        request: Request,
        credentials: BearerDocument,
        idempotency_key: IdempotencyKey,
    ) -> BrowserCaptureResponse:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _safe_error("extension_pairing_required")
        try:
            scope = companion.resolve_grant(credentials.credentials)
            digest = hashlib.sha256(idempotency_key.strip().encode()).hexdigest()
            result = capture_submission.submit(
                scope,
                payload,
                request_key=f"extension:{scope.app_user_id}:{digest}",
                publish_budget_seconds=publish_budget_seconds,
            )
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None
        except BrowserCaptureSubmissionError as exc:
            raise _safe_error(exc.error_code) from None
        return BrowserCaptureResponse(
            capture_id=result.capture_public_id,
            item_public_id=result.item_public_id,
            platform=result.platform,
            status=result.status,
            lifecycle=result.lifecycle,
            safe_error_code=result.safe_error_code,
        )

    @router.delete("/extension/grant", status_code=204)
    def disconnect_extension(credentials: BearerDocument) -> None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _safe_error("extension_pairing_required")
        try:
            companion.revoke_token(credentials.credentials)
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None

    def extension_scope(credentials: HTTPAuthorizationCredentials | None) -> UserScope:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _safe_error("extension_pairing_required")
        try:
            return companion.resolve_grant(credentials.credentials)
        except BrowserCompanionError as exc:
            raise _safe_error(exc.error_code) from None

    @router.get("/extension/library", response_model=LibraryPageResponse)
    def extension_library(
        credentials: BearerDocument,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
    ) -> LibraryPageResponse:
        # Capture grants are intentionally write-only. Workspace reads use
        # the extension session bearer through the canonical library routes.
        del credentials, page, page_size, search
        raise _safe_error("extension_scope_invalid")

    @router.get("/extension/library/{item_public_id}", response_model=LibraryItemResponse)
    def extension_library_item(
        item_public_id: str, credentials: BearerDocument
    ) -> LibraryItemResponse:
        del item_public_id, credentials
        raise _safe_error("extension_scope_invalid")

    return router


def _safe_error(code: str) -> HTTPException:
    if code in {
        "extension_pairing_required",
        "extension_grant_expired",
        "extension_grant_revoked",
        "extension_account_disabled",
        "extension_scope_invalid",
    }:
        status = 401
    elif code == "extension_device_not_found":
        status = 404
    elif code in {"extension_pairing_rate_limited", "quota_exceeded"}:
        status = 429
    elif code in {"capture_conflict", "extension_pairing_used"}:
        status = 409
    elif code in {"extension_pairing_expired"}:
        status = 410
    elif code in {"queue_unavailable", "capture_upload_failed"}:
        status = 503
    elif code == "capture_too_large":
        status = 413
    else:
        status = 422
    return HTTPException(status_code=status, detail=code)
