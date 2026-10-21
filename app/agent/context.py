"""Bounded, privacy-safe projections of completed conversation turns.

The context in this module is deliberately smaller than model message
history.  It is derived from canonical ``ConversationTurn.sources`` and
``action_results`` rows, and contains only references which are still visible
to the current tenant.  URLs, cursors, mutation state, raw tool payloads and
provider data are never represented by these types.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.types import TenantContext
from app.channels.source_codec import text_source_payloads
from app.models import ContentItem, ConversationThread, ConversationTurn, Segment


DEFAULT_CONTEXT_TURNS = 8
DEFAULT_CONTEXT_SOURCE_ROWS = 12
DEFAULT_CONTEXT_INVENTORY_ROWS = 20
DEFAULT_CONTEXT_TEXT_CHARS = 240

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_READY_STATES = {"ready"}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def _safe_text(value: Any, *, limit: int) -> str:
    """Return a short display string with links and control whitespace removed."""

    if not isinstance(value, str):
        return ""
    # Context text is a model hint, not an evidence payload.  In particular,
    # do not allow a title/excerpt to smuggle a URL into the prompt.
    value = _URL_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    return value[: max(1, int(limit))]


@dataclass(frozen=True)
class ContextCitationRef:
    """A prior source focus reference without its URL or authority meaning."""

    segment_id: int
    item_id: int
    title: str
    excerpt: str = ""
    start_sec: float | None = None

    def __post_init__(self) -> None:
        segment_id = _positive_int(self.segment_id)
        item_id = _positive_int(self.item_id)
        if segment_id is None or item_id is None:
            raise ValueError("context citation identifiers must be positive")
        title = _safe_text(self.title, limit=DEFAULT_CONTEXT_TEXT_CHARS)
        excerpt = _safe_text(self.excerpt, limit=DEFAULT_CONTEXT_TEXT_CHARS)
        if not title:
            title = "未命名来源"
        start = self.start_sec
        if start is not None:
            try:
                start = float(start)
            except (TypeError, ValueError, OverflowError):
                start = None
            if start is not None and (not math.isfinite(start) or start < 0):
                start = None
        object.__setattr__(self, "segment_id", segment_id)
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "excerpt", excerpt)
        object.__setattr__(self, "start_sec", start)


@dataclass(frozen=True)
class ContextItemRef:
    """A prior inventory row usable only as a bounded item reference."""

    item_id: int
    title: str
    ordinal: int = 0
    kind: str | None = None
    platform: str | None = None

    def __post_init__(self) -> None:
        item_id = _positive_int(self.item_id)
        ordinal = _positive_int(self.ordinal)
        if item_id is None:
            raise ValueError("context item identifier must be positive")
        if ordinal is None:
            # Callers constructing a reference directly may not know its
            # position yet; the builder assigns one before exposing context.
            ordinal = 1
        title = _safe_text(self.title, limit=DEFAULT_CONTEXT_TEXT_CHARS)
        if not title:
            title = "未命名条目"
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "ordinal", ordinal)
        object.__setattr__(self, "title", title)
        if self.kind is not None:
            object.__setattr__(self, "kind", _safe_text(self.kind, limit=32) or None)
        if self.platform is not None:
            object.__setattr__(self, "platform", _safe_text(self.platform, limit=32) or None)


@dataclass(frozen=True)
class TurnContext:
    """Immutable bounded context available to a bounded-autonomy turn."""

    recent_sources: tuple[ContextCitationRef, ...] = ()
    recent_inventory: tuple[ContextItemRef, ...] = ()
    # Reserved for a future safe conversational projection.  Raw model
    # history remains on AgentRequest.history and is never copied here.
    history: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        # Copy incoming iterables into tuples so a caller cannot mutate the
        # context after it has been attached to an AgentRequest.
        object.__setattr__(self, "recent_sources", tuple(self.recent_sources))
        object.__setattr__(self, "recent_inventory", tuple(self.recent_inventory))
        object.__setattr__(self, "history", tuple(self.history))

    @property
    def is_empty(self) -> bool:
        return not self.recent_sources and not self.recent_inventory and not self.history

    @property
    def inventory_item_ids(self) -> tuple[int, ...]:
        return tuple(item.item_id for item in self.recent_inventory)


class ContextBuilder:
    """Build a fail-closed projection from completed turns.

    The builder accepts either a trusted ``TenantContext`` or an app-user id
    and either a ``ConversationThread`` or its database id.  The latter form
    keeps unit callers simple while still repeating the tenant predicate in
    every query.
    """

    def __init__(
        self,
        *,
        max_turns: int = DEFAULT_CONTEXT_TURNS,
        max_sources: int | None = None,
        max_inventory: int | None = None,
        max_source_rows: int | None = None,
        max_inventory_rows: int | None = None,
        max_text_chars: int = DEFAULT_CONTEXT_TEXT_CHARS,
    ) -> None:
        self.max_turns = max(0, int(max_turns))
        self.max_sources = max(
            0,
            int(
                max_source_rows
                if max_source_rows is not None
                else (max_sources if max_sources is not None else DEFAULT_CONTEXT_SOURCE_ROWS)
            ),
        )
        self.max_inventory = max(
            0,
            int(
                max_inventory_rows
                if max_inventory_rows is not None
                else (max_inventory if max_inventory is not None else DEFAULT_CONTEXT_INVENTORY_ROWS)
            ),
        )
        self.max_text_chars = max(1, int(max_text_chars))

    def build(
        self,
        db: Session,
        thread: ConversationThread | int | None = None,
        tenant: TenantContext | int | None = None,
        *,
        thread_id: int | None = None,
        app_user_id: int | None = None,
    ) -> TurnContext:
        resolved_thread_id = thread_id
        if isinstance(thread, ConversationThread):
            resolved_thread_id = thread.id
            thread_app_user_id = _positive_int(thread.app_user_id)
        elif thread is not None:
            resolved_thread_id = _positive_int(thread)
            thread_app_user_id = None
        else:
            thread_app_user_id = None
        resolved_app_user_id = app_user_id
        if isinstance(tenant, TenantContext):
            resolved_app_user_id = tenant.app_user_id
        elif tenant is not None:
            resolved_app_user_id = _positive_int(tenant)
        if resolved_thread_id is None or resolved_app_user_id is None:
            return TurnContext()
        if thread_app_user_id is not None and thread_app_user_id != resolved_app_user_id:
            return TurnContext()

        try:
            trusted_thread = db.scalar(
                select(ConversationThread).where(
                    ConversationThread.id == resolved_thread_id,
                    ConversationThread.app_user_id == resolved_app_user_id,
                )
            )
            if trusted_thread is None:
                return TurnContext()
            turns = list(
                db.scalars(
                    select(ConversationTurn)
                    .where(
                        ConversationTurn.thread_id == resolved_thread_id,
                        ConversationTurn.status == "completed",
                    )
                    .order_by(
                        ConversationTurn.created_at.desc(),
                        ConversationTurn.id.desc(),
                    )
                    .limit(self.max_turns)
                )
            )
        except Exception:
            # Context is a quality hint.  A schema/connection problem must not
            # become an authorization or answer failure; empty context is the
            # safe projection.
            return TurnContext()

        if not turns or (self.max_sources == 0 and self.max_inventory == 0):
            return TurnContext()

        source_rows: list[dict[str, Any]] = []
        inventory_rows: list[dict[str, Any]] = []
        for turn in turns:
            if getattr(turn, "answer_status", None) == "mcp_management":
                continue
            source_rows.extend(self._source_payloads(getattr(turn, "sources", None)))
            inventory_rows.extend(self._inventory_payloads(getattr(turn, "action_results", None)))

        # Validate current availability in one tenant-bound query.  This keeps
        # historical sources from becoming evidence after deletion or a failed
        # ingestion, and prevents a forged action row from authorizing scope.
        item_ids = {
            value
            for row in source_rows + inventory_rows
            if (value := _positive_int(row.get("item_id"))) is not None
        }
        if not item_ids:
            return TurnContext()
        try:
            source_segment_ids = {
                segment_id
                for row in source_rows
                for segment_id in [_positive_int(row.get("segment_id"))]
                if segment_id is not None
            }
            available_items = {
                item.id: item
                for item in db.scalars(
                    select(ContentItem).where(
                        ContentItem.id.in_(item_ids),
                        ContentItem.user_id == resolved_app_user_id,
                        ContentItem.deleted_at.is_(None),
                        ContentItem.state.in_(_READY_STATES),
                    )
                )
            }
            # Segment ownership is checked independently of ContentItem so a
            # malformed source cannot pair one item's id with another's text.
            # Some lightweight channel harnesses intentionally omit the
            # segment table; in that case retain safe inventory context while
            # omitting all source focus rows.
            try:
                available_segments = {
                    segment_id: item_id
                    for segment_id, item_id in db.execute(
                        select(Segment.id, Segment.item_id)
                        .where(
                            Segment.id.in_(source_segment_ids),
                            Segment.item_id.in_(item_ids),
                        )
                    ).all()
                }
            except Exception:
                # Segment ownership could not be proved.  Inventory references
                # remain independently tenant/state checked, but every prior
                # source focus row must be omitted rather than pairing an
                # unverified segment with a currently available item.
                available_segments = {}
        except Exception:
            return TurnContext()

        recent_sources: list[ContextCitationRef] = []
        seen_segments: set[int] = set()
        for row in source_rows:
            if len(recent_sources) >= self.max_sources:
                break
            item_id = _positive_int(row.get("item_id"))
            segment_id = _positive_int(row.get("segment_id"))
            if item_id is None or segment_id is None:
                continue
            item = available_items.get(item_id)
            if item is None:
                continue
            if available_segments.get(segment_id) != item_id:
                continue
            if segment_id in seen_segments:
                continue
            # A persisted Citation always has a URL.  Validate its presence,
            # but intentionally do not copy it into the resulting reference.
            if not isinstance(row.get("url"), str) or not row["url"].strip():
                continue
            title = _safe_text(row.get("title"), limit=self.max_text_chars)
            excerpt = _safe_text(row.get("excerpt"), limit=self.max_text_chars)
            if not title:
                continue
            seen_segments.add(segment_id)
            recent_sources.append(
                ContextCitationRef(
                    segment_id=segment_id,
                    item_id=item_id,
                    title=title,
                    excerpt=excerpt,
                    start_sec=row.get("start_sec"),
                )
            )

        recent_inventory: list[ContextItemRef] = []
        seen_items: set[int] = set()
        for row in inventory_rows:
            if len(recent_inventory) >= self.max_inventory:
                break
            item_id = _positive_int(row.get("item_id"))
            if item_id is None or item_id in seen_items:
                continue
            item = available_items.get(item_id)
            if item is None or not self._inventory_row_ready(row):
                continue
            title = _safe_text(row.get("title"), limit=self.max_text_chars)
            if not title:
                continue
            seen_items.add(item_id)
            recent_inventory.append(
                ContextItemRef(
                    item_id=item_id,
                    title=title,
                    ordinal=len(recent_inventory) + 1,
                    kind=_safe_text(row.get("kind"), limit=32) or None,
                    platform=_safe_text(row.get("platform"), limit=32) or None,
                )
            )
        return TurnContext(tuple(recent_sources), tuple(recent_inventory))

    def _source_payloads(self, payload: Any) -> list[dict[str, Any]]:
        return text_source_payloads(payload)

    def _inventory_payloads(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, (list, tuple)):
            return []
        rows: list[dict[str, Any]] = []
        for value in payload:
            if not isinstance(value, dict):
                continue
            nested = value.get("items")
            if isinstance(nested, list):
                rows.extend(dict(row) for row in nested if isinstance(row, dict))
                continue
            # ``get_saved_item`` stores one SavedItem projection directly;
            # mutation result rows do not have a title/ingestion state and are
            # therefore rejected below.
            if "item_id" in value:
                rows.append(dict(value))
        return rows

    @staticmethod
    def _inventory_row_ready(row: dict[str, Any]) -> bool:
        return (
            row.get("ingestion_state") in _READY_STATES
            and row.get("deleted_at") is None
            and not row.get("safe_error_code")
            and isinstance(row.get("title"), str)
        )


__all__ = [
    "ContextBuilder",
    "ContextCitationRef",
    "ContextItemRef",
    "TurnContext",
]
