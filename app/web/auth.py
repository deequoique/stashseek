"""Channel-assisted challenge and opaque server-session authentication."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.channels.types import TenantContext, UserScope
from app.models import AppUser, ChannelIdentity, WebLoginChallenge, WebSession


SESSION_COOKIE_NAME = "__Host-kb_session"
CSRF_COOKIE_NAME = "__Host-kb_csrf"
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CHALLENGE_CREATE_LOCK_ID = 0x4E_4F_54_45_42_4F_4F
_RETENTION_DELETE_BATCH_SIZE = 100


_SAFE_MESSAGES = {
    "channel_unavailable": "该登录渠道当前不可用。",
    "challenge_invalid": "登录请求无效，请重新开始。",
    "challenge_expired": "登录请求已过期，请重新开始。",
    "challenge_pending": "请先在所选渠道批准登录。",
    "challenge_used": "登录请求已使用，请重新开始。",
    "account_disabled": "账户不可用。",
    "session_invalid": "登录已失效，请重新登录。",
    "csrf_invalid": "请求验证失败，请刷新后重试。",
    "rate_limited": "登录请求过于频繁，请稍后重试。",
}


class WebAuthError(Exception):
    """Stable, non-secret error safe to map at channel or HTTP boundaries."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_SAFE_MESSAGES.get(code, "请求无法完成。"))


@dataclass(frozen=True)
class LoginChallengeCredentials:
    public_id: str
    code: str
    browser_secret: str
    target_channel: str
    expires_at: datetime


@dataclass(frozen=True)
class LoginChallengeStatus:
    status: str
    expires_at: datetime


@dataclass(frozen=True)
class WebSessionCredentials:
    session_token: str
    csrf_token: str
    expires_at: datetime
    user_scope: UserScope


@dataclass(frozen=True)
class ResolvedWebSession(UserScope):
    public_id: str
    login_channel: str
    expires_at: datetime


