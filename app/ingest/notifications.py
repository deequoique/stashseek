"""PostgreSQL-backed source-channel completion notifications.

The completion event is the durable ingestion fact.  This module owns the
separate ``source-channel.notification.v1`` delivery ledger and performs the
small, bounded outbound effect after a database claim has committed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import ipaddress
import json
import logging
import math
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import Settings, _validate_langbot_outbound_url
from app.models import (
    AppUser,
    ChannelIdentity,
    ContentItem,
    ConversationThread,
    IngestCompletionDelivery,
    IngestCompletionEvent,
    IngestDispatch,
)


HANDLER_KEY = "source-channel.notification.v1"
SUPPORTED_SOURCE_CHANNELS = frozenset({"telegram", "wechat"})
SAFE_SUCCESS_DISPOSITIONS = frozenset(
    {
        "sent",
        "skipped_no_channel",
        "skipped_deleted",
        "skipped_identity_disabled",
        "skipped_owner_disabled",
        "skipped_target_mismatch",
    }
)
SAFE_ERROR_CODES = frozenset(
    {
        "outbound_api_key_missing",
        "invalid_outbound_target",
        "outbound_auth_rejected",
        "outbound_contract_invalid",
        "outbound_target_not_found",
        "outbound_rate_limited",
        "outbound_server_error",
        "outbound_http_4xx",
        "outbound_http_status",
        "outbound_timeout",
        "outbound_connect_failed",
        "outbound_transport_failed",
        "redirect_rejected",
        "notification_deferred",
        "notification_internal_failure",
        "retry_exhausted",
        "manual_redrive",
    }
)
_NOTIFICATION_DIAGNOSTIC_EVENTS = frozenset(
    {
        "notification_sweep",
        "notification_event_delivery",
        "notification_poller_heartbeat",
    }
)
_NOTIFICATION_DIAGNOSTIC_FIELDS = frozenset(
    {
        "heartbeat",
        "observability_failed",
        "claimed",
        "succeeded",
        "skipped",
        "failed",
        "deferred",
        "duration_ms",
        "oldest_eligible_backlog_age_seconds",
    }
)
_MAX_DIAGNOSTIC_INTEGER = 2_147_483_647

_LOGGER = logging.getLogger("notebook_agent.runtime")


class NotificationTransportError(RuntimeError):
    """A stable, privacy-safe outbound transport classification."""

    def __init__(self, error_code: str, *, retryable: bool) -> None:
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(error_code)


def sanitize_title(title: str | None, *, max_codepoints: int = 80) -> str:
    """Remove controls, fold whitespace, and bound an outbound title."""

    if max_codepoints <= 0:
        return ""
    value = str(title or "")
    cleaned = "".join(
        " " if character.isspace() else character
        for character in value
        if not (ord(character) < 32 or 127 <= ord(character) < 160)
    )
    return " ".join(cleaned.split())[:max_codepoints]


def render_completion_notification(
    event_or_outcome: Any,
    item_state: str | None = None,
    title: str | None = None,
    *,
    max_body_codepoints: int = 320,
) -> str:
    """Render one of four fixed Chinese completion messages.

    ``event_or_outcome`` may be an ORM event, a mapping, or the outcome string
    itself.  The renderer intentionally reads only the terminal snapshot and a
    bounded title; it never includes URLs, provider details, or identifiers.
    """

    if isinstance(event_or_outcome, str):
        outcome = event_or_outcome
        snapshot_state = item_state
        snapshot_title = title
    elif isinstance(event_or_outcome, dict):
        outcome = event_or_outcome.get("outcome")
        snapshot_state = event_or_outcome.get("item_state")
        snapshot_title = event_or_outcome.get("title", title)
    else:
        outcome = getattr(event_or_outcome, "outcome", None)
        snapshot_state = getattr(event_or_outcome, "item_state", item_state)
        snapshot_title = getattr(event_or_outcome, "title", title)

    label = sanitize_title(snapshot_title) or "你提交的视频"
    if outcome == "completed" and snapshot_state == "ready":
        message = f"{label}已解析并加入知识库，可以开始提问。"
    elif outcome == "completed" and snapshot_state == "needs_extension":
        message = f"{label}已保存，但需要浏览器扩展补充文本后才能检索。"
    elif outcome == "completed" and snapshot_state == "needs_asr":
        message = f"{label}已保存，但需要语音识别后才能检索。"
    elif outcome == "failed" and snapshot_state == "failed":
        message = f"{label}解析失败，请稍后在知识库中重试。"
    else:
        # Invalid snapshots are never sent by the poller, but a fixed fallback
        # keeps this public helper deterministic and free of exception text.
        message = "你提交的视频解析状态暂时不可用，请稍后重试。"
    return message[:max(1, int(max_body_codepoints))]


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise NotificationTransportError("redirect_rejected", retryable=False)


class LangBotOutboundClient:
    """Minimal official LangBot ``send_message`` HTTP client."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        timeout_seconds: float = 10.0,
        opener=None,
    ) -> None:
        _validate_langbot_outbound_url(base_url)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("LangBot outbound timeout must be positive")
        self.base_url = str(base_url).rstrip("/") + "/"
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or build_opener(_NoRedirectHandler())

    def send_message(
        self,
        *,
        bot_uuid: str,
        conversation_id: str,
        text: str,
        timeout_seconds: float | None = None,
    ) -> None:
        if not self.api_key:
            raise NotificationTransportError("outbound_api_key_missing", retryable=False)
        bot = str(bot_uuid).strip()
        conversation = str(conversation_id).strip()
        if not bot or not conversation or not str(text).strip():
            raise NotificationTransportError("invalid_outbound_target", retryable=False)
        endpoint = urljoin(
            self.base_url,
            "api/v1/platform/bots/"
            + quote(bot, safe="")
            + "/send_message",
        )
        payload = {
            "target_type": "person",
            "target_id": conversation,
            "message_chain": {"root": [{"type": "Plain", "text": str(text)}]},
        }
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if not math.isfinite(timeout) or timeout <= 0:
            raise NotificationTransportError("outbound_timeout", retryable=True)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status = int(getattr(response, "status", response.getcode()))
                # Do not retain or parse response text.  A successful HTTP
                # status is the only contract needed by the ledger.
                if 200 <= status < 300:
                    return
                self._raise_for_status(status)
        except NotificationTransportError:
            raise
        except HTTPError as exc:
            self._raise_for_status(int(exc.code))
        except (TimeoutError, socket.timeout):
            raise NotificationTransportError("outbound_timeout", retryable=True) from None
        except URLError as exc:
            # ``reason`` is deliberately not inspected or serialized.
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                code = "outbound_timeout"
            else:
                code = "outbound_connect_failed"
            raise NotificationTransportError(code, retryable=True) from None
        except OSError:
            raise NotificationTransportError("outbound_connect_failed", retryable=True) from None
        except Exception:
            raise NotificationTransportError("outbound_transport_failed", retryable=True) from None

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if 300 <= status < 400:
            raise NotificationTransportError("redirect_rejected", retryable=False)
        if status in {401, 403}:
            raise NotificationTransportError("outbound_auth_rejected", retryable=False)
        if status == 400:
            raise NotificationTransportError("outbound_contract_invalid", retryable=False)
        if status == 404:
            raise NotificationTransportError("outbound_target_not_found", retryable=False)
        if status == 429:
            raise NotificationTransportError("outbound_rate_limited", retryable=True)
        if status >= 500:
            raise NotificationTransportError("outbound_server_error", retryable=True)
        if 400 <= status < 500:
            raise NotificationTransportError("outbound_http_4xx", retryable=False)
        raise NotificationTransportError("outbound_http_status", retryable=True)


