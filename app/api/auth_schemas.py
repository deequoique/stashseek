"""Strict HTTP DTOs for the same-origin Web authentication contract.

Browser login uses email codes and configured website identity providers.
The channel challenge DTOs
remain in this module only for the migration-era compatibility router; the
email router uses the ``Email*`` models below and therefore exports a
browser-safe, identifier-free session projection.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChallengeCreateRequest(StrictSchema):
    target_channel: Literal["telegram", "wechat"]


class ChallengeCreateResponse(StrictSchema):
    public_id: str
    command: str
    browser_secret: str
    target_channel: Literal["telegram", "wechat"]
    expires_at: datetime


class ChallengeReferenceRequest(StrictSchema):
    public_id: str = Field(min_length=1, max_length=200)


class ChallengeStatusResponse(StrictSchema):
    status: Literal["pending", "approved"]
    expires_at: datetime


class SessionResponse(StrictSchema):
    authenticated: Literal[True] = True
    login_channel: Literal[
        "email", "telegram", "wechat", "microsoft", "wechat_website", "qq"
    ]
    expires_at: datetime


class AuthErrorResponse(StrictSchema):
    code: str
    message: str


class EmailChallengeRequest(StrictSchema):
    """Request an email verification code.

    Server-side normalization and validation remain authoritative; the
    bounded field prevents oversized bodies from reaching the auth service.
    """

    email: str = Field(min_length=3, max_length=254)


class AcceptedResponse(StrictSchema):
    status: Literal["accepted"] = "accepted"


class EmailVerifyRequest(EmailChallengeRequest):
    code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
    )


ExternalLoginProvider = Literal["microsoft", "wechat_website", "qq"]
ExternalLoginErrorCode = Literal[
    "flow_invalid", "flow_expired", "flow_used", "flow_csrf_invalid",
    "provider_unavailable", "provider_failed", "provider_denied",
    "session_present", "session_changed", "binding_conflict",
    "verification_failed", "invalid_email", "login_rate_limited",
    "email_delivery_unavailable", "request_failed",
]


class ExternalLoginPendingResponse(StrictSchema):
    status: Literal[
        "created", "exchanging", "email_required", "completed",
        "failed", "cancelled", "expired",
    ]
    provider: ExternalLoginProvider | None = None
    expires_at: datetime | None = None
    error_code: ExternalLoginErrorCode | None = None


class ExternalLoginVerifyRequest(EmailVerifyRequest):
    confirm_binding: Literal[True]

    @field_validator("confirm_binding", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit confirmation required")
        return value
