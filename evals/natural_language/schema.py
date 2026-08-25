"""Strict catalog contracts and safe template resolution."""

from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.diagnostics import _TOOLS as AGENT_TOOL_NAMES
from app.mcp_server import MCP_TOOL_NAMES

Route = Literal["model", "deterministic", "setup"]
KNOWN_TOOLS = frozenset(AGENT_TOOL_NAMES) | frozenset(MCP_TOOL_NAMES)
REQUIRED_CATEGORIES = frozenset(
    {"retrieval", "save", "inventory", "context", "conversation", "safety"}
)
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,79}\Z")


class CatalogError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectation(StrictModel):
    required_tools: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    error_codes: list[str | None] = Field(default_factory=list)
    citations: Literal["required", "forbidden", "optional"] = "optional"
    exact_url_scope: bool = False
    contains: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_tools(self) -> "Expectation":
        groups = (self.required_tools, self.allowed_tools, self.forbidden_tools)
        unknown = {name for group in groups for name in group if name not in KNOWN_TOOLS}
        if unknown:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("tool lists must not contain duplicates")
        if set(self.required_tools) & set(self.forbidden_tools):
            raise ValueError("a tool cannot be both required and forbidden")
        if set(self.allowed_tools) & set(self.forbidden_tools):
            raise ValueError("a tool cannot be both allowed and forbidden")
        return self


class Capture(StrictModel):
    name: str = Field(pattern=r"[a-z][a-z0-9_]{1,63}")
    path: str = Field(min_length=1, max_length=200)


class Turn(StrictModel):
    input: str = Field(min_length=1, max_length=4000)
    route: Route = "model"
    conversation: str = Field(default="primary", pattern=r"[a-z][a-z0-9_-]{0,31}")
    restart_before: bool = False
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    capture: list[Capture] = Field(default_factory=list)
    expect: Expectation = Field(default_factory=Expectation)

    @model_validator(mode="after")
    def valid_route(self) -> "Turn":
        if self.route == "model" and self.tool not in (
            None,
            "ask_stashseek",
            "ask_notebook_agent",
        ):
            raise ValueError("model turns must enter through ask_stashseek")
        if self.route != "model" and self.tool is None:
            raise ValueError("non-model turns require an explicit MCP tool")
        if self.tool is not None and self.tool not in MCP_TOOL_NAMES:
            raise ValueError(f"unknown MCP tool: {self.tool}")
        return self


class Case(StrictModel):
    id: str
    category: str
    description: str = Field(min_length=1, max_length=500)
    tags: list[str] = Field(default_factory=list)
    smoke: bool = False
    requires: list[str] = Field(default_factory=list)
    threshold: float = Field(default=1.0, gt=0, le=1)
    turns: list[Turn] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_id(self) -> "Case":
        if not _ID_RE.fullmatch(self.id):
            raise ValueError("invalid case id")
        return self


class Fixture(StrictModel):
    url: str = Field(min_length=1, max_length=4096)
    topic: str = Field(min_length=1, max_length=200)
    mutable: bool = False


class Catalog(StrictModel):
    version: str = Field(pattern=r"[0-9]+\.[0-9]+\.[0-9]+")
    fixtures: dict[str, Fixture]
    cases: list[Case]

    @model_validator(mode="after")
    def validate_catalog(self) -> "Catalog":
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        missing = REQUIRED_CATEGORIES - {case.category for case in self.cases}
        if missing:
            raise ValueError(f"missing required categories: {sorted(missing)}")
        reserved = {
            f"{name}_{suffix}" for name in self.fixtures
            for suffix in ("url", "topic", "item_id")
        } | {"run_id", "conversation_id", "unknown_topic", "failed_item_id"}
        available = set(reserved)
        formatter = string.Formatter()
        for case in self.cases:
            captures: set[str] = set()
            for turn in case.turns:
                values = [turn.input, *[str(value) for value in turn.arguments.values()]]
                referenced = {
                    field.split(".", 1)[0]
                    for value in values
                    for _, field, _, _ in formatter.parse(value) if field
                }
                unknown = referenced - available - captures
                if unknown:
                    raise ValueError(f"{case.id} references unavailable templates: {sorted(unknown)}")
                capture_names = [capture.name for capture in turn.capture]
                names = set(capture_names)
                if len(capture_names) != len(names):
                    raise ValueError("capture names may not be reused")
                overwritten = names & reserved
                if overwritten:
                    raise ValueError(f"reserved captures: {sorted(overwritten)}")
                if names & captures:
                    raise ValueError("capture names may not be reused")
                captures.update(names)
        return self


def load_catalog(path: str | Path | None = None) -> Catalog:
    target = Path(path) if path else Path(__file__).with_name("catalog.yaml")
    try:
        return Catalog.model_validate(yaml.safe_load(target.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise CatalogError(f"catalog validation failed: {exc}") from exc


def render_template(value: str, variables: dict[str, Any]) -> str:
    fields = [field for _, field, _, _ in string.Formatter().parse(value) if field]
    if any("." in field or "[" in field for field in fields):
        raise CatalogError("template traversal is not allowed")
    missing = [field for field in fields if field not in variables]
    if missing:
        raise CatalogError(f"missing template values: {sorted(set(missing))}")
    return value.format_map({key: str(item) for key, item in variables.items()})


def capture_path(payload: Any, path: str) -> Any:
    current = payload
    for token in path.split("."):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            try:
                current = current[int(token)]
            except IndexError as exc:
                raise CatalogError(f"capture path not found: {path}") from exc
        else:
            raise CatalogError(f"capture path not found: {path}")
    if isinstance(current, (dict, list, bool)) or current is None:
        raise CatalogError("captured template values must be non-null scalars")
    if isinstance(current, str) and (not current or len(current) > 512):
        raise CatalogError("captured template value is empty or too long")
    return current