@dataclass(frozen=True)
class DeliveryClaim:
    event_id: int
    delivery_id: int
    claim_token: str
    attempts: int


@dataclass(frozen=True)
class NotificationSweepResult:
    claimed: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0
    duration_ms: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "skipped": self.skipped,
            "failed": self.failed,
            "deferred": self.deferred,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class NotificationTarget:
    event_id: int
    outcome: str
    item_state: str
    error_code: str | None
    channel: str
    account_id: str
    conversation_id: str
    title: str | None


def _db_now(db: Session) -> datetime:
    value = db.scalar(select(func.now()))
    if not isinstance(value, datetime):
        raise RuntimeError("notification_db_time_unavailable")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _set_notification_statement_timeout(
    db: Session, remaining_seconds: float | None
) -> None:
    """Bound PostgreSQL claim/ACK SQL by the remaining sweep budget.

    Lightweight unit-session fakes and non-PostgreSQL development databases do
    not need the session setting.  PostgreSQL receives a parameterized value so
    no SQL text is constructed from the configured duration.
    """

    if remaining_seconds is None:
        return
    if remaining_seconds <= 0:
        raise TimeoutError("notification_sweep_deadline")
    bind = getattr(db, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect == "postgresql":
        db.execute(
            text("SELECT set_config('statement_timeout', :timeout_text, true)"),
            {"timeout_text": f"{max(1, int(remaining_seconds * 1000))}ms"},
        )


class IngestNotificationPoller:
    """Bounded claim/send/ACK service used by the maintenance Celery task."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        client: LangBotOutboundClient | None = None,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._client = client
        self._clock = clock
        self._token_factory = token_factory or self._new_token

    @staticmethod
    def _new_token() -> str:
        import secrets

        return secrets.token_hex(24)

    def _settings_or_default(self) -> Settings:
        if self._settings is not None:
            return self._settings
        from app.config import get_settings

        return get_settings()

    def _client_or_default(self, settings: Settings) -> LangBotOutboundClient:
        if self._client is None:
            self._client = LangBotOutboundClient(
                settings.langbot_outbound_base_url,
                settings.langbot_outbound_api_key,
                timeout_seconds=settings.langbot_outbound_timeout_seconds,
            )
        return self._client

    def _claim_batch(
        self,
        *,
        now: datetime,
        settings: Settings,
        budget_seconds: float | None = None,
    ) -> list[DeliveryClaim]:
        claims: list[DeliveryClaim] = []
        stale_before = now - timedelta(
            seconds=settings.ingest_notification_claim_timeout_seconds
        )
        with self._session_factory() as db:
            _set_notification_statement_timeout(db, budget_seconds)
            # PostgreSQL time is the claim/ACK clock; fail closed if it cannot
            # be read instead of using the caller's application clock.
            now = _db_now(db)
            stale_before = now - timedelta(
                seconds=settings.ingest_notification_claim_timeout_seconds
            )
            # The outer join discovers historical events that predate this
            # ledger.  ``publish_state`` is intentionally absent from this
            # predicate: Redis publication is a retired, independent path.
            delivery = IngestCompletionDelivery
            candidate_stmt = (
                select(IngestCompletionEvent)
                .outerjoin(
                    delivery,
                    and_(
                        delivery.event_id == IngestCompletionEvent.id,
                        delivery.handler_key == HANDLER_KEY,
                    ),
                )
                .where(
                    or_(
                        delivery.id.is_(None),
                        and_(
                            delivery.status == "failed",
                            delivery.next_attempt_at.is_not(None),
                            delivery.next_attempt_at <= now,
                        ),
                        and_(
                            delivery.status == "claimed",
                            or_(
                                delivery.claimed_at.is_(None),
                                delivery.claimed_at <= stale_before,
                            ),
                        ),
                    )
                )
                .order_by(IngestCompletionEvent.created_at, IngestCompletionEvent.id)
                .limit(settings.ingest_notification_batch_size)
                # PostgreSQL rejects an unqualified FOR UPDATE on the nullable
                # side of this outer join.  The event row is the serialization
                # root for both a missing delivery insert and an existing-row
                # reclaim, so lock that table explicitly.
                .with_for_update(of=IngestCompletionEvent, skip_locked=True)
            )
            events = list(db.scalars(candidate_stmt))
            for event in events:
                row = db.scalar(
                    select(delivery)
                    .where(
                        delivery.event_id == event.id,
                        delivery.handler_key == HANDLER_KEY,
                    )
                    .with_for_update()
                )
                if row is None:
                    row = delivery(
                        event_id=event.id,
                        handler_key=HANDLER_KEY,
                        status="claimed",
                        claim_token=self._token_factory(),
                        claimed_at=now,
                        attempts=1,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(row)
                    db.flush()
                elif row.status == "succeeded":
                    continue
                elif (
                    row.status == "claimed"
                    and _as_utc(row.claimed_at) is not None
                    and _as_utc(row.claimed_at) > stale_before
                ):
                    continue
                elif row.status == "failed" and (
                    row.next_attempt_at is None
                    or _as_utc(row.next_attempt_at) > now
                ):
                    continue
                else:
                    # Reclaim stale claims or an eligible failed retry.
                    manual_redrive = row.last_error_code == "manual_redrive"
                    deadline_deferred = (
                        row.last_error_code == "notification_deferred"
                    )
                    row.status = "claimed"
                    row.disposition = None
                    row.claim_token = self._token_factory()
                    row.claimed_at = now
                    # A manual re-drive starts a fresh bounded retry window;
                    # otherwise an exhausted row would be claimed and
                    # immediately exhausted again without calling LangBot.
                    if manual_redrive:
                        row.attempts = 1
                    elif deadline_deferred:
                        # This claim never started an outbound attempt, so it
                        # must not consume the transport retry ceiling.
                        row.attempts = max(1, int(row.attempts or 1))
                    else:
                        row.attempts = int(row.attempts or 0) + 1
                    row.next_attempt_at = None
                    row.last_error_code = None
                    row.completed_at = None
                    row.updated_at = now
                if row.attempts > settings.ingest_notification_max_attempts:
                    row.status = "failed"
                    row.disposition = "retry_exhausted"
                    row.last_error_code = "retry_exhausted"
                    row.claim_token = None
                    row.claimed_at = None
                    row.next_attempt_at = None
                    row.completed_at = None
                    row.updated_at = now
                    continue
                claims.append(
                    DeliveryClaim(
                        event_id=int(event.id),
                        delivery_id=int(row.id),
                        claim_token=str(row.claim_token),
                        attempts=int(row.attempts),
                    )
                )
            db.commit()
        return claims

    def _claim_event(
        self,
        event_id: int,
        *,
        now: datetime,
        settings: Settings,
        budget_seconds: float | None = None,
    ) -> DeliveryClaim | None:
        """Claim one event without running the global candidate scan.

        The completion event row is the serialization root for both the
        missing-ledger-row insert and duplicate task delivery.  Keeping this
        transaction event-scoped means a normal terminal hook can enqueue one
        cheap Celery message without making the worker pay for a sweep of
        every historical completion event.
        """

        stale_before = now - timedelta(
            seconds=settings.ingest_notification_claim_timeout_seconds
        )
        with self._session_factory() as db:
            _set_notification_statement_timeout(db, budget_seconds)
            now = _db_now(db)
            stale_before = now - timedelta(
                seconds=settings.ingest_notification_claim_timeout_seconds
            )
            event = db.scalar(
                select(IngestCompletionEvent)
                .where(IngestCompletionEvent.id == int(event_id))
                .with_for_update()
            )
            if event is None:
                return None

            delivery = IngestCompletionDelivery
            row = db.scalar(
                select(delivery)
                .where(
                    delivery.event_id == event.id,
                    delivery.handler_key == HANDLER_KEY,
                )
                .with_for_update()
            )
            if row is None:
                row = delivery(
                    event_id=event.id,
                    handler_key=HANDLER_KEY,
                    status="claimed",
                    claim_token=self._token_factory(),
                    claimed_at=now,
                    attempts=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                db.flush()
            elif row.status == "succeeded":
                return None
            elif (
                row.status == "claimed"
                and _as_utc(row.claimed_at) is not None
                and _as_utc(row.claimed_at) > stale_before
            ):
                return None
            elif row.status == "failed" and (
                row.next_attempt_at is None
                or _as_utc(row.next_attempt_at) > now
            ):
                return None
            else:
                manual_redrive = row.last_error_code == "manual_redrive"
                deadline_deferred = row.last_error_code == "notification_deferred"
                row.status = "claimed"
                row.disposition = None
                row.claim_token = self._token_factory()
                row.claimed_at = now
                if manual_redrive:
                    row.attempts = 1
                elif deadline_deferred:
                    row.attempts = max(1, int(row.attempts or 1))
                else:
                    row.attempts = int(row.attempts or 0) + 1
                row.next_attempt_at = None
                row.last_error_code = None
                row.completed_at = None
                row.updated_at = now

            if row.attempts > settings.ingest_notification_max_attempts:
                row.status = "failed"
                row.disposition = "retry_exhausted"
                row.last_error_code = "retry_exhausted"
                row.claim_token = None
                row.claimed_at = None
                row.next_attempt_at = None
                row.completed_at = None
                row.updated_at = now
                db.commit()
                return None

            claim = DeliveryClaim(
                event_id=int(event.id),
                delivery_id=int(row.id),
                claim_token=str(row.claim_token),
                attempts=int(row.attempts),
            )
            db.commit()
            return claim

    def _load_target(
        self,
        db: Session,
        event_id: int,
        *,
        budget_seconds: float | None = None,
    ) -> tuple[NotificationTarget | None, str]:
        _set_notification_statement_timeout(db, budget_seconds)
        event = db.get(IngestCompletionEvent, event_id)
        if event is None:
            return None, "skipped_deleted"
        if event.outcome not in {"completed", "failed"}:
            return None, "terminal_failure"
        dispatch = db.get(IngestDispatch, event.dispatch_id)
        item = db.get(ContentItem, event.item_id)
        if dispatch is None or item is None:
            return None, "skipped_deleted"
        if dispatch.item_id != event.item_id:
            return None, "skipped_target_mismatch"
        if item.deleted_at is not None or item.purge_claimed_at is not None:
            return None, "skipped_deleted"
        source_thread_id = dispatch.source_thread_id
        if source_thread_id is None:
            return None, "skipped_no_channel"
        thread = db.get(ConversationThread, source_thread_id)
        if thread is None or thread.channel not in SUPPORTED_SOURCE_CHANNELS:
            return None, "skipped_no_channel"
        identity = db.get(ChannelIdentity, thread.channel_identity_id)
        owner = db.get(AppUser, thread.app_user_id)
        if identity is None or identity.disabled_at is not None:
            return None, "skipped_identity_disabled"
        if owner is None or owner.disabled_at is not None:
            return None, "skipped_owner_disabled"
        if (
            thread.app_user_id != item.user_id
            or identity.app_user_id != thread.app_user_id
            or identity.channel != thread.channel
            or identity.account_id != thread.account_id
            or thread.channel != identity.channel
            or not thread.account_id
            or not thread.external_conversation_id
        ):
            return None, "skipped_target_mismatch"
        if event.item_state not in {"ready", "needs_extension", "needs_asr", "failed"}:
            return None, "terminal_failure"
        if event.outcome == "completed" and event.item_state == "failed":
            return None, "terminal_failure"
        if event.outcome == "failed" and event.item_state != "failed":
            return None, "terminal_failure"
        return (
            NotificationTarget(
                event_id=event.id,
                outcome=event.outcome,
                item_state=event.item_state,
                error_code=event.error_code,
                channel=thread.channel,
                account_id=thread.account_id,
                conversation_id=thread.external_conversation_id,
                title=item.title,
            ),
            "",
        )

    def _oldest_eligible_backlog_age_seconds(
        self,
        *,
        settings: Settings,
        budget_seconds: float,
    ) -> int | None:
        """Read the oldest eligible event age without changing delivery state.

        This is deliberately a separate, unlocked observation query.  It uses
        the same eligibility predicate as claiming and a PostgreSQL statement
        timeout derived from the remaining sweep budget.  A failure is handled
        by the caller as an observability miss, never as a delivery failure.
        """

        with self._session_factory() as db:
            _set_notification_statement_timeout(db, budget_seconds)
            now = _db_now(db)
            stale_before = now - timedelta(
                seconds=settings.ingest_notification_claim_timeout_seconds
            )
            delivery = IngestCompletionDelivery
            oldest_created = db.scalar(
                select(func.min(IngestCompletionEvent.created_at))
                .outerjoin(
                    delivery,
                    and_(
                        delivery.event_id == IngestCompletionEvent.id,
                        delivery.handler_key == HANDLER_KEY,
                    ),
                )
                .where(
                    or_(
                        delivery.id.is_(None),
                        and_(
                            delivery.status == "failed",
                            delivery.next_attempt_at.is_not(None),
                            delivery.next_attempt_at <= now,
                        ),
                        and_(
                            delivery.status == "claimed",
                            or_(
                                delivery.claimed_at.is_(None),
                                delivery.claimed_at <= stale_before,
                            ),
                        ),
                    )
                )
            )
            if oldest_created is None:
                return 0
            created = _as_utc(oldest_created)
            if created is None:
                return None
            age_seconds = (now - created).total_seconds()
            if not math.isfinite(age_seconds):
                return None
            return min(
                _MAX_DIAGNOSTIC_INTEGER,
                max(1, math.ceil(age_seconds)),
            )

    def _ack_succeeded(
        self,
        claim: DeliveryClaim,
        *,
        disposition: str,
        now: datetime,
        budget_seconds: float | None = None,
    ) -> bool:
        if disposition not in SAFE_SUCCESS_DISPOSITIONS:
            raise ValueError("invalid_notification_success_disposition")
        with self._session_factory() as db:
            _set_notification_statement_timeout(db, budget_seconds)
            now = _db_now(db)
            row = db.scalar(
                select(IngestCompletionDelivery)
                .where(
                    IngestCompletionDelivery.id == claim.delivery_id,
                    IngestCompletionDelivery.event_id == claim.event_id,
                    IngestCompletionDelivery.handler_key == HANDLER_KEY,
                    IngestCompletionDelivery.status == "claimed",
                    IngestCompletionDelivery.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
            if row is None:
                return False
            row.status = "succeeded"
            row.disposition = disposition
            row.claim_token = None
            row.claimed_at = None
            row.next_attempt_at = None
            row.last_error_code = None
            row.completed_at = now
            row.updated_at = now
            db.commit()
            return True

    def _ack_failure(
        self,
        claim: DeliveryClaim,
        *,
        error_code: str,
        settings: Settings,
        now: datetime,
        budget_seconds: float | None = None,
        terminal: bool = False,
    ) -> tuple[bool, bool]:
        """Return (acknowledged, exhausted)."""

        with self._session_factory() as db:
            _set_notification_statement_timeout(db, budget_seconds)
            now = _db_now(db)
            row = db.scalar(
                select(IngestCompletionDelivery)
                .where(
                    IngestCompletionDelivery.id == claim.delivery_id,
                    IngestCompletionDelivery.event_id == claim.event_id,
                    IngestCompletionDelivery.handler_key == HANDLER_KEY,
                    IngestCompletionDelivery.status == "claimed",
                    IngestCompletionDelivery.claim_token == claim.claim_token,
                )
                .with_for_update()
            )
            if row is None:
                return False, False
            row.claim_token = None
            row.claimed_at = None
            row.completed_at = None
            row.updated_at = now
            row.last_error_code = (
                error_code
                if error_code in SAFE_ERROR_CODES
                else "notification_internal_failure"
            )
            if terminal:
                row.status = "failed"
                row.disposition = "terminal_failure"
                row.next_attempt_at = None
                db.commit()
                return True, False
            if row.attempts >= settings.ingest_notification_max_attempts:
                row.status = "failed"
                row.disposition = "retry_exhausted"
                # Keep the terminal disposition and stable error code in
                # sync.  This is part of the failed-ledger state contract and
                # lets operators distinguish exhausted retries from a
                # retryable transport failure without inspecting history.
                row.last_error_code = "retry_exhausted"
                row.next_attempt_at = None
                db.commit()
                return True, True
            delay = min(
                settings.ingest_notification_retry_base_seconds
                * (2 ** max(0, int(row.attempts) - 1)),
                settings.ingest_notification_retry_max_seconds,
            )
            row.status = "failed"
            row.disposition = None
            row.next_attempt_at = now + timedelta(seconds=delay)
            db.commit()
            return True, False

    def _release_deferred_claims(
        self,
        claims: Iterable[DeliveryClaim],
        *,
        budget_seconds: float | None = None,
    ) -> int:
        """Return claims that have not started outbound I/O to the next tick."""

        pending = tuple(claims)
        if not pending:
            return 0
        token_predicates = tuple(
            and_(
                IngestCompletionDelivery.id == claim.delivery_id,
                IngestCompletionDelivery.event_id == claim.event_id,
                IngestCompletionDelivery.claim_token == claim.claim_token,
            )
            for claim in pending
        )
        with self._session_factory() as db:
            _set_notification_statement_timeout(db, budget_seconds)
            now = _db_now(db)
            rows = list(
                db.scalars(
                    select(IngestCompletionDelivery)
                    .where(
                        IngestCompletionDelivery.handler_key == HANDLER_KEY,
                        IngestCompletionDelivery.status == "claimed",
                        or_(*token_predicates),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = "failed"
                row.disposition = None
                row.claim_token = None
                row.claimed_at = None
                row.next_attempt_at = now
                row.last_error_code = "notification_deferred"
                row.completed_at = None
                row.updated_at = now
            db.commit()
            return len(rows)

    def _deliver_claim(
        self,
        claim: DeliveryClaim,
        *,
        settings: Settings,
        client: LangBotOutboundClient,
        deadline: float,
        work_deadline: float,
    ) -> tuple[int, int, int, int, bool]:
        """Deliver one claimed event and return safe counters.

        The final boolean indicates that no outbound request was started and
        the claim can be released immediately when the caller still has SQL
        budget.  ACKs remain token-fenced through the same methods used by the
        repair sweep.
        """

        succeeded = skipped = failed = deferred = 0
        unsent = False
        try:
            target_budget = work_deadline - self._clock()
            if target_budget <= 0:
                return succeeded, skipped, failed, 1, True
            with self._session_factory() as db:
                target, terminal = self._load_target(
                    db, claim.event_id, budget_seconds=target_budget
                )
            if target is None:
                ack_budget = deadline - self._clock()
                if ack_budget <= 0:
                    return succeeded, skipped, failed, 1, False
                if terminal == "terminal_failure":
                    acknowledged, _ = self._ack_failure(
                        claim,
                        error_code="notification_internal_failure",
                        settings=settings,
                        now=datetime.now(UTC),
                        budget_seconds=ack_budget,
                        terminal=True,
                    )
                    if acknowledged:
                        failed += 1
                elif self._ack_succeeded(
                    claim,
                    disposition=terminal,
                    now=datetime.now(UTC),
                    budget_seconds=ack_budget,
                ):
                    succeeded += 1
                    skipped += 1
                return succeeded, skipped, failed, deferred, unsent

            body = render_completion_notification(
                target.outcome,
                target.item_state,
                target.title,
            )
            outbound_budget = work_deadline - self._clock()
            if outbound_budget <= 0:
                return succeeded, skipped, failed, 1, True
            client.send_message(
                bot_uuid=target.account_id,
                conversation_id=target.conversation_id,
                text=body,
                timeout_seconds=min(
                    settings.langbot_outbound_timeout_seconds,
                    outbound_budget,
                ),
            )
            ack_budget = deadline - self._clock()
            if ack_budget <= 0:
                return succeeded, skipped, failed, 1, False
            if self._ack_succeeded(
                claim,
                disposition="sent",
                now=datetime.now(UTC),
                budget_seconds=ack_budget,
            ):
                succeeded += 1
        except NotificationTransportError as exc:
            try:
                ack_budget = deadline - self._clock()
                if ack_budget <= 0:
                    return succeeded, skipped, failed, 1, False
                if exc.retryable:
                    acknowledged, exhausted = self._ack_failure(
                        claim,
                        error_code=exc.error_code,
                        settings=settings,
                        now=datetime.now(UTC),
                        budget_seconds=ack_budget,
                    )
                    if acknowledged:
                        failed += 1
                        if exhausted:
                            skipped += 1
                else:
                    acknowledged, _ = self._ack_failure(
                        claim,
                        error_code=exc.error_code,
                        settings=settings,
                        now=datetime.now(UTC),
                        budget_seconds=ack_budget,
                        terminal=True,
                    )
                    if acknowledged:
                        failed += 1
            except Exception:
                # A database outage while ACKing one transport failure must
                # not abort a batch or turn a targeted task into a global
                # retry. The stale claim is the crash-safe recovery path.
                deferred += 1
        except Exception:
            # Keep event failures isolated and diagnostics privacy-safe.
            try:
                ack_budget = deadline - self._clock()
                if ack_budget <= 0:
                    return succeeded, skipped, failed, 1, False
                acknowledged, exhausted = self._ack_failure(
                    claim,
                    error_code="notification_internal_failure",
                    settings=settings,
                    now=datetime.now(UTC),
                    budget_seconds=ack_budget,
                )
                if acknowledged:
                    failed += 1
                    if exhausted:
                        skipped += 1
            except Exception:
                deferred += 1
        return succeeded, skipped, failed, deferred, unsent

    def deliver_event(self, event_id: int) -> NotificationSweepResult:
        """Deliver one completion event without querying other events."""

        settings = self._settings_or_default()
        started = self._clock()
        deadline = started + settings.ingest_notification_max_duration_seconds
        release_reserve = min(
            1.0, settings.ingest_notification_max_duration_seconds * 0.25
        )
        work_deadline = deadline - release_reserve
        try:
            client = self._client_or_default(settings)
            claim_budget = work_deadline - self._clock()
            if claim_budget <= 0:
                return NotificationSweepResult(deferred=1, duration_ms=0)
            claim = self._claim_event(
                int(event_id),
                now=datetime.now(UTC),
                settings=settings,
                budget_seconds=claim_budget,
            )
        except Exception:
            duration = max(0, int((self._clock() - started) * 1000))
            _notification_diagnostic(
                "notification_event_delivery", failed=1, duration_ms=duration
            )
            return NotificationSweepResult(failed=1, duration_ms=duration)

        if claim is None:
            duration = max(0, int((self._clock() - started) * 1000))
            return NotificationSweepResult(duration_ms=duration)

        succeeded, skipped, failed, deferred, unsent = self._deliver_claim(
            claim,
            settings=settings,
            client=client,
            deadline=deadline,
            work_deadline=work_deadline,
        )
        if unsent:
            release_budget = deadline - self._clock()
            if release_budget > 0:
                try:
                    self._release_deferred_claims(
                        (claim,), budget_seconds=release_budget
                    )
                except Exception:
                    # The stale-claim path remains the crash-safe fallback.
                    pass
        duration = max(0, int((self._clock() - started) * 1000))
        result = NotificationSweepResult(
            claimed=1,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            deferred=deferred,
            duration_ms=duration,
        )
        _notification_diagnostic(
            "notification_event_delivery",
            claimed=result.claimed,
            succeeded=result.succeeded,
            skipped=result.skipped,
            failed=result.failed,
            deferred=result.deferred,
            duration_ms=result.duration_ms,
        )
        return result

    def _emit_successful_tick_heartbeat(
        self,
        *,
        settings: Settings,
        deadline: float,
        observation_budget_seconds: float,
        result: NotificationSweepResult,
    ) -> None:
        """Emit one bounded heartbeat after a completed poller tick.

        Backlog observation is best effort and runs after delivery work.  A
        timeout, database outage, or malformed observation result only marks
        ``observability_failed``; it never changes the already-computed
        delivery counters or row state.
        """

        remaining = min(
            observation_budget_seconds,
            deadline - self._clock(),
        )
        backlog_age: int | None = None
        observability_failed = 0
        if remaining > 0:
            try:
                backlog_age = self._oldest_eligible_backlog_age_seconds(
                    settings=settings,
                    budget_seconds=remaining,
                )
            except Exception:
                observability_failed = 1
        else:
            observability_failed = 1
        values: dict[str, int] = {
            "heartbeat": 1,
            "claimed": result.claimed,
            "succeeded": result.succeeded,
            "skipped": result.skipped,
            "failed": result.failed,
            "deferred": result.deferred,
            "duration_ms": result.duration_ms,
            "observability_failed": observability_failed,
        }
        if backlog_age is not None:
            values["oldest_eligible_backlog_age_seconds"] = backlog_age
        _notification_diagnostic("notification_poller_heartbeat", **values)

    def sweep_once(self) -> NotificationSweepResult:
        settings = self._settings_or_default()
        started = self._clock()
        deadline = started + settings.ingest_notification_max_duration_seconds
        # Keep a small part of the configured whole-sweep deadline for the
        # final token-fenced ACK or for releasing claims whose HTTP has not
        # started.  Without this reserve, one slow peer can leave the rest of
        # a claimed batch invisible until the much longer stale timeout.
        release_reserve = min(
            1.0, settings.ingest_notification_max_duration_seconds * 0.25
        )
        observability_reserve = min(
            0.25, settings.ingest_notification_max_duration_seconds * 0.10
        )
        work_deadline = deadline - release_reserve - observability_reserve
        claimed = succeeded = skipped = failed = deferred = 0

        def finish(result: NotificationSweepResult) -> NotificationSweepResult:
            self._emit_successful_tick_heartbeat(
                settings=settings,
                deadline=deadline,
                observation_budget_seconds=observability_reserve,
                result=result,
            )
            return result

        try:
            # Validate/build the transport before claiming any durable work.
            # A local client-construction failure must not strand a full batch
            # behind the stale-claim timeout.
            client = self._client_or_default(settings)
            now = datetime.now(UTC)
            claim_budget = work_deadline - self._clock()
            if claim_budget <= 0:
                return finish(NotificationSweepResult(deferred=1, duration_ms=0))
            claims = self._claim_batch(
                now=now,
                settings=settings,
                budget_seconds=claim_budget,
            )
        except Exception:
            duration = max(0, int((self._clock() - started) * 1000))
            _notification_diagnostic("notification_sweep", failed=1, duration_ms=duration)
            return NotificationSweepResult(failed=1, duration_ms=duration)
        claimed = len(claims)

        def defer_unsent(start_index: int) -> None:
            nonlocal deferred
            unsent = claims[start_index:]
            deferred += len(unsent)
            release_budget = deadline - observability_reserve - self._clock()
            if not unsent or release_budget <= 0:
                return
            try:
                self._release_deferred_claims(
                    unsent,
                    budget_seconds=release_budget,
                )
            except Exception:
                # The stale-claim path remains the crash-safe fallback.
                return

        for index, claim in enumerate(claims):
            remaining = work_deadline - self._clock()
            if remaining <= 0:
                defer_unsent(index)
                break
            try:
                target_budget = work_deadline - self._clock()
                if target_budget <= 0:
                    defer_unsent(index)
                    break
                with self._session_factory() as db:
                    target, terminal = self._load_target(
                        db, claim.event_id, budget_seconds=target_budget
                    )
                if target is None:
                    ack_budget = deadline - self._clock()
                    if ack_budget <= 0:
                        deferred += 1
                        continue
                    if terminal == "terminal_failure":
                        acknowledged, _ = self._ack_failure(
                            claim,
                            error_code="notification_internal_failure",
                            settings=settings,
                            now=datetime.now(UTC),
                            budget_seconds=ack_budget,
                            terminal=True,
                        )
                        if acknowledged:
                            failed += 1
                    elif self._ack_succeeded(
                        claim,
                        disposition=terminal,
                        now=datetime.now(UTC),
                        budget_seconds=ack_budget,
                    ):
                        succeeded += 1
                        skipped += 1
                    continue
                body = render_completion_notification(
                    target.outcome,
                    target.item_state,
                    target.title,
                )
                outbound_budget = work_deadline - self._clock()
                if outbound_budget <= 0:
                    defer_unsent(index)
                    break
                client.send_message(
                    bot_uuid=target.account_id,
                    conversation_id=target.conversation_id,
                    text=body,
                    timeout_seconds=min(
                        settings.langbot_outbound_timeout_seconds,
                        outbound_budget,
                    ),
                )
                ack_budget = deadline - self._clock()
                if ack_budget <= 0:
                    deferred += 1
                    continue
                if self._ack_succeeded(
                    claim,
                    disposition="sent",
                    now=datetime.now(UTC),
                    budget_seconds=ack_budget,
                ):
                    succeeded += 1
            except NotificationTransportError as exc:
                try:
                    ack_budget = deadline - self._clock()
                    if ack_budget <= 0:
                        deferred += 1
                        continue
                    if exc.retryable:
                        acknowledged, exhausted = self._ack_failure(
                            claim,
                            error_code=exc.error_code,
                            settings=settings,
                            now=datetime.now(UTC),
                            budget_seconds=ack_budget,
                        )
                        if acknowledged:
                            failed += 1
                            if exhausted:
                                skipped += 1
                    else:
                        acknowledged, _ = self._ack_failure(
                            claim,
                            error_code=exc.error_code,
                            settings=settings,
                            now=datetime.now(UTC),
                            budget_seconds=ack_budget,
                            terminal=True,
                        )
                        if acknowledged:
                            failed += 1
                except Exception:
                    # Keep peer events independent if the ACK database is
                    # unavailable after the outbound classification.
                    deferred += 1
            except Exception:
                # Keep peer events independent and diagnostics privacy-safe.
                try:
                    ack_budget = deadline - self._clock()
                    if ack_budget <= 0:
                        deferred += 1
                        continue
                    acknowledged, exhausted = self._ack_failure(
                        claim,
                        error_code="notification_internal_failure",
                        settings=settings,
                        now=datetime.now(UTC),
                        budget_seconds=ack_budget,
                    )
                    if acknowledged:
                        failed += 1
                        if exhausted:
                            skipped += 1
                except Exception:
                    deferred += 1
        duration = max(0, int((self._clock() - started) * 1000))
        _notification_diagnostic(
            "notification_sweep",
            claimed=claimed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            deferred=deferred,
            duration_ms=duration,
        )
        return finish(NotificationSweepResult(
            claimed=claimed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            deferred=deferred,
            duration_ms=duration,
        ))

    def redrive_failed(self, event_id: int) -> bool:
        """Make one failed ledger row eligible after an operator fix."""

        with self._session_factory() as db:
            row = db.scalar(
                select(IngestCompletionDelivery)
                .where(
                    IngestCompletionDelivery.event_id == int(event_id),
                    IngestCompletionDelivery.handler_key == HANDLER_KEY,
                    IngestCompletionDelivery.status == "failed",
                )
                .with_for_update()
            )
            if row is None:
                return False
            now = _db_now(db)
            row.next_attempt_at = now
            row.disposition = None
            row.attempts = 1
            # Keep the failed-row state contract valid while making the row
            # immediately eligible for a fresh claim.
            row.last_error_code = "manual_redrive"
            row.updated_at = now
            db.commit()
            return True


def _notification_diagnostic(event: str, **values: Any) -> None:
    safe_event = event if event in _NOTIFICATION_DIAGNOSTIC_EVENTS else "notification_sweep"
    payload = {"event": safe_event}
    for key, value in values.items():
        if key not in _NOTIFICATION_DIAGNOSTIC_FIELDS:
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            payload[key] = min(_MAX_DIAGNOSTIC_INTEGER, max(0, value))
    try:
        _LOGGER.info("diagnostic", extra={"diagnostic_payload": payload})
    except Exception:
        return


def redrive_failed_ingest_notification(
    event_id: int, *, session_factory=None
) -> bool:
    """Convenience operator hook using the configured database factory."""

    if session_factory is None:
        from app.db import get_session_factory

        session_factory = get_session_factory()
    return IngestNotificationPoller(session_factory).redrive_failed(event_id)
