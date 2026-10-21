"""Framework-neutral request and response contracts for the knowledge Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal

from typing_extensions import TypedDict

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictInt, model_validator

from app.agent.answer_validation import NaturalAnswerValidationError, validate_natural_answer
from app.agent.context import TurnContext
from app.agent.media import validate_source_location
from app.channels.types import TenantContext


@dataclass(frozen=True)
class UnifiedRetrievalCandidate:
    """Provider-neutral candidate boundary for text and future media rows."""

    candidate_id: str
    kind: Literal["text", "media"]
    score: float
    item_id: int | None = None
    segment_id: int | None = None
    media_id: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None


@dataclass(frozen=True)
class UnifiedRetrievalResult:
    """Bounded retrieval result envelope without committing to DB schema."""

    candidates: tuple[UnifiedRetrievalCandidate, ...] = ()
    source: Literal["text", "multimodal"] = "text"


class Citation(BaseModel):
    """A source that was actually returned by a tenant-scoped tool."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    segment_id: int
    title: str
    excerpt: str
    url: str
    start_sec: float | None = None
    # ``title`` marks the per-item title-and-description row: it has no
    # transcript time, and its excerpt is the title text, not a spoken quote.
    # The default is omitted from serialized output so persisted and public
    # text sources keep their untagged legacy shape (the persisted-source codec
    # treats any other ``kind`` tag as non-text); only ``title`` is written.
    kind: Literal["segment", "title"] = Field(
        default="segment", exclude_if=lambda value: value == "segment"
    )
    _retrieval_score: float | None = PrivateAttr(default=None)
    # Set only by tenant-scoped database hydration, never public/model payloads.
    # A mutable source URL/P ordinal cannot prove exact CID item identity.
    _source_reference: tuple[str, str] | None = PrivateAttr(default=None)

    def __eq__(self, other: object) -> bool:
        """Keep private retrieval diagnostics out of the public source contract."""

        if not isinstance(other, Citation):
            return NotImplemented
        return self.model_dump() == other.model_dump()


class MediaCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    media_id: str
    item_id: StrictInt
    title: str = Field(min_length=1, max_length=360)
    location: str
    start_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_identity(self) -> "MediaCitation":
        if (
            isinstance(self.item_id, bool)
            or self.item_id <= 0
            or not re.fullmatch(r"M[A-Za-z0-9_-]{1,128}", self.evidence_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.media_id)
            or self.evidence_id != "M" + self.media_id
        ):
            raise ValueError("invalid media citation identity")
        validate_source_location(self.location)
        if not self.title.strip() or any(ord(char) < 32 or ord(char) == 127 for char in self.title):
            raise ValueError("invalid media citation title")
        if (self.start_sec is None) != (self.end_sec is None):
            raise ValueError("incomplete media citation time range")
        if self.start_sec is not None and self.end_sec < self.start_sec:
            raise ValueError("invalid media citation time range")
        if any(
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 360
            or any(ord(char) < 32 for char in value)
            for value in self.evidence
        ):
            raise ValueError("invalid media citation evidence")
        return self


class AgentAnswer(BaseModel):
    """Stable answer contract shared by CLI and channel adapters."""

    status: Literal["ok", "not_found", "failed"]
    text: str
    citations: list[Citation] = Field(default_factory=list)
    action_results: list[dict] = Field(default_factory=list)
    thread_id: str | None = None
    error_code: str | None = None
    media_citations: list[MediaCitation] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )


class RetrievalToolPayload(TypedDict):
    """The only retrieval-tool result shape exposed to the planning Agent.

    ``skipped`` is deliberately distinct from an empty successful search:
    every retrieval call within the server-owned stage budgets (5 total, 2
    searches, 3 expansions) executes sequentially, and only a call beyond
    those budgets returns this truthful no-side-effect result.
    """

    status: Literal["ok", "skipped"]
    evidence: list[dict]
    reason: Literal["budget_exhausted"] | None