def _sha256(raw_value: str, field: str) -> str:
    normalized = str(raw_value).strip()
    if not normalized:
        code = {
            "browser": "challenge_invalid",
            "csrf": "csrf_invalid",
        }.get(field, "session_invalid")
        raise WebAuthError(code)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_login_code(raw_code: str, secret: str) -> str:
    code = str(raw_code).strip().upper()
    if not code:
        raise WebAuthError("challenge_invalid")
    return hmac.new(
        secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_browser_secret(raw_value: str) -> str:
    return _sha256(raw_value, "browser")


def hash_session_token(raw_value: str) -> str:
    return _sha256(raw_value, "session")


def hash_csrf_token(raw_value: str) -> str:
    return _sha256(raw_value, "csrf")


class WebAuthService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        secret: str,
        challenge_ttl: timedelta = timedelta(minutes=10),
        session_ttl: timedelta = timedelta(days=30),
        attempt_limit: int = 5,
        enabled_channels: Iterable[str] = ("telegram", "wechat"),
        challenge_rate_window: timedelta = timedelta(minutes=1),
        challenge_rate_limit_per_requester: int = 5,
        challenge_global_rate_limit: int = 100,
        challenge_active_limit_per_requester: int = 3,
        challenge_retention: timedelta = timedelta(days=1),
        session_retention: timedelta = timedelta(days=7),
    ) -> None:
        if len(str(secret)) < 32:
            raise ValueError("WEB_AUTH_SECRET must contain at least 32 characters")
        if challenge_ttl <= timedelta(0) or session_ttl <= timedelta(0):
            raise ValueError("Web authentication TTL values must be positive")
        if attempt_limit <= 0:
            raise ValueError("Web authentication attempt limit must be positive")
        if challenge_rate_window <= timedelta(0):
            raise ValueError("Web authentication rate window must be positive")
        if min(
            challenge_rate_limit_per_requester,
            challenge_global_rate_limit,
            challenge_active_limit_per_requester,
        ) <= 0:
            raise ValueError("Web authentication challenge limits must be positive")
        if challenge_retention <= timedelta(0) or session_retention <= timedelta(0):
            raise ValueError("Web authentication retention must be positive")
        if challenge_retention < challenge_rate_window:
            raise ValueError(
                "Web authentication challenge retention must cover the rate window"
            )
        channels = tuple(dict.fromkeys(str(value).strip().lower() for value in enabled_channels))
        if not channels or set(channels) - {"telegram", "wechat"}:
            raise ValueError("enabled channels must contain telegram and/or wechat")
        self._session_factory = session_factory
        self._secret = str(secret)
        self._challenge_ttl = challenge_ttl
        self._session_ttl = session_ttl
        self._attempt_limit = attempt_limit
        self._enabled_channels = channels
        self._challenge_rate_window = challenge_rate_window
        self._challenge_rate_limit_per_requester = challenge_rate_limit_per_requester
        self._challenge_global_rate_limit = challenge_global_rate_limit
        self._challenge_active_limit_per_requester = (
            challenge_active_limit_per_requester
        )
        self._challenge_retention = challenge_retention
        self._session_retention = session_retention

    def create_challenge(
        self,
        target_channel: str,
        *,
        requester_key: str = "direct-call",
        now: datetime | None = None,
    ) -> LoginChallengeCredentials:
        channel = str(target_channel).strip().lower()
        if channel not in self._enabled_channels:
            raise WebAuthError("channel_unavailable")
        current = _utc_now(now)
        code = _new_code()
        browser_secret = secrets.token_urlsafe(32)
        public_id = secrets.token_urlsafe(24)
        expires_at = current + self._challenge_ttl
        requester_hash = _hash_requester(requester_key, self._secret)
        with self._session_factory() as db:
            self._lock_and_enforce_challenge_limits(
                db,
                requester_hash=requester_hash,
                now=current,
            )
            db.add(
                WebLoginChallenge(
                    public_id=public_id,
                    code_hash=hash_login_code(code, self._secret),
                    browser_token_hash=hash_browser_secret(browser_secret),
                    requester_hash=requester_hash,
                    target_channel=channel,
                    expires_at=expires_at,
                    created_at=current,
                )
            )
            db.commit()
        return LoginChallengeCredentials(
            public_id, code, browser_secret, channel, expires_at
        )

    def _lock_and_enforce_challenge_limits(
        self,
        db: Session,
        *,
        requester_hash: str,
        now: datetime,
    ) -> None:
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            db.execute(
                select(func.pg_advisory_xact_lock(_CHALLENGE_CREATE_LOCK_ID))
            )
        # Delete in bounded batches so a public request cannot turn retention
        # backlog into one unbounded transaction.  Retention starts only after
        # expiry, so active challenges are never removed.
        challenge_ids = (
            select(WebLoginChallenge.id)
            .where(
                WebLoginChallenge.expires_at
                < now - self._challenge_retention
            )
            .order_by(
                WebLoginChallenge.expires_at,
                WebLoginChallenge.id,
            )
            .limit(_RETENTION_DELETE_BATCH_SIZE)
        )
        db.execute(
            delete(WebLoginChallenge).where(
                WebLoginChallenge.id.in_(challenge_ids)
            )
        )
        session_cutoff = now - self._session_retention
        session_ids = (
            select(WebSession.id)
            .where(
                or_(
                    WebSession.expires_at < session_cutoff,
                    WebSession.revoked_at < session_cutoff,
                )
            )
            .order_by(WebSession.created_at, WebSession.id)
            .limit(_RETENTION_DELETE_BATCH_SIZE)
        )
        db.execute(
            delete(WebSession).where(
                WebSession.id.in_(session_ids)
            )
        )
        window_start = now - self._challenge_rate_window
        global_recent = db.scalar(
            select(func.count(WebLoginChallenge.id)).where(
                WebLoginChallenge.created_at >= window_start
            )
        ) or 0
        requester_recent = db.scalar(
            select(func.count(WebLoginChallenge.id)).where(
                WebLoginChallenge.requester_hash == requester_hash,
                WebLoginChallenge.created_at >= window_start,
            )
        ) or 0
        requester_active = db.scalar(
            select(func.count(WebLoginChallenge.id)).where(
                WebLoginChallenge.requester_hash == requester_hash,
                WebLoginChallenge.expires_at > now,
                WebLoginChallenge.consumed_at.is_(None),
                WebLoginChallenge.cancelled_at.is_(None),
            )
        ) or 0
        if (
            global_recent >= self._challenge_global_rate_limit
            or requester_recent >= self._challenge_rate_limit_per_requester
            or requester_active >= self._challenge_active_limit_per_requester
        ):
            # Persist bounded retention cleanup before returning the stable
            # public limit error.  Rolling the transaction back here would
            # silently defeat cleanup whenever the service is under pressure.
            db.commit()
            raise WebAuthError("rate_limited")

    def status(
        self,
        public_id: str,
        browser_secret: str,
        *,
        now: datetime | None = None,
    ) -> LoginChallengeStatus:
        current = _utc_now(now)
        with self._session_factory() as db:
            challenge = db.scalar(
                select(WebLoginChallenge).where(
                    WebLoginChallenge.public_id == str(public_id).strip()
                )
            )
            self._verify_browser(challenge, browser_secret)
            assert challenge is not None
            if challenge.cancelled_at is not None:
                state = "cancelled"
            elif challenge.consumed_at is not None:
                state = "consumed"
            elif challenge.expires_at <= current:
                state = "expired"
            elif challenge.approved_at is not None:
                state = "approved"
            else:
                state = "pending"
            return LoginChallengeStatus(state, challenge.expires_at)

    def approve(
        self,
        raw_code: str,
        tenant: TenantContext,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _utc_now(now)
        with self._session_factory() as db:
            challenge = db.scalar(
                select(WebLoginChallenge)
                .where(
                    WebLoginChallenge.code_hash
                    == hash_login_code(raw_code, self._secret)
                )
                .with_for_update()
            )
            if challenge is None:
                raise WebAuthError("challenge_invalid")
            if challenge.attempt_count >= self._attempt_limit:
                raise WebAuthError("challenge_invalid")
            if challenge.cancelled_at is not None or challenge.consumed_at is not None:
                raise WebAuthError("challenge_used")
            if challenge.expires_at <= current:
                raise WebAuthError("challenge_expired")
            if challenge.approved_at is not None:
                raise WebAuthError("challenge_used")
            if challenge.target_channel != tenant.channel:
                challenge.attempt_count += 1
                db.commit()
                raise WebAuthError("challenge_invalid")
            user = db.get(AppUser, tenant.app_user_id)
            identity = db.get(ChannelIdentity, tenant.channel_identity_id)
            if (
                user is None
                or user.disabled_at is not None
                or identity is None
                or identity.disabled_at is not None
                or identity.app_user_id != tenant.app_user_id
                or identity.channel != tenant.channel
            ):
                raise WebAuthError("account_disabled")
            challenge.approved_app_user_id = tenant.app_user_id
            challenge.approved_by_identity_id = tenant.channel_identity_id
            challenge.approved_at = current
            db.commit()

    def exchange(
        self,
        public_id: str,
        browser_secret: str,
        *,
        now: datetime | None = None,
    ) -> WebSessionCredentials:
        current = _utc_now(now)
        with self._session_factory() as db:
            challenge = db.scalar(
                select(WebLoginChallenge)
                .where(WebLoginChallenge.public_id == str(public_id).strip())
                .with_for_update()
            )
            self._verify_browser(challenge, browser_secret)
            assert challenge is not None
            if challenge.attempt_count >= self._attempt_limit:
                raise WebAuthError("challenge_invalid")
            if challenge.cancelled_at is not None or challenge.consumed_at is not None:
                raise WebAuthError("challenge_used")
            if challenge.expires_at <= current:
                raise WebAuthError("challenge_expired")
            if challenge.approved_at is None or challenge.approved_app_user_id is None:
                raise WebAuthError("challenge_pending")
            user = db.get(AppUser, challenge.approved_app_user_id)
            if user is None or user.disabled_at is not None:
                raise WebAuthError("account_disabled")
            session_token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            expires_at = current + self._session_ttl
            db.add(
                WebSession(
                    public_id=secrets.token_urlsafe(24),
                    token_hash=hash_session_token(session_token),
                    csrf_token_hash=hash_csrf_token(csrf_token),
                    app_user_id=user.id,
                    login_channel=challenge.target_channel,
                    expires_at=expires_at,
                )
            )
            challenge.consumed_at = current
            db.commit()
        return WebSessionCredentials(
            session_token, csrf_token, expires_at, UserScope(user.id)
        )

    def resolve_session(
        self, raw_token: str, *, now: datetime | None = None
    ) -> ResolvedWebSession:
        current = _utc_now(now)
        with self._session_factory() as db:
            row = db.scalar(
                select(WebSession).where(
                    WebSession.token_hash == hash_session_token(raw_token)
                )
            )
            if (row is None or row.external_login_identity_id is not None
                    or row.login_channel not in {"telegram", "wechat"}
                    or row.revoked_at is not None or row.expires_at <= current):
                raise WebAuthError("session_invalid")
            user = db.get(AppUser, row.app_user_id)
            if user is None or user.disabled_at is not None:
                raise WebAuthError("session_invalid")
            return ResolvedWebSession(
                user.id, row.public_id, row.login_channel, row.expires_at
            )

    def validate_csrf(
        self,
        raw_session_token: str,
        raw_csrf_token: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _utc_now(now)
        with self._session_factory() as db:
            row = db.scalar(
                select(WebSession).where(
                    WebSession.token_hash == hash_session_token(raw_session_token)
                )
            )
            if (row is None or row.external_login_identity_id is not None
                    or row.login_channel not in {"telegram", "wechat"}
                    or row.revoked_at is not None or row.expires_at <= current):
                raise WebAuthError("session_invalid")
            supplied = hash_csrf_token(raw_csrf_token)
            if not hmac.compare_digest(row.csrf_token_hash, supplied):
                raise WebAuthError("csrf_invalid")

    def revoke_session(
        self, raw_token: str, *, now: datetime | None = None
    ) -> None:
        current = _utc_now(now)
        with self._session_factory() as db:
            row = db.scalar(
                select(WebSession)
                .where(WebSession.token_hash == hash_session_token(raw_token))
                .with_for_update()
            )
            if row is not None and row.revoked_at is None:
                row.revoked_at = current
                db.commit()

    @staticmethod
    def _verify_browser(
        challenge: WebLoginChallenge | None, raw_browser_secret: str
    ) -> None:
        if challenge is None:
            raise WebAuthError("challenge_invalid")
        supplied = hash_browser_secret(raw_browser_secret)
        if not hmac.compare_digest(challenge.browser_token_hash, supplied):
            raise WebAuthError("challenge_invalid")


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _hash_requester(raw_value: str, secret: str) -> str:
    value = str(raw_value).strip()[:200] or "unknown"
    return hmac.new(
        secret.encode("utf-8"),
        f"requester:{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("authentication timestamps must be timezone-aware")
    return current.astimezone(UTC)
