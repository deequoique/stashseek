"""Deterministic self-registration and cross-channel identity linking."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, inspect, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.channels.errors import (
    DisabledIdentity,
    ExpiredLinkToken,
    IdentityConflict,
    InvalidLinkToken,
    LinkMergeBusy,
    UnboundIdentity,
    UsedLinkToken,
    WrongChannelLinkToken,
)
from app.channels.types import ChannelEnvelope, TenantContext
from app.limits import normalize_why_saved
from app.models import (
    AppUser,
    ChannelIdentity,
    ChannelLinkToken,
    ContentItem,
    ConversationThread,
    IngestDispatch,
    McpAccessGrant,
    Segment,
    WebSession,
)

LINKABLE_CHANNELS = frozenset({"telegram", "wechat", "web"})
LINK_TOKEN_TTL = timedelta(minutes=10)
_LINK_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_CHANNEL_LIKE_RE = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z")
_ACTIVE_DISPATCH_STATES = frozenset({"pending", "enqueued", "running"})
_CONTENT_FILL_FIELDS = (
    "title",
    "author",
    "published_at",
    "duration_sec",
    "char_count",
    "lang",
    "description",
    "tags",
    "chapters",
    "cover_url",
    "content_hash",
    "raw_object_key",
)


def classify_link_argument(argument: str) -> tuple[Literal["channel", "token"], str]:
    """Classify `/link` input without consulting identity or model state."""

    value = str(argument).strip()
    normalized_channel = value.lower()
    if normalized_channel in LINKABLE_CHANNELS:
        return "channel", normalized_channel
    if _LINK_TOKEN_RE.fullmatch(value):
        return "token", value
    if _CHANNEL_LIKE_RE.fullmatch(normalized_channel):
        raise IdentityConflict("link channel is unsupported")
    raise InvalidLinkToken("link token has an invalid format")


def _identity_query(envelope: ChannelEnvelope):
    return select(ChannelIdentity).where(
        ChannelIdentity.channel == envelope.channel,
        ChannelIdentity.account_id == envelope.account_id,
        ChannelIdentity.external_user_id == envelope.external_user_id,
    )


def _tenant(db: Session, identity: ChannelIdentity) -> TenantContext:
    user = db.get(AppUser, identity.app_user_id)
    if user is None or user.disabled_at is not None or identity.disabled_at is not None:
        raise DisabledIdentity("account is disabled")
    return TenantContext(
        app_user_id=user.id,
        channel_identity_id=identity.id,
        channel=identity.channel,
        account_id=identity.account_id,
        external_user_id=identity.external_user_id,
    )


def resolve_identity(db: Session, envelope: ChannelEnvelope) -> TenantContext:
    identity = db.scalar(_identity_query(envelope))
    if identity is None:
        raise UnboundIdentity("channel identity is not registered")
    return _tenant(db, identity)


def ensure_explicit_identity(
    db: Session,
    *,
    app_user_id: int,
    channel: str,
    account_id: str,
    external_user_id: str,
) -> TenantContext:
    """Bind an application-owned identity to an existing user.

    Operator-created principals (MCP grants and the CLI) must never trigger
    self-registration.  This helper is intentionally transaction-scoped: the
    caller owns commit/rollback and can create a grant and its identity as one
    atomic unit.  Rebinding an existing external identity to another tenant is
    rejected rather than silently changing authorization state.
    """

    try:
        numeric_user_id = int(app_user_id)
    except (TypeError, ValueError):
        raise UnboundIdentity("internal user does not exist") from None
    if isinstance(app_user_id, bool) or numeric_user_id <= 0:
        raise UnboundIdentity("internal user does not exist")
    envelope = ChannelEnvelope(
        channel=channel,
        account_id=account_id,
        external_user_id=external_user_id,
        conversation_id="identity-bootstrap",
        message_id="identity-bootstrap",
        text="",
    )
    user = db.get(AppUser, numeric_user_id)
    if user is None:
        raise UnboundIdentity("internal user does not exist")
    if user.disabled_at is not None:
        raise DisabledIdentity("account is disabled")
    identity = db.scalar(_identity_query(envelope))
    if identity is not None:
        if identity.app_user_id != user.id:
            raise IdentityConflict("identity is already bound to another user")
        return _tenant(db, identity)
    identity = ChannelIdentity(
        app_user_id=user.id,
        channel=envelope.channel,
        account_id=envelope.account_id,
        external_user_id=envelope.external_user_id,
    )
    # SQLite's INTEGER-only autoincrement rule does not apply to the
    # production BigInteger primary key.  Small offline/unit fixtures often
    # use SQLite, so provide a deterministic local id only for that dialect;
    # PostgreSQL continues to use its normal sequence.
    if getattr(getattr(db, "bind", None), "dialect", None) is not None and db.bind.dialect.name == "sqlite":
        identity.id = int(db.scalar(select(func.max(ChannelIdentity.id))) or 0) + 1
    db.add(identity)
    try:
        # A savepoint keeps the caller's transaction (including a just-added
        # grant) intact when a concurrent insert wins the unique race.
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # A concurrent operator may have won the unique identity race.  The
        # session remains usable after rollback, then we resolve and validate
        # that winner without ever accepting a cross-tenant rebind.
        existing = db.scalar(_identity_query(envelope))
        if existing is None:
            raise IdentityConflict("identity registration raced") from None
        if existing.app_user_id != user.id:
            raise IdentityConflict("identity is already bound to another user") from None
        return _tenant(db, existing)
    return _tenant(db, identity)


# Friendly aliases used by operator tooling and adapters.
ensure_identity_for_user = ensure_explicit_identity
bind_explicit_identity = ensure_explicit_identity


def resolve_or_register(db: Session, envelope: ChannelEnvelope) -> TenantContext:
    """Atomically return the existing identity or create one private tenant."""

    identity = db.scalar(_identity_query(envelope))
    if identity is not None:
        return _tenant(db, identity)

    user = AppUser()
    db.add(user)
    db.flush()
    identity_id = db.scalar(
        insert(ChannelIdentity)
        .values(
            app_user_id=user.id,
            channel=envelope.channel,
            account_id=envelope.account_id,
            external_user_id=envelope.external_user_id,
        )
        .on_conflict_do_nothing(
            index_elements=["channel", "account_id", "external_user_id"]
        )
        .returning(ChannelIdentity.id)
    )
    if identity_id is None:
        db.delete(user)
        db.flush()
        identity = db.scalar(_identity_query(envelope))
        if identity is None:
            raise IdentityConflict("identity registration raced but no owner exists")
    else:
        identity = db.get(ChannelIdentity, identity_id)
    return _tenant(db, identity)


def create_link_token(
    db: Session,
    tenant: TenantContext,
    *,
    target_channel: str | None = None,
    ttl: timedelta = LINK_TOKEN_TTL,
    now: datetime | None = None,
) -> str:
    if tenant.channel not in LINKABLE_CHANNELS:
        raise IdentityConflict("source channel is unsupported")
    target = target_channel.strip().lower() if target_channel else None
    if target is not None and target == tenant.channel:
        raise IdentityConflict("target channel must be different")
    identity = db.get(ChannelIdentity, tenant.channel_identity_id)
    if identity is None or identity.app_user_id != tenant.app_user_id:
        raise UnboundIdentity("channel identity is not registered")
    _tenant(db, identity)
    raw = secrets.token_urlsafe(32)
    token = ChannelLinkToken(
        token_hash=_token_hash(raw),
        app_user_id=tenant.app_user_id,
        target_channel=target,
        expires_at=(now or datetime.now(UTC)) + ttl,
    )
    db.add(token)
    db.flush()
    return raw


def consume_link_token(
    db: Session,
    envelope: ChannelEnvelope,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> TenantContext:
    try:
        with db.begin_nested():
            return _consume_and_merge(db, envelope, raw_token, now=now)
    except IntegrityError as exc:
        raise IdentityConflict("identity merge conflicted; retry") from exc


def _consume_and_merge(
    db: Session,
    envelope: ChannelEnvelope,
    raw_token: str,
    *,
    now: datetime | None,
) -> TenantContext:
    token = db.scalar(
        select(ChannelLinkToken)
        .where(ChannelLinkToken.token_hash == _token_hash(raw_token))
        .with_for_update()
    )
    current = now or datetime.now(UTC)
    if token is None:
        raise InvalidLinkToken("link token is invalid")
    if token.consumed_at is not None:
        raise UsedLinkToken("link token has already been used")
    if token.expires_at <= current:
        raise ExpiredLinkToken("link token has expired")
    if token.target_channel and token.target_channel != envelope.channel:
        raise WrongChannelLinkToken("link token is for a different channel")
    if envelope.channel not in LINKABLE_CHANNELS:
        raise WrongChannelLinkToken("presenting channel is unsupported")

    presenting = db.scalar(_identity_query(envelope).with_for_update())
    if presenting is None:
        target_tenant = resolve_or_register(db, envelope)
        presenting = db.get(ChannelIdentity, target_tenant.channel_identity_id)
    assert presenting is not None

    user_ids = sorted({token.app_user_id, presenting.app_user_id})
    users = list(
        db.scalars(
            select(AppUser)
            .where(AppUser.id.in_(user_ids))
            .order_by(AppUser.id)
            .with_for_update()
        )
    )
    if len(users) != len(user_ids) or any(
        user.disabled_at is not None for user in users
    ):
        raise DisabledIdentity("source or target account is disabled")
    db.refresh(presenting)
    if presenting.disabled_at is not None:
        raise DisabledIdentity("target channel identity is disabled")
    source_user_id = token.app_user_id
    target_user_id = presenting.app_user_id
    if target_user_id == source_user_id:
        token.consumed_at = current
        db.flush()
        return _tenant(db, presenting)

    _merge_tenants(db, source_user_id, target_user_id)
    db.refresh(presenting)
    token.consumed_at = current
    db.flush()
    return _tenant(db, presenting)


def _merge_tenants(db: Session, source_user_id: int, target_user_id: int) -> None:
    """Merge the target tenant into the token-creating source tenant."""

    # Lock every directly-owned row in deterministic order before changing
    # ownership. Threads/actions retain their channel-local history.
    db.scalars(
        select(ChannelIdentity)
        .where(ChannelIdentity.app_user_id.in_([source_user_id, target_user_id]))
        .order_by(ChannelIdentity.id)
        .with_for_update()
    ).all()
    db.scalars(
        select(ConversationThread)
        .where(ConversationThread.app_user_id.in_([source_user_id, target_user_id]))
        .order_by(ConversationThread.id)
        .with_for_update()
    ).all()
    db.scalars(
        select(ChannelLinkToken)
        .where(ChannelLinkToken.app_user_id.in_([source_user_id, target_user_id]))
        .order_by(ChannelLinkToken.id)
        .with_for_update()
    ).all()
    # Some legacy/unit fixtures intentionally create only the pre-Web schema.
    # Production reaches this path only after the head migration, where both
    # tables are present and therefore locked/revoked transactionally.
    bind = db.get_bind()
    has_web_sessions = inspect(bind).has_table("web_session")
    has_mcp_grants = inspect(bind).has_table("mcp_access_grant")
    # Credentials belonging to the tenant being absorbed must never become
    # live source credentials.  Keep revoked rows as audit history under the
    # surviving user before deleting the target root.
    if has_web_sessions:
        db.scalars(
            select(WebSession)
            .where(WebSession.app_user_id.in_([source_user_id, target_user_id]))
            .order_by(WebSession.id)
            .with_for_update()
        ).all()
    if has_mcp_grants:
        db.scalars(
            select(McpAccessGrant)
            .where(McpAccessGrant.app_user_id.in_([source_user_id, target_user_id]))
            .order_by(McpAccessGrant.id)
            .with_for_update()
        ).all()
    items = list(
        db.scalars(
            select(ContentItem)
            .where(ContentItem.user_id.in_([source_user_id, target_user_id]))
            .order_by(ContentItem.id)
            .with_for_update()
        )
    )
    item_ids = [item.id for item in items]
    dispatches = (
        list(
            db.scalars(
                select(IngestDispatch)
                .where(IngestDispatch.item_id.in_(item_ids))
                .order_by(IngestDispatch.item_id, IngestDispatch.id)
                .with_for_update()
            )
        )
        if item_ids
        else []
    )
    dispatches_by_item: dict[int, list[IngestDispatch]] = {}
    for dispatch in dispatches:
        dispatches_by_item.setdefault(dispatch.item_id, []).append(dispatch)
    if any(
        item.user_id == target_user_id
        and dispatch.state == "running"
        for item in items
        for dispatch in dispatches_by_item.get(item.id, ())
    ):
        raise LinkMergeBusy("target ingestion is running; retry later")

    groups: dict[tuple[str, str], list[ContentItem]] = {}
    for item in items:
        groups.setdefault((item.platform, item.platform_id), []).append(item)
    retired_item_ids: set[int] = set()
    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        survivor = max(
            duplicates,
            key=lambda item: _survivor_rank(
                db,
                item,
                dispatches_by_item.get(item.id, []),
                source_user_id,
            ),
        )
        for loser in sorted(
            (item for item in duplicates if item.id != survivor.id),
            key=lambda item: item.id,
        ):
            loser_dispatches = dispatches_by_item.get(loser.id, [])
            if any(dispatch.state == "running" for dispatch in loser_dispatches):
                raise LinkMergeBusy("duplicate ingestion is running; retry later")
            _reconcile_content(survivor, loser, source_user_id, target_user_id)
            for dispatch in loser_dispatches:
                if dispatch.state in {"pending", "enqueued"}:
                    dispatch.state = "failed"
                    dispatch.error_code = "merged_duplicate"
            db.flush()
            db.delete(loser)
            db.flush()
            retired_item_ids.add(loser.id)

    # The duplicate loop has already deleted losers; do not issue owner
    # updates for those retired rows.
    for item in items:
        if item.id not in retired_item_ids and item.user_id == target_user_id:
            item.user_id = source_user_id
    db.flush()
    db.execute(
        update(ConversationThread)
        .where(ConversationThread.app_user_id == target_user_id)
        .values(app_user_id=source_user_id)
    )
    db.execute(
        update(ChannelIdentity)
        .where(ChannelIdentity.app_user_id == target_user_id)
        .values(app_user_id=source_user_id)
    )
    db.execute(
        update(ChannelLinkToken)
        .where(ChannelLinkToken.app_user_id == target_user_id)
        .values(app_user_id=source_user_id)
    )
    current = datetime.now(UTC)
    if has_web_sessions:
        db.execute(
            update(WebSession)
            .where(WebSession.app_user_id == target_user_id)
            .values(app_user_id=source_user_id, revoked_at=current)
        )
        # Import lazily to avoid the identity -> web_auth -> identity module
        # cycle. Failure must abort the transaction: committing revocation
        # without scheduling cache invalidation could briefly revive a session.
        from app.web_auth import mark_web_session_cache_invalidation

        mark_web_session_cache_invalidation(db)
    if has_mcp_grants:
        db.execute(
            update(McpAccessGrant)
            .where(McpAccessGrant.app_user_id == target_user_id)
            .values(app_user_id=source_user_id, revoked_at=current, updated_at=current)
        )
    db.flush()
    target_user = db.get(AppUser, target_user_id)
    if target_user is None:
        raise IdentityConflict("target tenant disappeared during merge")
    db.delete(target_user)
    db.flush()


def _survivor_rank(
    db: Session,
    item: ContentItem,
    dispatches: list[IngestDispatch],
    source_user_id: int,
) -> tuple[int, int, int, int, int, int]:
    segment_count = db.scalar(
        select(func.count(Segment.id)).where(Segment.item_id == item.id)
    ) or 0
    metadata_count = sum(
        getattr(item, field) not in (None, "", [], {}) for field in _CONTENT_FILL_FIELDS
    )
    has_active_dispatch = any(
        dispatch.state in _ACTIVE_DISPATCH_STATES - {"running"}
        for dispatch in dispatches
    )
    return (
        int(item.state == "ready"),
        segment_count,
        metadata_count,
        int(has_active_dispatch),
        int(item.user_id == source_user_id),
        -item.id,
    )


def _reconcile_content(
    survivor: ContentItem,
    loser: ContentItem,
    source_user_id: int,
    target_user_id: int,
) -> None:
    survivor.saved_at = min(survivor.saved_at, loser.saved_at)
    survivor.why_saved = _merge_why_saved(
        survivor.why_saved,
        loser.why_saved,
        survivor.user_id,
        loser.user_id,
        source_user_id,
        target_user_id,
    )
    positions = [
        value
        for value in (survivor.watch_pos_sec, loser.watch_pos_sec)
        if value is not None
    ]
    survivor.watch_pos_sec = max(positions) if positions else None
    states = (survivor.watch_state, loser.watch_state)
    if "watched" in states:
        survivor.watch_state = "watched"
    elif survivor.watch_state is None:
        survivor.watch_state = loser.watch_state
    for field in _CONTENT_FILL_FIELDS:
        if getattr(survivor, field) in (None, "", [], {}):
            setattr(survivor, field, getattr(loser, field))


def _merge_why_saved(
    survivor_value: str | None,
    loser_value: str | None,
    survivor_user_id: int,
    loser_user_id: int,
    source_user_id: int,
    target_user_id: int,
) -> str | None:
    values = {
        survivor_user_id: survivor_value.strip() if survivor_value else "",
        loser_user_id: loser_value.strip() if loser_value else "",
    }
    source_value = values.get(source_user_id, "")
    target_value = values.get(target_user_id, "")
    if source_value and target_value and source_value != target_value:
        merged = f"[source]\n{source_value}\n\n[target]\n{target_value}"
    else:
        merged = source_value or target_value or None
    try:
        return normalize_why_saved(merged)
    except ValueError:
        raise IdentityConflict(
            "saved notes are too long to merge without data loss"
        ) from None


def _token_hash(raw_token: str) -> str:
    normalized = str(raw_token).strip()
    if not normalized:
        raise InvalidLinkToken("link token is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
