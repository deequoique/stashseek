"""Typed MCP boundary for StashSeek Chat.

This module is deliberately a thin adapter.  It translates validated MCP
arguments into trusted ``ChannelEnvelope`` values for ``ChannelService`` and
tenant-bound calls to the existing saved-item/ingestion services.  Importing
the module does not open a database connection, construct a model client, or
import the optional LangBot integration.
"""

from __future__ import annotations

import contextvars
import os
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from app.agent.limits import SEARCH_RESULT_LIMIT
from app.agent.types import AgentAnswer, Citation
from app.channels.types import ChannelEnvelope, TenantContext
from app.config import Settings, get_settings
from app.mcp_grants import (
    InsufficientMcpScope,
    McpGrantMetadata,
    McpGrantError,
    McpGrantService,
    ResolvedMcpGrant,
)
from app.mcp_readiness import (
    McpMutationReadiness,
    assess_mcp_mutation_readiness,
    probe_mcp_worker,
)
from app.limits import MAX_WHY_SAVED_CHARS


MCP_TOOL_NAMES: tuple[str, ...] = (
    "ask_stashseek",
    "ask_notebook_agent",
    "submit_knowledge_urls",
    "list_saved_items",
    "get_saved_item",
    "update_saved_item",
    "request_delete_saved_items",
    "confirm_item_deletion",
    "cancel_item_deletion",
    "restore_saved_items",
    "retry_item_ingestion",
)
READ_TOOL_NAMES: frozenset[str] = frozenset(
    {"ask_stashseek", "ask_notebook_agent", "list_saved_items", "get_saved_item"}
)
FULL_TOOL_NAMES: frozenset[str] = frozenset(MCP_TOOL_NAMES)
try:
    from mcp_types import ToolAnnotations
    from mcp.server.mcpserver import MCPServer as OfficialMCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    _TOOL_ANNOTATIONS = {
        "ask_stashseek": ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False
        ),
        "ask_notebook_agent": ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False
        ),
        "submit_knowledge_urls": ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False
        ),
        "list_saved_items": ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        ),
        "get_saved_item": ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True
        ),
        "update_saved_item": ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True
        ),
        "request_delete_saved_items": ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False
        ),
        "confirm_item_deletion": ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False
        ),
        "cancel_item_deletion": ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True
        ),
        "restore_saved_items": ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True
        ),
        "retry_item_ingestion": ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False
        ),
    }
except ImportError as exc:  # pragma: no cover - package is a required dep
    raise RuntimeError("StashSeek Chat MCP requires mcp==2.0.0") from exc
_CONVERSATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MCP_PATH_TOKEN_RE = re.compile(r"^/mcp/c/([^/?#]+)\Z")
_MAX_URL_BATCH = 10
_MAX_URL_CHARS = 4096
_MAX_WHY_SAVED_CHARS = MAX_WHY_SAVED_CHARS
_SAFE_ERROR_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_AUTH_CONTEXT: contextvars.ContextVar[ResolvedMcpGrant | None] = contextvars.ContextVar(
    "mcp_resolved_grant", default=None
)


