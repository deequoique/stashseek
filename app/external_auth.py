"""Browser-bound external authorization and atomic email-account binding.

Adapters own provider protocols. This service owns the only durable proof,
identity and session transitions; its errors never include provider responses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.channels.types import TenantContext
from app.external_providers.base import ExternalProvider, ProviderError
from app.models import AppUser, ChannelIdentity, ExternalLoginFlow, ExternalLoginIdentity, WebSession
from app.web_auth import (
    EmailDeliveryUnavailable, InvalidSession, InvalidVerification, LoginRateLimited,
    VerifiedWebLogin, WebAuthError, WebAuthService, _sqlite_id, _token_hash, _utc, canonical_email,
)

PROVIDERS = frozenset({"microsoft", "wechat_website", "qq"})
_PROVIDER_LABELS = {"microsoft": "Microsoft", "wechat_website": "WeChat", "qq": "QQ"}


def _provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, "third-party")


ERROR_CODES = frozenset({
    "flow_invalid", "flow_expired", "flow_used", "flow_csrf_invalid", "provider_unavailable",
    "provider_failed", "provider_denied", "session_present", "session_changed", "binding_conflict",
    "verification_failed", "invalid_email", "login_rate_limited", "email_delivery_unavailable", "request_failed",
})
_FLOW_LIFETIME = timedelta(minutes=10)
_MAX_MATERIAL = 32_768


class ExternalLoginError(WebAuthError):
    def __init__(self, error_code: str = "request_failed") -> None:
        super().__init__(error_code if error_code in ERROR_CODES else "request_failed")


@dataclass(frozen=True)
class StartedExternalLogin:
    authorization_url: str = field(repr=False)
    raw_flow_token: str = field(repr=False)
    raw_csrf_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class PendingExternalLogin:
    status: str
    provider: str
    expires_at: datetime
    error_code: str | None = None


@dataclass(frozen=True)
class ExternalCallbackResult:
    status: str
    login: VerifiedWebLogin | None = field(default=None, repr=False)


class ExternalLoginService:
    def __init__(self, email_auth: WebAuthService, settings, providers: Mapping[str, ExternalProvider]) -> None:
        if any(name not in PROVIDERS or name != adapter.name for name, adapter in providers.items()):
            raise ValueError("unsupported external provider")
        self._email_auth = email_auth
        self._settings = settings
        self._providers = providers
        self._factory = email_auth._session_factory
        # Keep the configured mapping itself: removing a provider must also
        # immediately close its DB-only session admission path.
        email_auth._external_providers = providers
        secret = (settings.web_auth_secret or "").encode()
        if len(secret) < 32:
            raise ValueError("Web authentication requires a high-entropy secret")
        self._key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                         info=b"stashseek/external-login/material/v1").derive(secret)

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(name for name in ("microsoft", "wechat_website", "qq") if name in self._providers)

    def _provider(self, name: str):
        provider = self._providers.get(name)
        if provider is None:
            raise ExternalLoginError("provider_unavailable")
        return provider

    def _redirect_uri(self, provider: str) -> str:
        return f"{self._settings.web_public_origin}/api/v1/auth/external/{provider}/callback"

    def _seal(self, material: dict, token_hash: str) -> str:
        raw = json.dumps(material, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > _MAX_MATERIAL:
            raise ExternalLoginError("provider_failed")
        nonce = secrets.token_bytes(12)
        return base64.urlsafe_b64encode(nonce + AESGCM(self._key).encrypt(nonce, raw, token_hash.encode())).decode()

    def _open(self, encrypted: str, token_hash: str) -> dict:
        try:
            if len(encrypted) > 2 * _MAX_MATERIAL:
                raise ValueError()
            encoded = base64.urlsafe_b64decode(encrypted)
            raw = AESGCM(self._key).decrypt(encoded[:12], encoded[12:], token_hash.encode())
            value = json.loads(raw)
            if not isinstance(value, dict) or len(raw) > _MAX_MATERIAL:
                raise ValueError()
            return value
        except Exception:
            raise ExternalLoginError("provider_failed") from None

    def _ensure_anonymous(self, session_token: str, *, changed: bool, now: datetime) -> None:
        if not session_token:
            return
        try:
            self._email_auth.resolve_session(session_token, now=now)
        except InvalidSession:
            return
        raise ExternalLoginError("session_changed" if changed else "session_present")

    @staticmethod
    def _flow(db, token: str, *, now: datetime, phase: str | None = None, lock: bool = False):
        try:
            digest = _token_hash(token)
        except ValueError:
            raise ExternalLoginError("flow_invalid") from None
        query = select(ExternalLoginFlow).where(ExternalLoginFlow.token_hash == digest)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise ExternalLoginError("flow_invalid")
        if _utc(row.expires_at) <= now:
            raise ExternalLoginError("flow_expired")
        if phase is not None and row.phase != phase:
            raise ExternalLoginError("flow_used")
        return row

    def _configured_flow(self, flow):
        provider = self._provider(flow.provider)
        if (provider.application_id != flow.application_id
                or provider.configuration_id != flow.configuration_id):
            raise ExternalLoginError("provider_unavailable")
        return provider

    def _fail(self, token: str, error_code: str) -> None:
        try:
            digest = _token_hash(token)
        except ValueError:
            return
        with self._factory() as db:
            db.execute(update(ExternalLoginFlow).where(
                ExternalLoginFlow.token_hash == digest,
                ExternalLoginFlow.phase.in_(("created", "exchanging", "email_required")),
            ).values(phase="failed", failure_code=error_code if error_code in ERROR_CODES else "request_failed",
                     material_ciphertext=None, namespace=None, subject=None))
            db.commit()

    def _new_flow(self, provider: str, *, current: datetime, client_ip: str,
                  previous_flow_token: str, failure_code: str | None = None):
        if provider not in PROVIDERS:
            raise ExternalLoginError("provider_unavailable")
        raw, csrf, state = (secrets.token_urlsafe(32) for _ in range(3))
        adapter = self._providers.get(provider)
        requester = hmac.new(self._key, client_ip[:128].encode(), hashlib.sha256).hexdigest()
        with self._factory() as db:
            if db.get_bind().dialect.name == "postgresql":
                # Serialize admission for a requester, never persist their IP.
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": int(requester[:15], 16)})
            count = db.scalar(select(func.count()).select_from(ExternalLoginFlow).where(
                ExternalLoginFlow.requester_hash == requester,
                ExternalLoginFlow.created_at > current - _FLOW_LIFETIME,
            ))
            if count >= 20:
                raise ExternalLoginError("login_rate_limited")
            old_ids = select(ExternalLoginFlow.id).where(
                ExternalLoginFlow.expires_at < current - timedelta(hours=1)
            ).order_by(ExternalLoginFlow.id).limit(100)
            db.execute(delete(ExternalLoginFlow).where(ExternalLoginFlow.id.in_(old_ids)))
            if previous_flow_token:
                try:
                    previous_hash = _token_hash(previous_flow_token)
                except ValueError:
                    previous_hash = ""
                db.execute(update(ExternalLoginFlow).where(
                    ExternalLoginFlow.token_hash == previous_hash,
                    ExternalLoginFlow.phase.not_in(("completed", "cancelled")),
                ).values(phase="cancelled", material_ciphertext=None, namespace=None, subject=None))
            row = ExternalLoginFlow(
                token_hash=_token_hash(raw), state_hash=_token_hash(state), csrf_token_hash=_token_hash(csrf),
                requester_hash=requester, provider=provider,
                application_id=adapter.application_id if adapter else "",
                configuration_id=adapter.configuration_id if adapter else "",
                phase="failed" if failure_code else "created", failure_code=failure_code,
                expires_at=current + _FLOW_LIFETIME, created_at=current,
            )
            _sqlite_id(db, row, ExternalLoginFlow)
            db.add(row)
            db.commit()
        return raw, csrf, state

    def start(self, provider: str, *, client_ip: str = "unknown", previous_flow_token: str = "",
              session_token: str = "", now: datetime | None = None) -> StartedExternalLogin:
        current = _utc(now)
        self._ensure_anonymous(session_token, changed=False, now=current)
        adapter = self._provider(provider)
        raw, csrf, state = self._new_flow(provider, current=current, client_ip=client_ip,
                                          previous_flow_token=previous_flow_token)
        try:
            authorization = adapter.begin(redirect_uri=self._redirect_uri(provider), state=state)
            if not isinstance(authorization.authorization_url, str) or not 1 <= len(authorization.authorization_url) <= 16_384:
                raise ExternalLoginError("provider_failed")
            material = self._seal(authorization.material, _token_hash(raw))
            with self._factory() as db:
                flow = self._flow(db, raw, now=_utc(now), phase="created", lock=True)
                self._configured_flow(flow)
                flow.material_ciphertext = material
                db.commit()
        except Exception:
            self._fail(raw, "provider_failed")
            raise ExternalLoginError("provider_failed") from None
        return StartedExternalLogin(authorization.authorization_url, raw, csrf, current + _FLOW_LIFETIME)

    def failure_notice(self, code: str, *, provider: str = "microsoft", previous_flow_token: str = "",
                       client_ip: str = "unknown", now: datetime | None = None) -> StartedExternalLogin:
        current = _utc(now)
        raw, csrf, _ = self._new_flow(provider, current=current, client_ip=client_ip,
                                     previous_flow_token=previous_flow_token,
                                     failure_code=code if code in ERROR_CODES else "request_failed")
        return StartedExternalLogin("/login/external", raw, csrf, current + _FLOW_LIFETIME)

    def linked_providers(self, scope) -> tuple[str, ...]:
        """Providers currently bound to this app user, never another tenant."""

        with self._factory() as db:
            rows = db.scalars(select(ExternalLoginIdentity.provider).where(
                ExternalLoginIdentity.app_user_id == scope.app_user_id,
                ExternalLoginIdentity.disabled_at.is_(None),
            ))
            return tuple(sorted({row for row in rows if row in PROVIDERS}))

    def unlink(self, scope, provider: str) -> None:
        """Remove one provider binding; the email identity remains the anchor."""

        if provider not in PROVIDERS:
            raise ExternalLoginError("provider_unavailable")
        with self._factory() as db:
            identity = db.scalar(select(ExternalLoginIdentity).where(
                ExternalLoginIdentity.app_user_id == scope.app_user_id,
                ExternalLoginIdentity.provider == provider,
                ExternalLoginIdentity.disabled_at.is_(None),
            ))
            if identity is None:
                raise ExternalLoginError("not_found")
            db.delete(identity)
            db.commit()

    def pending(self, raw_flow_token: str, *, session_token: str = "", now: datetime | None = None) -> PendingExternalLogin:
        current = _utc(now)
        with self._factory() as db:
            try:
                digest = _token_hash(raw_flow_token)
            except ValueError:
                raise ExternalLoginError("flow_invalid") from None
            flow = db.scalar(select(ExternalLoginFlow).where(ExternalLoginFlow.token_hash == digest))
            if flow is None:
                raise ExternalLoginError("flow_invalid")
            if _utc(flow.expires_at) <= current:
                return PendingExternalLogin("expired", flow.provider, _utc(flow.expires_at), "flow_expired")
            result = PendingExternalLogin(flow.phase, flow.provider, _utc(flow.expires_at), flow.failure_code)
            expected = flow.completed_session_hash
        if result.status == "completed" and session_token:
            try:
                self._email_auth.resolve_session(session_token, now=current)
            except InvalidSession:
                pass
            else:
                if not expected or not hmac.compare_digest(expected, _token_hash(session_token)):
                    return PendingExternalLogin("failed", result.provider, result.expires_at, "session_changed")
        return result

    def validate_flow_csrf(self, raw_flow_token: str, raw_csrf: str, *, now: datetime | None = None) -> None:
        try:
            csrf_hash = _token_hash(raw_csrf)
        except ValueError:
            raise ExternalLoginError("flow_csrf_invalid") from None
        with self._factory() as db:
            flow = self._flow(db, raw_flow_token, now=_utc(now))
            if not hmac.compare_digest(flow.csrf_token_hash, csrf_hash):
                raise ExternalLoginError("flow_csrf_invalid")

    def cancel(self, raw_flow_token: str, *, now: datetime | None = None) -> None:
        with self._factory() as db:
            flow = self._flow(db, raw_flow_token, now=_utc(now), lock=True)
            if flow.phase != "completed":
                flow.phase = "cancelled"
                flow.material_ciphertext = flow.namespace = flow.subject = None
            db.commit()

    def _mapping_query(self, flow):
        return select(ExternalLoginIdentity).where(
            ExternalLoginIdentity.provider == flow.provider,
            ExternalLoginIdentity.application_id == flow.application_id,
            ExternalLoginIdentity.namespace == flow.namespace,
            ExternalLoginIdentity.subject == flow.subject,
        )

    @staticmethod
    def _mapping_tenant(db, mapping) -> tuple[TenantContext, ExternalLoginIdentity]:
        user = db.scalar(select(AppUser).where(AppUser.id == mapping.app_user_id).with_for_update().execution_options(populate_existing=True))
        identity = db.scalar(select(ChannelIdentity).where(
            ChannelIdentity.id == mapping.channel_identity_id
        ).with_for_update().execution_options(populate_existing=True))
        mapping = db.scalar(select(ExternalLoginIdentity).where(
            ExternalLoginIdentity.id == mapping.id
        ).with_for_update().execution_options(populate_existing=True))
        if (mapping is None or mapping.disabled_at is not None or user is None or user.disabled_at is not None
                or identity is None or identity.disabled_at is not None
                or mapping.app_user_id != user.id or identity.app_user_id != user.id
                or mapping.channel_identity_id != identity.id or identity.channel != "web" or identity.account_id != "web"):
            raise ExternalLoginError("provider_unavailable")
        if canonical_email(identity.external_user_id) != identity.external_user_id:
            raise ExternalLoginError("provider_unavailable")
        return TenantContext(user.id, identity.id, identity.channel, identity.account_id, identity.external_user_id), mapping

    def callback(self, provider: str, *, raw_flow_token: str, state: str, code: str = "", error: str = "",
                 session_token: str = "", now: datetime | None = None) -> ExternalCallbackResult:
        try:
            self._ensure_anonymous(session_token, changed=True, now=_utc(now))
            with self._factory() as db:
                flow = self._flow(db, raw_flow_token, now=_utc(now), phase="created", lock=True)
                adapter = self._configured_flow(flow)
                if flow.provider != provider or not state or not hmac.compare_digest(flow.state_hash, _token_hash(state)):
                    raise ExternalLoginError("flow_invalid")
                if error:
                    raise ExternalLoginError("provider_denied")
                if not code or len(code) > 4096:
                    raise ExternalLoginError("flow_invalid")
                material = self._open(flow.material_ciphertext or "", flow.token_hash)
                flow.phase, flow.material_ciphertext = "exchanging", None
                db.commit()
            # The one-time claim is committed. No database transaction spans
            # this potentially slow, ambiguous external code exchange.
            principal = adapter.complete(redirect_uri=self._redirect_uri(provider), code=code, material=material)
            self._ensure_anonymous(session_token, changed=True, now=_utc(now))
            with self._factory() as db:
                flow = self._flow(db, raw_flow_token, now=_utc(now), phase="exchanging", lock=True)
                self._configured_flow(flow)
                if (not isinstance(principal.namespace, str) or not 1 <= len(principal.namespace) <= 1024
                        or not isinstance(principal.subject, str) or not 1 <= len(principal.subject) <= 1024):
                    raise ExternalLoginError("provider_failed")
                flow.namespace, flow.subject = principal.namespace, principal.subject
                mapping = db.scalar(self._mapping_query(flow))
                if mapping is None:
                    flow.phase = "email_required"
                    db.commit()
                    return ExternalCallbackResult("email_required")
                tenant, mapping = self._mapping_tenant(db, mapping)
                login = self._email_auth._issue_session(db, tenant, current=_utc(now),
                    login_channel=provider, external_login_identity_id=mapping.id)
                flow.phase, flow.completed_session_hash = "completed", _token_hash(login.raw_session_token)
                flow.namespace = flow.subject = None
                db.commit()
                return ExternalCallbackResult("completed", login)
        except ExternalLoginError as exc:
            # A replay cannot cancel an already-claimed exchange. Only mark
            # failures for operations that did not lose a claim to a peer.
            if exc.error_code != "flow_used":
                self._fail(raw_flow_token, exc.error_code)
            raise
        except (ProviderError, ValueError):
            self._fail(raw_flow_token, "provider_failed")
            raise ExternalLoginError("provider_failed") from None
        except Exception:
            self._fail(raw_flow_token, "request_failed")
            raise ExternalLoginError("request_failed") from None

    def request_email_challenge(self, raw_flow_token: str, email: str, client_ip: str,
                                *, now: datetime | None = None) -> None:
        canonical, current = canonical_email(email), _utc(now)
        with self._factory() as db:
            flow = self._flow(db, raw_flow_token, now=current, phase="email_required", lock=True)
            self._configured_flow(flow)
            if not self._email_auth._limiter.allow(canonical, client_ip, now=current):
                raise LoginRateLimited()
            try:
                self._email_auth._request_email_proof(
                    db, canonical, current=current,
                    external_login_flow_id=flow.id,
                    expires_at=_utc(flow.expires_at),
                    purpose=(
                        "linking a " + _provider_label(flow.provider) + " account"
                    ),
                )
            except EmailDeliveryUnavailable:
                db.commit()
                raise
            db.commit()

    def verify_email(self, raw_flow_token: str, email: str, code: str, *, confirm_binding: bool,
                     session_token: str = "", now: datetime | None = None) -> VerifiedWebLogin:
        canonical, current = canonical_email(email), _utc(now)
        try:
            self._ensure_anonymous(session_token, changed=True, now=current)
        except ExternalLoginError as exc:
            self._fail(raw_flow_token, exc.error_code)
            raise
        if confirm_binding is not True:
            raise ExternalLoginError("verification_failed")
        with self._factory() as db:
            flow = self._flow(db, raw_flow_token, now=current, phase="email_required", lock=True)
            self._configured_flow(flow)
            proof = self._email_auth._verify_email_proof(db, canonical, code,
                current=current, external_login_flow_id=flow.id)
            if proof is None:
                db.commit()
                raise InvalidVerification()
            tenant = self._email_auth._resolve_email_tenant(db, canonical)
            mapping = db.scalar(self._mapping_query(flow).with_for_update().execution_options(populate_existing=True))
            if mapping is None:
                try:
                    with db.begin_nested():
                        mapping = ExternalLoginIdentity(provider=flow.provider, application_id=flow.application_id,
                            namespace=flow.namespace, subject=flow.subject, app_user_id=tenant.app_user_id,
                            channel_identity_id=tenant.channel_identity_id)
                        _sqlite_id(db, mapping, ExternalLoginIdentity)
                        db.add(mapping)
                        db.flush()
                except IntegrityError:
                    mapping = db.scalar(self._mapping_query(flow).with_for_update())
            if (mapping is None or mapping.disabled_at is not None or mapping.app_user_id != tenant.app_user_id
                    or mapping.channel_identity_id != tenant.channel_identity_id):
                raise ExternalLoginError("binding_conflict")
            login = self._email_auth._issue_session(db, tenant, current=current,
                login_channel=flow.provider, external_login_identity_id=mapping.id)
            proof.consumed_at = current
            flow.phase, flow.completed_session_hash = "completed", _token_hash(login.raw_session_token)
            flow.namespace = flow.subject = flow.material_ciphertext = None
            db.commit()
            return login

    def disable_identity(self, identity_id: int, *, now: datetime | None = None) -> None:
        """Administrative building block; no self-service unlink route exists."""
        with self._factory() as db:
            mapping = db.get(ExternalLoginIdentity, identity_id)
            if mapping is None:
                raise ExternalLoginError("binding_conflict")
            _, mapping = self._mapping_tenant(db, mapping)
            mapping.disabled_at = _utc(now)
            db.execute(update(WebSession).where(WebSession.external_login_identity_id == identity_id,
                WebSession.revoked_at.is_(None)).values(revoked_at=_utc(now)))
            db.commit()
