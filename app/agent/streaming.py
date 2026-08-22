"""Private Agent-to-channel streaming events.

The HTTP adapter projects these events into the public SSE contract.  Provider
messages, prompts, tool payloads and model metadata never cross this module's
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai.messages import ModelMessage

from app.agent.autonomy import TurnTodoItem
from app.agent.types import AgentAnswer, Citation


PublicStepCode = Literal[
    "updating_plan",
    "searching_library",
    "reading_context",
    "checking_source",
    "reviewing_library",
    "checking_item",
    "handling_save",
    "managing_library",
    "working",
]
StepOutcome = Literal["completed", "failed", "skipped"]

AgentStreamEventType = Literal[
    "activity",
    "step_started",
    "step_completed",
    "plan_updated",
    "section_started",
    "text_delta",
    "section_completed",
    "section_aborted",
    "completed",
]

_PUBLIC_STEP_CODES: dict[str, PublicStepCode] = {
    "todo_write": "updating_plan",
    "search_segments": "searching_library",
    "get_neighbors": "reading_context",
    "get_item": "checking_source",
    "open_at": "checking_source",
    "list_saved_items": "reviewing_library",
    "get_saved_item": "checking_item",
    "request_save_confirmation": "handling_save",
    "save_videos": "handling_save",
    "confirm_video_save": "handling_save",
    "clarify_save_confirmation": "handling_save",
    "cancel_video_save": "handling_save",
    "update_saved_item": "managing_library",
    "delete_saved_items": "managing_library",
    "confirm_item_deletion": "managing_library",
    "clarify_item_deletion": "managing_library",
    "cancel_item_deletion": "managing_library",
    "restore_saved_items": "managing_library",
    "retry_item_ingestion": "managing_library",
}


def public_step_code(tool_name: object) -> PublicStepCode:
    """Map a server-owned tool name to the closed browser step vocabulary."""

    return _PUBLIC_STEP_CODES.get(tool_name, "working") if isinstance(tool_name, str) else "working"


@dataclass(frozen=True, slots=True)
class AgentPlanItem:
    """Validated, browser-safe projection of one turn-local Todo item."""

    id: str
    title: str
    status: Literal["pending", "in_progress", "completed", "blocked"]

    def __post_init__(self) -> None:
        item = TurnTodoItem(self.id, self.title, self.status)
        object.__setattr__(self, "id", item.id)
        object.__setattr__(self, "title", item.title)


@dataclass(frozen=True)
class AgentStreamEvent:
    """A bounded internal event emitted by one Agent execution."""

    type: AgentStreamEventType
    request_id: str
    message_id: str
    activity: Literal["retrieving", "planning_answer", "composing"] | None = None
    step_id: str | None = None
    step_code: PublicStepCode | None = None
    step_outcome: StepOutcome | None = None
    result_count: int | None = None
    plan: tuple[AgentPlanItem, ...] = ()
    section_id: str | None = None
    status: Literal["grounded", "unsupported"] | None = None
    citation_ids: tuple[int, ...] = ()
    citations: tuple[Citation, ...] = ()
    text: str | None = None
    reason: Literal["provider_failure", "timeout", "cancelled"] | None = None
    answer: AgentAnswer | None = None
    new_messages: tuple[ModelMessage, ...] = field(default_factory=tuple)
    persist: bool = True


__all__ = [
    "AgentPlanItem",
    "AgentStreamEvent",
    "AgentStreamEventType",
    "PublicStepCode",
    "StepOutcome",
    "public_step_code",
]
