"""Shared fakes for the page Q&A service and API tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.channels.types import TenantContext
from app.config import Settings

TENANT = TenantContext(7, 11, "web", "web", "user-7")
CONVERSATION_ID = "page-" + "a" * 32
VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def sqlite_factory():
    """Conversation tables only, as plain SQLite DDL (JSONB columns as TEXT)."""

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE conversation_thread (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                app_user_id INTEGER NOT NULL,
                channel_identity_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_conversation_id TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE conversation_turn (
                id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                sources TEXT NOT NULL,
                model_messages TEXT NOT NULL,
                answer_status TEXT NOT NULL DEFAULT 'legacy',
                error_code TEXT,
                action_results TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'completed',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(thread_id, message_id)
            )
            """
        )
    return sessionmaker(engine, expire_on_commit=False)


def make_settings(**overrides) -> Settings:
    values: dict[str, Any] = {
        "database_url": "sqlite://",
        "notebook_agent_env": "development",
        "agent_timeout_seconds": 5.0,
        "browser_companion_allowed_origins": (),
    }
    values.update(overrides)
    return Settings(**values)


def youtube_page(
    paragraph_texts: list[str],
    *,
    language: str = "en",
    title: str = "Test Video",
    description: str | None = None,
    chapters: list[dict] | None = None,
    captions: bool = True,
) -> dict:
    """A YouTube page whose cues form exactly one paragraph per text.

    Every text becomes three 10 s cues (30 s), so paragraph ``k`` (1-based)
    starts at ``30 * (k - 1)`` seconds.
    """

    cues = []
    for index, text in enumerate(paragraph_texts):
        base = index * 30
        cues.extend(
            {
                "start_sec": base + part * 10,
                "end_sec": base + part * 10 + 10,
                "text": text if part == 0 else f"{text} (part {part})",
            }
            for part in range(3)
        )
    caption = (
        {"status": "available", "source": "official_cc", "language": language, "cues": cues}
        if captions
        else {"status": "unavailable"}
    )
    return {
        "platform": "youtube",
        "platform_id": VIDEO_ID,
        "canonical_url": VIDEO_URL,
        "page_url": VIDEO_URL,
        "metadata": {
            "title": title,
            "author": "Some Author",
            "language": language,
            "description": description,
            "chapters": chapters or [],
        },
        "caption": caption,
    }


def message(
    page: dict,
    *,
    question: str = "What does the speaker say?",
    message_id: str = "message-1",
    playback_sec: float | None = None,
) -> dict:
    return {
        "message_id": message_id,
        "question": question,
        "playback_sec": playback_sec,
        "page": page,
    }


@dataclass
class ScriptedModel:
    """A FunctionModel whose streamed answer chunks come from a per-call script.

    The keyword-rewrite call (non-streamed, recognised by its instructions)
    returns ``rewrite`` or raises when ``rewrite`` is ``None``.
    """

    scripts: list[list[str]]
    delay: float = 0.0
    rewrite: str | None = None
    calls: list[dict] = field(default_factory=list)

    @property
    def answer_calls(self) -> list[dict]:
        return [call for call in self.calls if call["kind"] == "answer"]

    @property
    def rewrite_calls(self) -> list[dict]:
        return [call for call in self.calls if call["kind"] == "rewrite"]

    def _record(self, messages, info) -> dict:
        instructions = info.instructions or ""
        call = {
            "kind": "rewrite" if "检索关键词" in instructions else "answer",
            "messages": messages,
            "instructions": instructions,
            "prompt": last_user_prompt(messages),
        }
        self.calls.append(call)
        return call

    def build(self) -> FunctionModel:
        async def stream(messages, info):
            call = self._record(messages, info)
            assert call["kind"] == "answer"
            index = min(len(self.answer_calls) - 1, len(self.scripts) - 1)
            for chunk in self.scripts[index]:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk

        def function(messages, info):
            call = self._record(messages, info)
            if call["kind"] == "rewrite":
                if self.rewrite is None:
                    raise RuntimeError("rewrite provider failure")
                return ModelResponse(parts=[TextPart(self.rewrite)])
            index = min(len(self.answer_calls) - 1, len(self.scripts) - 1)
            return ModelResponse(parts=[TextPart("".join(self.scripts[index]))])

        return FunctionModel(function, stream_function=stream)


def last_user_prompt(messages) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


def all_user_prompts(messages) -> list[str]:
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
