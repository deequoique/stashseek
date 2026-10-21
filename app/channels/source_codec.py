"""Fail-closed codec for legacy text and tagged media conversation sources."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


# Text citations may carry their own ``kind`` tag (the title-and-description
# row is ``title``); every other tag is a non-text row and is never reread as
# text. ``segment`` is the default and is normally not written at all.
_TEXT_SOURCE_KINDS = frozenset({"segment", "title"})


@dataclass(frozen=True)
class DecodedSources:
    text: tuple[dict[str, Any], ...] = ()
    media: tuple[Any, ...] = ()


def decode_sources(value: object) -> DecodedSources:
    """Split persisted JSON without allowing malformed media into text."""

    if not isinstance(value, (list, tuple)):
        return DecodedSources()
    text: list[dict[str, Any]] = []
    media: list[Any] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "media":
            citation = _validate_media(row)
            if citation is not None:
                media.append(citation)
            continue
        # Tagged rows are never silently reinterpreted as legacy text.
        if "kind" in row and row["kind"] not in _TEXT_SOURCE_KINDS:
            continue
        if _looks_like_media(row):
            continue
        text.append(dict(row))
    return DecodedSources(tuple(text), tuple(media))


def text_source_payloads(value: object) -> list[dict[str, Any]]:
    """Return only text rows (untagged legacy rows and ``title`` rows)."""

    return list(decode_sources(value).text)


def validated_text_citations(value: object) -> list[Any]:
    from app.agent.types import Citation

    citations: list[Citation] = []
    for row in text_source_payloads(value):
        try:
            citations.append(Citation.model_validate(row))
        except (TypeError, ValueError):
            continue
    return citations


def encode_sources(
    text_sources: Sequence[dict[str, Any]] = (),
    media_citations: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Preserve text shape and append explicitly tagged validated media."""

    encoded: list[dict[str, Any]] = [dict(row) for row in text_sources if isinstance(row, dict)]
    for citation in media_citations:
        validated = _validate_media(citation.model_dump(mode="json"))
        if validated is None:
            continue
        row = validated.model_dump(mode="json")
        row["kind"] = "media"
        encoded.append(row)
    return encoded


def _looks_like_media(row: dict[str, Any]) -> bool:
    return any(key in row for key in ("evidence_id", "media_id", "evidence"))


def _validate_media(value: object) -> Any | None:
    from app.agent.types import MediaCitation

    if isinstance(value, MediaCitation):
        payload = value.model_dump(mode="json")
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        return None
    # The media contract is intentionally caller-owned until the Agent worker
    # supplies its real positive item_id field. Do not persist a weaker row.
    payload.pop("kind", None)
    item_id = payload.get("item_id")
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
        return None
    try:
        return MediaCitation.model_validate(payload)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DecodedSources",
    "decode_sources",
    "encode_sources",
    "text_source_payloads",
    "validated_text_citations",
]