class GroundedSection(BaseModel):
    """One explicit grounded or unsupported Composer section.

    Unsupported sections intentionally carry no model-authored text. The
    response boundary renders a fixed server-owned notice for that status.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["grounded", "unsupported"] = "grounded"
    text: str | None = Field(default=None, min_length=1)
    citation_ids: list[int] = Field(default_factory=list, max_length=8)
    media_citation_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "GroundedSection":
        if self.status == "grounded":
            if self.text is None or not self.text.strip():
                raise ValueError("grounded section requires text")
            if not self.citation_ids and not self.media_citation_ids:
                raise ValueError("grounded section requires citations")
            return self
        if self.text is not None:
            raise ValueError("unsupported section must not contain model text")
        if self.citation_ids or self.media_citation_ids:
            raise ValueError("unsupported section must not contain citations")
        return self


class GroundedDraft(BaseModel):
    """A grounded answer decision returned by the private answer Agent."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[GroundedSection] = Field(min_length=1, max_length=8)


class AnswerDraft(BaseModel):
    """Grounded Composer draft; never persisted verbatim."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[GroundedSection] = Field(min_length=1, max_length=8)
    # DeepSeek occasionally appends this legacy metadata key as ``null`` to
    # otherwise valid JSON.  Accept only the null form and never use or emit
    # it; arbitrary provider fields remain rejected by ``extra=forbid``.
    sections_note: None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_disposition(self) -> "AnswerDraft":
        if not self.sections:
            raise ValueError("grounded answer requires sections")
        if len(self.sections) > 8:
            raise ValueError("grounded answer has too many sections")
        return self

    @property
    def decision(self) -> GroundedDraft:
        return GroundedDraft(kind="grounded", sections=self.sections or [])


class PlannedSection(BaseModel):
    """Server-validated section plan used before provider text streaming.

    ``task`` is a short topic/answer assignment, not model-authored answer
    text.  It gives each section stream enough context to distinguish adjacent
    questions in a mixed request while staying bounded and source-safe.
    """

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=240)
    status: Literal["grounded", "unsupported"]
    citation_ids: list[int] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_citations(self) -> "PlannedSection":
        try:
            task = validate_natural_answer(self.task).text
        except NaturalAnswerValidationError as exc:
            raise ValueError("planned section task must be short safe topic text") from exc
        self.task = task
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.citation_ids
        ):
            raise ValueError("planned citation ids must be positive integers")
        # A repeated or cross-section duplicate citation is no longer rejected
        # at this schema boundary. ``normalize_section_citations`` drops an
        # in-section repeat and allows the same segment to support several
        # sections; see agent-retrieval-convergence.md.
        if self.status == "grounded" and not self.citation_ids:
            raise ValueError("grounded planned section requires citations")
        if self.status == "unsupported" and self.citation_ids:
            raise ValueError("unsupported planned section must not contain citations")
        return self


class AnswerStreamPlan(BaseModel):
    """Citation-only plan; it deliberately contains no model-authored text."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grounded"]
    sections: list[PlannedSection] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_plan(self) -> "AnswerStreamPlan":
        section_ids = [section.section_id for section in self.sections]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("planned section ids must be unique")
        citation_ids = [
            citation_id
            for section in self.sections
            if section.status == "grounded"
            for citation_id in section.citation_ids
        ]
        # A citation may legitimately support more than one section; the
        # server-owned distinct-segment cap and round-robin clamp are applied
        # by ``normalize_section_citations`` in the output validator, not by
        # this schema.
        if not citation_ids:
            raise ValueError("stream plan requires at least one grounded citation")
        return self
# Historical import name retained for integrations; the section itself is
# unchanged and the top-level duplicate selection field is gone.
AnswerSection = GroundedSection


@dataclass(frozen=True)
class AgentRequest:
    """A trusted request assembled by application code, never by the model."""

    question: str
    tenant: TenantContext
    thread_db_id: int
    thread_public_id: str
    message_id: str
    request_id: str
    history: tuple[dict, ...] = ()
    # Server-owned correlation for high-risk confirmations.  This is the
    # message id of the newest completed turn before the current message;
    # model/tool arguments never carry it.
    latest_turn_message_id: str | None = None
    # A bounded, immutable projection of trusted prior-turn focus.  Appended
    # with a default to preserve positional compatibility for integrations
    # that construct AgentRequest directly.
    context: TurnContext = field(default_factory=TurnContext)

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("question must not be empty")


# Management contracts live in their focused module to keep retrieval types
# small.  Re-exporting them here preserves the package-level contract for
# integrations that historically imported all Agent payloads from ``types``.
from app.agent.management import (  # noqa: E402  (intentional compatibility export)
    BatchItemOperationResult,
    ItemFilters,
    ItemOperationResult,
    KnowledgeItemManagementService,
    SavedItem,
    SavedItemPage,
    decode_cursor,
    encode_cursor,
)