class McpInputError(ValueError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class McpAuthenticationError(McpInputError):
    def __init__(self, error_code: str = "invalid_grant") -> None:
        super().__init__(error_code)


class McpToolError(ValueError):
    """Unexpected adapter/service error projected without exception text."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def _safe_error_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and _SAFE_ERROR_RE.fullmatch(value):
        return value
    return fallback


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskStashSeekInput(_StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question is required")
        return normalized


    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _CONVERSATION_RE.fullmatch(normalized):
            raise ValueError("conversation_id must be a bounded opaque identifier")
        return normalized


# These aliases are used on the official MCPServer function signatures so the
# wire schemas carry the same bounds as the adapter models (the SDK derives
# schemas directly from annotations).
QuestionArg = Annotated[StrictStr, Field(min_length=1, max_length=4000)]
ConversationArg = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
    ),
]
ItemIDArg = Annotated[StrictInt, Field(gt=0)]
ItemIDsArg = Annotated[list[ItemIDArg], Field(min_length=1, max_length=10)]
URLArg = Annotated[StrictStr, Field(min_length=1, max_length=_MAX_URL_CHARS)]
URLBatchArg = Annotated[list[URLArg], Field(min_length=1, max_length=_MAX_URL_BATCH)]
WhySavedArg = Annotated[StrictStr | None, Field(max_length=_MAX_WHY_SAVED_CHARS)]


class CitationProjection(_StrictModel):
    item_id: int = Field(gt=0)
    segment_id: int = Field(gt=0)
    title: str = Field(max_length=500)
    excerpt: str = Field(max_length=4000)
    url: str = Field(max_length=_MAX_URL_CHARS)
    start_sec: float | None = Field(default=None, ge=0)


class SavedItemProjection(_StrictModel):
    item_id: int = Field(gt=0)
    platform: str = Field(max_length=32)
    kind: str = Field(max_length=32)
    title: str = Field(max_length=500)
    author: str | None = Field(default=None, max_length=500)
    url: str = Field(max_length=_MAX_URL_CHARS)
    duration_sec: int | None = None
    saved_at: datetime
    why_saved: str | None = Field(default=None, max_length=_MAX_WHY_SAVED_CHARS)
    ingestion_state: str = Field(max_length=32)
    safe_error_code: str | None = Field(default=None, max_length=64)
    deleted_at: datetime | None = None
    expires_at: datetime | None = None
    restorable: bool | None = None


class SavedItemsOutput(_StrictModel):
    status: str = Field(max_length=32)
    items: list[SavedItemProjection] = Field(default_factory=list, max_length=50)
    next_cursor: str | None = Field(default=None, max_length=512)
    error_code: str | None = Field(default=None, max_length=64)


class SavedItemOutput(_StrictModel):
    status: str = Field(max_length=32)
    item: SavedItemProjection | None = None
    error_code: str | None = Field(default=None, max_length=64)


class AskStashSeekOutput(_StrictModel):
    status: str = Field(max_length=32)
    answer: str = Field(max_length=16000)
    # ``KnowledgeServices`` and the Agent runtime expose at most the bounded
    # search window; keep the public MCP projection aligned with that cap.
    citations: list[CitationProjection] = Field(
        default_factory=list, max_length=SEARCH_RESULT_LIMIT
    )
    conversation_id: str = Field(max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)
    request_id: str = Field(max_length=128)
    elapsed_ms: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=64)

    # ``text`` is a compatibility read-only alias for callers that use the
    # existing AgentAnswer vocabulary; it is not serialized into MCP output.
    @property
    def text(self) -> str:
        return self.answer


# Former Python model names remain import-compatible while the canonical model
# definitions use the StashSeek brand. Keeping one model prevents schema drift
# between the two MCP tool names.
AskNotebookAgentInput = AskStashSeekInput
AskNotebookAgentOutput = AskStashSeekOutput


class SubmitKnowledgeURLsInput(_StrictModel):
    urls: list[str] = Field(min_length=1, max_length=_MAX_URL_BATCH)
    why_saved: str | None = Field(default=None, max_length=_MAX_WHY_SAVED_CHARS)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            if not isinstance(value, str) or not (value := value.strip()) or len(value) > _MAX_URL_CHARS:
                raise ValueError("invalid_url")
            cleaned.append(value)
        return cleaned

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation(cls, value: str) -> str:
        normalized = value.strip()
        if not _CONVERSATION_RE.fullmatch(normalized):
            raise ValueError("conversation_id must be a bounded opaque identifier")
        return normalized


class ListSavedItemsInput(_StrictModel):
    kind: str | None = None
    platform: str | None = None
    state: str | None = None
    location: str = "library"
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class GetSavedItemInput(_StrictModel):
    item_id: int = Field(gt=0)


class UpdateSavedItemInput(_StrictModel):
    item_id: int = Field(gt=0)
    why_saved: str | None = Field(default=None, max_length=_MAX_WHY_SAVED_CHARS)


class ItemIDsInput(_StrictModel):
    item_ids: list[int] = Field(min_length=1, max_length=10)

    @field_validator("item_ids")
    @classmethod
    def validate_item_ids(cls, values: list[int]) -> list[int]:
        if any(isinstance(value, bool) or value <= 0 for value in values):
            raise ValueError("invalid_batch")
        if len(set(values)) != len(values):
            raise ValueError("invalid_batch")
        return values


class DeleteRequestInput(ItemIDsInput):
    conversation_id: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation(cls, value: str) -> str:
        normalized = value.strip()
        if not _CONVERSATION_RE.fullmatch(normalized):
            raise ValueError("conversation_id must be a bounded opaque identifier")
        return normalized


class ConfirmDeleteInput(_StrictModel):
    confirmation_code: str = Field(min_length=1, max_length=32)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("confirmation_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
            raise ValueError("confirmation_missing")
        return value

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation(cls, value: str) -> str:
        normalized = value.strip()
        if not _CONVERSATION_RE.fullmatch(normalized):
            raise ValueError("conversation_id must be a bounded opaque identifier")
        return normalized


class CancelDeleteInput(_StrictModel):
    conversation_id: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation(cls, value: str) -> str:
        normalized = value.strip()
        if not _CONVERSATION_RE.fullmatch(normalized):
            raise ValueError("conversation_id must be a bounded opaque identifier")
        return normalized


class ItemOperationOutput(_StrictModel):
    status: str = Field(max_length=32)
    error_code: str | None = Field(default=None, max_length=64)
    results: list["OperationRow"] = Field(default_factory=list, max_length=10)
    confirmation_code: str | None = Field(default=None, max_length=32)


class OperationRow(_StrictModel):
    item_id: int | None = Field(default=None, gt=0)
    status: str | None = Field(default=None, max_length=64)
    safe_error_code: str | None = Field(default=None, max_length=64)
    result_id: str | None = Field(default=None, max_length=128)
    input_index: int | None = Field(default=None, ge=0, le=9)
    state: str | None = Field(default=None, max_length=32)
    item_ids: list[int] | None = Field(default=None, min_length=1, max_length=10)


ItemOperationOutput.model_rebuild()


@dataclass(frozen=True)
class AuthenticatedPath:
    token: str
    canonical_path: str
    from_url_path: bool = False


def allowed_tool_names(scope: str) -> tuple[str, ...]:
    if scope not in {"read", "full"}:
        raise ValueError("scope must be read or full")
    return tuple(name for name in MCP_TOOL_NAMES if scope == "full" or name in READ_TOOL_NAMES)


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def extract_authentication(
    headers: Mapping[str, str] | None,
    *,
    path: str,
    query_string: str | bytes | None = None,
    url_token_mode: bool = False,
    canonical_path: str = "/mcp",
    scheme: str | None = None,
) -> AuthenticatedPath:
    """Extract a Bearer header or explicitly-enabled opaque URL capability."""

    query = query_string.decode("utf-8", "ignore") if isinstance(query_string, bytes) else (query_string or "")
    try:
        query_keys = {key.lower() for key, _value in parse_qsl(query, keep_blank_values=True)}
    except ValueError:
        query_keys = set()
    if "token" in query_keys or re.search(r"(?:^|&)token(?:=|&|$)", query, flags=re.IGNORECASE):
        raise McpAuthenticationError("query_token_not_allowed")
    authorization = _header(headers, "authorization")
    if authorization is not None:
        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise McpAuthenticationError()
        return AuthenticatedPath(parts[1].strip(), canonical_path, False)
    prefix = canonical_path.rstrip("/") or "/mcp"
    path_pattern = re.compile(re.escape(prefix) + r"/c/([^/?#]+)\Z")
    match = path_pattern.fullmatch(path)
    if match is None or not url_token_mode:
        raise McpAuthenticationError()
    if scheme is not None and scheme.lower() != "https":
        raise McpAuthenticationError("https_required")
    token = match.group(1)
    if len(token) > 512:
        raise McpAuthenticationError()
    return AuthenticatedPath(token, canonical_path, True)


def redact_request_uri(uri: str, *, canonical_path: str = "/mcp") -> str:
    """Return a safe request target for application/proxy diagnostics.

    The default path is kept for compatibility, while any configured MCP
    path can be supplied by callers that emit access logs.  Capability
    material is never returned, even when a token appears in a query or
    fragment.
    """

    try:
        path = uri.split("?", 1)[0].split("#", 1)[0]
    except Exception:
        return "/mcp"
    canonical = canonical_path or "/mcp"
    prefix = canonical.rstrip("/") or "/mcp"
    if re.fullmatch(re.escape(prefix) + r"/c/[^/?#]+", path):
        return canonical
    # Keep the legacy default matcher for callers that do not pass a custom
    # path and redact malformed token-bearing targets conservatively.
    if _MCP_PATH_TOKEN_RE.fullmatch(path):
        return "/mcp"
    return path or canonical


def _project_citation(citation: Citation) -> CitationProjection:
    return CitationProjection(
        item_id=citation.item_id,
        segment_id=citation.segment_id,
        title=citation.title,
        excerpt=citation.excerpt,
        url=citation.url,
        start_sec=citation.start_sec,
    )


class McpToolFacade:
    """Application service adapter used by both MCP transports and tests."""

    def __init__(
        self,
        *,
        channel_service=None,
        grant_service: McpGrantService | None = None,
        grant: ResolvedMcpGrant | None = None,
        tenant: TenantContext | None = None,
        token: str | None = None,
        scope: str | None = None,
        settings: Settings | None = None,
        session_factory=None,
        submission=None,
        management=None,
        pending=None,
        publisher=None,
        account_id: str = "mcp",
        mutation_ready: bool | None = None,
        mutation_error_code: str = "mutation_unavailable",
    ) -> None:
        self.channel_service = channel_service
        self.grant_service = grant_service
        if grant is None and tenant is not None:
            # Explicit dependency injection for in-memory/stdio tests.  A
            # production HTTP request must use ``grant_service``; this path is
            # never populated from an MCP tool argument.
            grant = ResolvedMcpGrant(
                metadata=McpGrantMetadata(
                    grant_id=tenant.external_user_id,
                    app_user_id=tenant.app_user_id,
                    scope=scope or "read",
                    expires_at=None,
                    revoked_at=None,
                    disabled_at=None,
                    created_at=None,
                    updated_at=None,
                    rotated_at=None,
                    last_used_at=None,
                    label=None,
                    created_by=None,
                ),
                tenant=tenant,
            )
        self.static_grant = grant
        self.token = token
        self.scope = scope or (grant.scope if grant is not None else None)
        self.settings = settings
        self.session_factory = session_factory
        self.submission = submission
        self.management = management
        self.pending = pending
        self.publisher = publisher
        self.account_id = account_id
        # ``None`` keeps the normal lazy composition path.  Deployments that
        # perform an explicit broker/object-store/worker readiness probe can
        # pass ``False`` to withhold mutating tools before discovery.
        self.mutation_ready = mutation_ready
        self.mutation_error_code = mutation_error_code

    def _ensure_services(self) -> None:
        if self.channel_service is not None:
            return
        # Imports and provider/database construction stay inside invocation;
        # importing app.mcp_server for schema inspection remains side-effect
        # free.
        from app.bootstrap import build_channel_service

        settings = self.settings or get_settings()
        self.channel_service = build_channel_service(settings)
        if self.session_factory is None:
            from app.db import get_session_factory

            self.session_factory = get_session_factory()
        if self.management is None or self.submission is None or self.pending is None:
            from app.agent.management import KnowledgeItemManagementService
            from app.channels.pending_actions import PendingConfirmationService
            from app.ingest.submission import build_ingest_submission_service
            from app.ingest.tasks import publish_ingest_dispatch

            self.management = self.management or KnowledgeItemManagementService(
                self.session_factory, retention_days=settings.trash_retention_days
            )
            self.pending = self.pending or PendingConfirmationService(self.session_factory)
            self.submission = self.submission or build_ingest_submission_service(
                self.session_factory,
                self.publisher or publish_ingest_dispatch,
                settings,
            )
        if self.grant_service is None:
            self.grant_service = McpGrantService(self.session_factory)

    def _grant(self, required_scope: str = "read") -> ResolvedMcpGrant:
        current = _AUTH_CONTEXT.get() or self.static_grant
        if current is None and self.grant_service is not None and self.token:
            try:
                current = self.grant_service.resolve(
                    self.token, required_scope=required_scope
                )
            except McpGrantError as exc:
                raise McpAuthenticationError(exc.error_code) from None
        if current is None:
            raise McpAuthenticationError()
        if required_scope == "full" and current.scope != "full":
            raise InsufficientMcpScope()
        return current

    def _thread(self, resolved: ResolvedMcpGrant, conversation_id: str, message_id: str):
        if self.session_factory is None:
            self._ensure_services()
        from app.channels.conversations import get_or_create_thread
        from sqlalchemy import select
        from app.models import ConversationTurn

        envelope = ChannelEnvelope(
            channel="mcp",
            account_id=resolved.tenant.account_id,
            external_user_id=resolved.tenant.external_user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text="mcp action",
        )
        with self.session_factory() as db:
            thread = get_or_create_thread(db, resolved.tenant, envelope)
            latest = db.scalar(
                select(ConversationTurn)
                .where(
                    ConversationTurn.thread_id == thread.id,
                    ConversationTurn.status == "completed",
                )
                .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
                .limit(1)
            )
            thread_id, public_id = thread.id, thread.public_id
            latest_id = latest.message_id if latest is not None else None
            db.commit()
        return thread_id, public_id, latest_id

    def _record_management_turn(
        self,
        resolved: ResolvedMcpGrant,
        conversation_id: str,
        message_id: str,
        *,
        thread_id: int | None = None,
    ) -> bool:
        try:
            return self._record_management_turn_impl(
                resolved, conversation_id, message_id, thread_id=thread_id
            )
        except Exception:
            # The anchor is a safety aid.  If its auxiliary write fails, keep
            # the operation result bounded.  Callers that issued a new
            # confirmation must treat this as a failed operation: without a
            # durable anchor, a later confirmation could not be safely tied to
            # the request that displayed its one-time code.
            return False

    def _record_management_turn_impl(
        self,
        resolved: ResolvedMcpGrant,
        conversation_id: str,
        message_id: str,
        *,
        thread_id: int | None = None,
    ) -> bool:
        """Persist a bounded MCP turn used solely as a confirmation anchor.

        MCP management tools do not pass through ``ChannelService`` and thus
        would otherwise leave no completed turn for the pending-action chain
        to compare against.  This synthetic turn intentionally contains no
        item ids, URLs, confirmation codes, or model messages; it is a
        server-owned ordering marker, not conversational context.
        """

        if self.session_factory is None:
            self._ensure_services()
        from app.channels.conversations import get_or_create_thread, save_completed_turn
        from app.models import ConversationThread

        envelope = ChannelEnvelope(
            channel="mcp",
            account_id=resolved.tenant.account_id,
            external_user_id=resolved.tenant.external_user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text="mcp management action",
        )
        with self.session_factory() as db:
            thread = (
                db.get(ConversationThread, thread_id)
                if thread_id is not None
                else None
            )
            if thread is None:
                thread = get_or_create_thread(db, resolved.tenant, envelope)
            if (
                thread.app_user_id != resolved.tenant.app_user_id
                or thread.channel_identity_id != resolved.tenant.channel_identity_id
                or thread.closed_at is not None
            ):
                db.rollback()
                return False
            save_completed_turn(
                db,
                thread=thread,
                envelope=envelope,
                assistant_text="mcp management action",
                sources=(),
                model_messages=(),
                answer_status="mcp_management",
                action_results=(),
            )
            db.commit()
        return True

    def _management_thread(
        self,
        resolved: ResolvedMcpGrant,
        conversation_id: str,
        message_id: str,
    ) -> tuple[int, str, str | None]:
        """Create/read the tenant thread and return its latest prior turn."""

        return self._thread(resolved, conversation_id, message_id)

    def _management_thread_safe(
        self,
        resolved: ResolvedMcpGrant,
        conversation_id: str,
        message_id: str,
    ) -> tuple[tuple[int, str, str | None] | None, ItemOperationOutput | None]:
        try:
            return self._management_thread(resolved, conversation_id, message_id), None
        except Exception as exc:
            return None, self._failure(
                getattr(exc, "error_code", None) or "management_unavailable"
            )

    async def ask_stashseek(
        self, question: str | AskStashSeekInput, conversation_id: str = "default"
    ) -> AskStashSeekOutput:
        request = (
            question
            if isinstance(question, AskStashSeekInput)
            else AskStashSeekInput(question=question, conversation_id=conversation_id)
        )
        started = time.monotonic()
        request_id = uuid4().hex
        try:
            if request.question.startswith("/"):
                raise McpToolError("slash_command_not_allowed")
            resolved = self._grant("read")
            self._ensure_services()
            envelope = ChannelEnvelope(
                channel="mcp",
                account_id=resolved.tenant.account_id,
                external_user_id=resolved.tenant.external_user_id,
                conversation_id=request.conversation_id,
                message_id=uuid4().hex,
                text=request.question,
                request_id=request_id,
            )
            answer = await self.channel_service.handle(envelope)
            if not isinstance(answer, AgentAnswer):
                # Fake services may return a compatible object; projection is
                # still strict and intentionally excludes arbitrary payloads.
                answer = AgentAnswer.model_validate(answer)
            return AskStashSeekOutput(
                status=answer.status,
                answer=answer.text,
                citations=[_project_citation(value) for value in answer.citations],
                conversation_id=request.conversation_id,
                thread_id=answer.thread_id,
                request_id=request_id,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=answer.error_code,
            )
        except McpToolError as exc:
            return AskStashSeekOutput(
                status="failed", answer="该请求不支持命令式操作。", citations=[],
                conversation_id=request.conversation_id, request_id=request_id,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=exc.error_code,
            )
        except (McpGrantError, McpAuthenticationError) as exc:
            return AskStashSeekOutput(
                status="failed", answer="MCP 凭证无效或权限不足。", citations=[],
                conversation_id=request.conversation_id, request_id=request_id,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=exc.error_code,
            )
        except Exception as exc:
            # Never put exception text or provider payloads into MCP output.
            return AskStashSeekOutput(
                status="failed", answer="知识库服务暂时不可用。", citations=[],
                conversation_id=request.conversation_id, request_id=request_id,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=_safe_error_code(getattr(exc, "error_code", None), "runtime_error"),
            )

    async def ask_notebook_agent(
        self, question: str | AskStashSeekInput, conversation_id: str = "default"
    ) -> AskStashSeekOutput:
        """Compatibility alias for clients using the former MCP tool name."""

        return await self.ask_stashseek(question, conversation_id)

    def _failure(self, error_code: str) -> ItemOperationOutput:
        return ItemOperationOutput(
            status="failed", error_code=_safe_error_code(error_code, "management_failed")
        )

    def _prepare(
        self, required_scope: str
    ) -> tuple[ResolvedMcpGrant | None, ItemOperationOutput | None]:
        """Resolve scope and lazily construct services without protocol errors."""

        try:
            if required_scope == "full" and self.mutation_ready is False:
                return None, self._failure(self.mutation_error_code)
            resolved = self._grant(required_scope)
            self._ensure_services()
            return resolved, None
        except (McpGrantError, McpAuthenticationError) as exc:
            return None, self._failure(exc.error_code)
        except Exception as exc:
            return None, self._failure(
                getattr(exc, "error_code", None) or "management_unavailable"
            )

    def _service_call(self, required_scope: str, callback: Callable[[], Any]) -> Any:
        try:
            self._grant(required_scope)
            self._ensure_services()
            return callback()
        except (McpGrantError, McpAuthenticationError) as exc:
            return self._failure(exc.error_code)
        except Exception as exc:
            return self._failure(getattr(exc, "error_code", None) or "management_failed")

    async def submit_knowledge_urls(
        self, urls: list[str], why_saved: str | None = None, conversation_id: str = "default"
    ) -> ItemOperationOutput:
        request = SubmitKnowledgeURLsInput(urls=urls, why_saved=why_saved, conversation_id=conversation_id)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, request.conversation_id, message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            result = self.submission.submit_urls(
                resolved.tenant, request.urls, why_saved=request.why_saved,
                request_key=f"mcp:{resolved.grant_id}:{uuid4().hex}",
            )
            rows = [
                value.model_dump() if hasattr(value, "model_dump") else value.__dict__
                for value in result.results
            ]
            failures = [
                row for row in rows
                if row.get("safe_error_code")
                or row.get("status") in {
                    "invalid_url", "unsupported_url", "queue_unavailable",
                    "create_failed", "retry_not_allowed", "purge_in_progress",
                }
            ]
            output = ItemOperationOutput(
                status=("failed" if len(failures) == len(rows) else "partial" if failures else "ok"),
                error_code=(failures[0].get("safe_error_code") if failures else "items_submitted"),
                results=rows,
            )
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "submission_failed")
        self._record_management_turn(
            resolved, request.conversation_id, message_id, thread_id=thread_id
        )
        return output

    async def list_saved_items(self, **kwargs: Any) -> Any:
        request = ListSavedItemsInput(**kwargs)
        resolved, failure = self._prepare("read")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, "default", message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            page = self.management.list_saved_items(resolved.tenant, **request.model_dump())
            output = page
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "management_failed")
        self._record_management_turn(
            resolved, "default", message_id, thread_id=thread_id
        )
        return output

    async def get_saved_item(self, item_id: int) -> Any:
        request = GetSavedItemInput(item_id=item_id)
        resolved, failure = self._prepare("read")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, "default", message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            output = self.management.get_saved_item(resolved.tenant, request.item_id)
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "item_not_found")
        self._record_management_turn(
            resolved, "default", message_id, thread_id=thread_id
        )
        return output

    async def update_saved_item(self, item_id: int, why_saved: str | None = None) -> ItemOperationOutput:
        request = UpdateSavedItemInput(item_id=item_id, why_saved=why_saved)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, "default", message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            result = self.management.update_saved_item(resolved.tenant, request.item_id, request.why_saved)
            output = ItemOperationOutput(status="ok", error_code="item_updated", results=[result.model_dump()])
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "management_failed")
        self._record_management_turn(
            resolved, "default", message_id, thread_id=thread_id
        )
        return output

    async def request_delete_saved_items(self, item_ids: list[int], conversation_id: str = "default") -> ItemOperationOutput:
        request = DeleteRequestInput(item_ids=item_ids, conversation_id=conversation_id)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, request.conversation_id, message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, latest_id = context
        try:
            result = self.pending.request_delete(
                resolved.tenant, thread_id, request.item_ids,
                management=self.management, request_message_id=message_id,
                latest_turn_message_id=latest_id,
            )
            output = ItemOperationOutput(
                status=result.status,
                error_code=result.error_code or ("confirmation_required" if result.status == "confirmation_required" else None),
                results=list(result.results) or [{"item_ids": list(result.item_ids)}],
                confirmation_code=result.confirmation_code,
            )
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "management_failed")
        # A newly-issued confirmation code is only usable when its ordering
        # anchor was durably persisted.  If the auxiliary marker write fails,
        # revoke the pending action best-effort before returning a code-free
        # bounded failure.  Never hand a caller a code that cannot be safely
        # validated against the request marker on a later call.
        marker_persisted = self._record_management_turn(
            resolved, request.conversation_id, message_id, thread_id=thread_id
        )
        if not marker_persisted:
            try:
                self.pending.cancel_delete(resolved.tenant, thread_id)
            except Exception:
                pass
            return self._failure("management_unavailable")
        return output

    async def confirm_item_deletion(self, confirmation_code: str, conversation_id: str = "default") -> ItemOperationOutput:
        request = ConfirmDeleteInput(confirmation_code=confirmation_code, conversation_id=conversation_id)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, request.conversation_id, message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, latest_id = context
        try:
            result, operation = self.pending.confirm_delete(
                resolved.tenant, thread_id, message_id=message_id,
                message_text=f"确认删除 {request.confirmation_code}",
                management=self.management, latest_turn_message_id=latest_id,
            )
            rows = list(result.results)
            if operation is not None:
                rows = [value.model_dump() for value in operation.results]
            output = ItemOperationOutput(status=result.status, error_code=result.error_code, results=rows)
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "confirmation_missing")
        self._record_management_turn(
            resolved, request.conversation_id, message_id, thread_id=thread_id
        )
        return output

    async def cancel_item_deletion(self, conversation_id: str = "default") -> ItemOperationOutput:
        request = CancelDeleteInput(conversation_id=conversation_id)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, request.conversation_id, message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            result = self.pending.cancel_delete(resolved.tenant, thread_id)
            output = ItemOperationOutput(status=result.status, error_code=result.error_code, results=list(result.results))
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "confirmation_missing")
        self._record_management_turn(
            resolved, request.conversation_id, message_id, thread_id=thread_id
        )
        return output

    async def restore_saved_items(self, item_ids: list[int]) -> ItemOperationOutput:
        request = ItemIDsInput(item_ids=item_ids)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, "default", message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            result = self.management.restore_saved_items(resolved.tenant, request.item_ids)
            output = ItemOperationOutput(status="ok", error_code="items_restored", results=[value.model_dump() for value in result.results])
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "management_failed")
        self._record_management_turn(
            resolved, "default", message_id, thread_id=thread_id
        )
        return output

    async def retry_item_ingestion(self, item_id: int) -> ItemOperationOutput:
        request = GetSavedItemInput(item_id=item_id)
        resolved, failure = self._prepare("full")
        if failure is not None or resolved is None:
            return failure or self._failure("management_unavailable")
        message_id = uuid4().hex
        context, context_failure = self._management_thread_safe(
            resolved, "default", message_id
        )
        if context_failure is not None or context is None:
            return context_failure or self._failure("management_unavailable")
        thread_id, _public_id, _latest_id = context
        try:
            result = self.submission.retry_item(
                resolved.tenant, request.item_id,
                request_key=f"mcp:{resolved.grant_id}:{uuid4().hex}:retry",
            )
            output = ItemOperationOutput(status=result.status, error_code=result.safe_error_code, results=[result.model_dump() if hasattr(result, "model_dump") else result.__dict__])
        except Exception as exc:
            output = self._failure(getattr(exc, "error_code", None) or "retry_not_allowed")
        self._record_management_turn(
            resolved, "default", message_id, thread_id=thread_id
        )
        return output


def _new_sdk_server(name: str, settings: Settings | None = None):
    return OfficialMCPServer(name=name)


def _mcp_transport_security(settings: Settings) -> TransportSecuritySettings:
    """Keep DNS-rebinding protection while admitting the validated Web origin."""

    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]
    if settings.web_auth_enabled and settings.web_public_origin:
        public_origin = urlsplit(settings.web_public_origin)
        allowed_hosts.append(public_origin.netloc)
        allowed_origins.append(settings.web_public_origin)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _register(server, fn: Callable[..., Any], *, description: str = "") -> None:
    name = fn.__name__
    annotations = _TOOL_ANNOTATIONS.get(name)
    server.add_tool(
        fn,
        name=name,
        description=description,
        annotations=annotations,
        structured_output=True,
    )


def create_mcp_server(
    *,
    name: str = "StashSeek Chat",
    scope: str = "full",
    facade: McpToolFacade | None = None,
    **facade_kwargs: Any,
):
    """Create one typed SDK server; no application resources are opened here."""

    if scope not in {"read", "full"}:
        raise ValueError("scope must be read or full")
    facade = facade or McpToolFacade(scope=scope, **facade_kwargs)
    server = _new_sdk_server(name, facade.settings)

    async def ask_stashseek(
        question: QuestionArg, conversation_id: ConversationArg = "default"
    ) -> AskStashSeekOutput:
        return await facade.ask_stashseek(question, conversation_id)

    async def ask_notebook_agent(
        question: QuestionArg, conversation_id: ConversationArg = "default"
    ) -> AskStashSeekOutput:
        return await facade.ask_notebook_agent(question, conversation_id)

    async def submit_knowledge_urls(
        urls: URLBatchArg,
        why_saved: WhySavedArg = None,
        conversation_id: ConversationArg = "default",
    ) -> ItemOperationOutput:
        return await facade.submit_knowledge_urls(urls, why_saved, conversation_id)

    async def list_saved_items(
        kind: Annotated[str | None, Field(max_length=32)] = None,
        platform: Annotated[str | None, Field(max_length=32)] = None,
        state: Annotated[str | None, Field(max_length=32)] = None,
        location: Annotated[str, Field(pattern=r"^(library|trash)$")] = "library",
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        cursor: Annotated[str | None, Field(max_length=512)] = None,
    ) -> SavedItemsOutput:
        value = await facade.list_saved_items(
            kind=kind, platform=platform, state=state, location=location,
            limit=limit, cursor=cursor,
        )
        if hasattr(value, "items") and hasattr(value, "next_cursor"):
            return SavedItemsOutput(
                status="ok",
                items=[SavedItemProjection.model_validate(item.model_dump(mode="json")) for item in value.items],
                next_cursor=value.next_cursor,
            )
        return SavedItemsOutput(
            status="failed", error_code=getattr(value, "error_code", None) or "management_failed"
        )

    async def get_saved_item(item_id: ItemIDArg) -> SavedItemOutput:
        value = await facade.get_saved_item(item_id)
        if hasattr(value, "item_id"):
            return SavedItemOutput(
                status="ok",
                item=SavedItemProjection.model_validate(value.model_dump(mode="json")),
            )
        return SavedItemOutput(
            status="failed", error_code=getattr(value, "error_code", None) or "item_not_found"
        )

    async def update_saved_item(item_id: ItemIDArg, why_saved: WhySavedArg = None) -> ItemOperationOutput:
        return await facade.update_saved_item(item_id, why_saved)

    async def request_delete_saved_items(
        item_ids: ItemIDsArg, conversation_id: ConversationArg = "default"
    ) -> ItemOperationOutput:
        return await facade.request_delete_saved_items(item_ids, conversation_id)

    async def confirm_item_deletion(
        confirmation_code: Annotated[str, Field(min_length=1, max_length=32, pattern=r"[A-Za-z0-9_-]{1,32}")],
        conversation_id: ConversationArg = "default",
    ) -> ItemOperationOutput:
        return await facade.confirm_item_deletion(confirmation_code, conversation_id)

    async def cancel_item_deletion(conversation_id: ConversationArg = "default") -> ItemOperationOutput:
        return await facade.cancel_item_deletion(conversation_id)

    async def restore_saved_items(item_ids: ItemIDsArg) -> ItemOperationOutput:
        return await facade.restore_saved_items(item_ids)

    async def retry_item_ingestion(item_id: ItemIDArg) -> ItemOperationOutput:
        return await facade.retry_item_ingestion(item_id)

    functions: dict[str, Callable[..., Any]] = {
        "ask_stashseek": ask_stashseek,
        "ask_notebook_agent": ask_notebook_agent,
        "submit_knowledge_urls": submit_knowledge_urls,
        "list_saved_items": list_saved_items,
        "get_saved_item": get_saved_item,
        "update_saved_item": update_saved_item,
        "request_delete_saved_items": request_delete_saved_items,
        "confirm_item_deletion": confirm_item_deletion,
        "cancel_item_deletion": cancel_item_deletion,
        "restore_saved_items": restore_saved_items,
        "retry_item_ingestion": retry_item_ingestion,
    }
    descriptions = {
        "ask_stashseek": "Chat with everything you've saved and find the matching source video.",
        "ask_notebook_agent": "Compatibility alias: ask StashSeek Chat a natural-language knowledge question.",
        "submit_knowledge_urls": "Submit bounded knowledge URLs for asynchronous ingestion.",
        "list_saved_items": "List this grant's tenant-scoped saved items.",
        "get_saved_item": "Read one tenant-scoped saved item.",
        "update_saved_item": "Update the saved-item reason.",
        "request_delete_saved_items": "Request recoverable deletion and return a one-time confirmation code.",
        "confirm_item_deletion": "Confirm the server-owned pending deletion; item ids are never accepted here.",
        "cancel_item_deletion": "Cancel the server-owned pending deletion.",
        "restore_saved_items": "Restore bounded items from the recycle bin.",
        "retry_item_ingestion": "Retry one failed ingestion dispatch.",
    }
    tool_names = allowed_tool_names(scope)
    if scope == "full" and facade.mutation_ready is False:
        # A process can remain MCP-protocol-available while its mutating
        # dependencies are not ready.  Do not advertise tools that cannot
        # safely execute; the read profile remains useful for discovery and
        # grounded questions.
        tool_names = tuple(name for name in tool_names if name in READ_TOOL_NAMES)
    for tool_name in tool_names:
        _register(server, functions[tool_name], description=descriptions[tool_name])
    # Expose profile metadata without coupling callers to SDK internals.
    server.mcp_facade = facade
    server.allowed_tool_names = tool_names
    return server


class McpAuthMiddleware:
    """ASGI wrapper resolving bearer grants before the canonical MCP route."""

    def __init__(
        self,
        app,
        grant_service: McpGrantService,
        *,
        settings: Settings | None = None,
        read_app=None,
        full_app=None,
    ):
        self.app = app
        self.read_app = read_app
        self.full_app = full_app
        self.grant_service = grant_service
        self.settings = settings or get_settings()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            return await self._lifespan(receive, send)
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        headers = {
            key.decode("latin1"): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        try:
            auth = extract_authentication(
                headers,
                path=scope.get("path", ""),
                query_string=scope.get("query_string", b""),
                url_token_mode=self.settings.mcp_url_token_mode,
                canonical_path=self.settings.mcp_path,
                scheme=scope.get("scheme"),
            )
            resolved = self.grant_service.resolve(auth.token)
        except (McpGrantError, McpAuthenticationError) as exc:
            await self._error(send, exc.error_code)
            return
        except Exception:
            # Database/identity lookup failures are indistinguishable from an
            # unavailable credential at the transport boundary.  Never pass
            # driver text or token material into an HTTP response.
            await self._error(send, "authentication_unavailable")
            return
        # The bearer header is authoritative when both mechanisms are
        # present, but a capability-shaped URL still has to be rewritten so
        # the SDK router sees the configured canonical endpoint.  The URL
        # token itself is ignored in this branch.
        canonical_prefix = self.settings.mcp_path.rstrip("/") or "/mcp"
        dynamic_path = re.fullmatch(
            re.escape(canonical_prefix) + r"/c/[^/?#]+", scope.get("path", "")
        )
        if auth.from_url_path or dynamic_path is not None:
            scope = dict(scope)
            scope["path"] = auth.canonical_path
            scope["raw_path"] = auth.canonical_path.encode()
        scope = dict(scope)
        scope["mcp.grant"] = resolved
        marker = _AUTH_CONTEXT.set(resolved)
        try:
            selected = self.full_app if resolved.scope == "full" else self.read_app
            await (selected or self.app)(scope, receive, send)
        finally:
            _AUTH_CONTEXT.reset(marker)

    async def _lifespan(self, receive, send):
        """Start both scope profiles' official Streamable HTTP managers.

        ``MCPServer.streamable_http_app`` owns a per-app session manager.  A
        read-scope grant is dispatched to a second app, so forwarding only
        the full app's lifespan leaves that manager uninitialized.  Enter
        both Starlette router lifespan contexts under one ASGI lifecycle.
        """

        startup = await receive()
        if startup.get("type") != "lifespan.startup":
            return
        apps = [self.app]
        if self.read_app is not None and self.read_app is not self.app:
            apps.append(self.read_app)
        try:
            async with AsyncExitStack() as stack:
                for app in apps:
                    router = getattr(app, "router", None)
                    context_factory = getattr(router, "lifespan_context", None)
                    if context_factory is None:
                        raise RuntimeError(
                            "mcp==2.0.0 Streamable HTTP app lacks lifespan support"
                        )
                    await stack.enter_async_context(context_factory(app))
                await send({"type": "lifespan.startup.complete"})
                while True:
                    message = await receive()
                    if message.get("type") == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
        except Exception as exc:
            # Keep startup failures private while making the process fail
            # clearly to uvicorn/TestClient.
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": f"MCP startup failed: {type(exc).__name__}",
                }
            )
            return

    @staticmethod
    async def _error(send, error_code: str):
        body = b'{"error":"authentication_failed"}'
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_streamable_http_app(
    *,
    server=None,
    grant_service: McpGrantService,
    settings: Settings | None = None,
):
    """Wrap the SDK's stateless Streamable HTTP app with grant auth."""

    if server is None:
        server = create_mcp_server(scope="full")
    settings = settings or get_settings()
    try:
        app = server.streamable_http_app(
            streamable_http_path=settings.mcp_path,
            json_response=True,
            stateless_http=True,
            host=settings.mcp_host,
            transport_security=_mcp_transport_security(settings),
        )
    except AttributeError as exc:
        raise RuntimeError("mcp==2.0.0 is required for Streamable HTTP") from exc
    # Discovery is scope-gated as well as invocation-gated.  Build a read
    # profile beside the full profile and let the resolved grant select the
    # app before MCP initialization/tools/list is dispatched.
    read_server = create_mcp_server(
        scope="read", facade=server.mcp_facade, settings=settings
    )
    try:
        read_app = read_server.streamable_http_app(
            streamable_http_path=settings.mcp_path,
            json_response=True,
            stateless_http=True,
            host=settings.mcp_host,
            transport_security=_mcp_transport_security(settings),
        )
    except AttributeError:
        read_app = None
    return McpAuthMiddleware(
        app, grant_service, settings=settings, read_app=read_app, full_app=app
    )


def run_stdio(server=None, *, settings: Settings | None = None) -> None:
    """Run stdio with protocol-clean stdout and diagnostics on stderr."""

    settings = settings or get_settings()
    from app.diagnostics import configure_runtime_logging

    configure_runtime_logging(
        log_dir=settings.stashseek_log_dir,
        max_bytes=settings.stashseek_log_max_bytes,
        backup_count=settings.stashseek_log_backup_count,
        console_stream="stderr",
    )
    if server is None:
        # stdio has no HTTP header, so a local operator may provide one
        # explicitly in the subprocess environment.  The raw value is never
        # logged; it is resolved through the same hash-only grant service.
        token = os.environ.get("MCP_TOKEN")
        if not token or not token.strip():
            raise RuntimeError(
                "MCP_TOKEN is required when starting the stdio MCP server"
            )
        from app.db import get_session_factory

        session_factory = get_session_factory()
        grant_service = McpGrantService(session_factory)
        try:
            resolved = grant_service.resolve(token.strip())
        except McpGrantError:
            # Do not echo the raw bearer value or a database/provider error.
            raise RuntimeError("MCP_TOKEN is invalid or unavailable") from None
        readiness = (
            assess_mcp_mutation_readiness(
                settings,
                session_factory=session_factory,
                worker_probe=probe_mcp_worker,
            )
            if resolved.scope == "full"
            else None
        )
        server = create_mcp_server(
            scope=resolved.scope,
            settings=settings,
            facade=McpToolFacade(
                settings=settings,
                grant_service=grant_service,
                grant=resolved,
                mutation_ready=readiness.ready if readiness is not None else None,
                mutation_error_code=(
                    readiness.error_code
                    if readiness is not None
                    else "mutation_unavailable"
                ),
            ),
        )
    try:
        server.run(transport="stdio")
    except TypeError as exc:
        raise RuntimeError("mcp==2.0.0 stdio transport is unavailable") from exc


def run_streamable_http(server=None, *, settings: Settings | None = None, grant_service=None) -> None:
    """Run the authenticated, middleware-wrapped Streamable HTTP ASGI app."""

    settings = settings or get_settings()
    from app.db import get_session_factory

    session_factory = get_session_factory()
    grant_service = grant_service or McpGrantService(session_factory)
    readiness = assess_mcp_mutation_readiness(
        settings,
        session_factory=session_factory,
        worker_probe=probe_mcp_worker,
    )
    if server is None:
        server = create_mcp_server(
            scope="full",
            settings=settings,
            facade=McpToolFacade(
                settings=settings,
                grant_service=grant_service,
                mutation_ready=readiness.ready,
                mutation_error_code=readiness.error_code,
            ),
        )
    else:
        # Servers passed by an embedding process still receive the production
        # decision before their profile is exposed.
        facade = getattr(server, "mcp_facade", None)
        if facade is not None:
            facade.mutation_ready = readiness.ready
            facade.mutation_error_code = readiness.error_code
        if readiness.ready is False:
            # Rebuild the official profile around the same facade so full
            # discovery cannot retain stale mutating registrations.
            server = create_mcp_server(
                scope="full",
                facade=facade or McpToolFacade(
                    settings=settings,
                    grant_service=grant_service,
                    mutation_ready=False,
                    mutation_error_code=readiness.error_code,
                ),
            )
    if settings.web_auth_enabled:
        # The dispatcher routes MCP before FastAPI so bearer authorization is
        # never interpreted as a browser cookie/session.
        from app.web_runtime import create_combined_asgi_app
        app = create_combined_asgi_app(
            settings=settings,
            mcp_server=server,
            grant_service=grant_service,
        )
    else:
        app = create_streamable_http_app(
            server=server, grant_service=grant_service, settings=settings
        )
    import uvicorn

    config = uvicorn.Config(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level="info",
        access_log=False,
        log_config=None,
    )
    uvicorn.Server(config).run()


# Public aliases retained for callers that prefer noun-style names.
MCPServer = OfficialMCPServer
MCPToolFacade = McpToolFacade
create_server = create_mcp_server


__all__ = [
    "AskNotebookAgentInput", "AskNotebookAgentOutput",
    "AskStashSeekInput", "AskStashSeekOutput", "CitationProjection",
    "McpAuthMiddleware", "McpAuthenticationError", "McpGrantService",
    "McpMutationReadiness", "assess_mcp_mutation_readiness",
    "probe_mcp_worker",
    "McpInputError", "McpToolError", "McpToolFacade", "SubmitKnowledgeURLsInput",
    "allowed_tool_names", "create_mcp_server", "create_streamable_http_app",
    "extract_authentication", "redact_request_uri", "run_stdio",
    "run_streamable_http", "MCPServer", "MCPToolFacade", "create_server",
]
