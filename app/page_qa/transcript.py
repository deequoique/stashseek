"""Paragraph merging, token estimation and context selection for page Q&A.

Everything here is pure, in-memory and deterministic.  The caller-supplied cues
are merged into ~30 s paragraphs (each with one start timestamp) and either sent
whole (small transcripts) or narrowed to a playback window plus the BM25 top
hits (large transcripts).  The ingest chunker is intentionally not reused: its
80-200 word windows and ``start_time`` chapter key do not fit this use.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.connectors.base import Cue
from app.page_qa.bm25 import CJK_CHAR_CLASS, BM25Index

PARAGRAPH_TARGET_SECONDS = 30.0
PARAGRAPH_MAX_GAP_SECONDS = 4.0
PLAYBACK_WINDOW_SECONDS = 300.0
BM25_TOP_K = 12
# Per-paragraph overhead for the ``[P12 05:30]`` label in the prompt.
PARAGRAPH_LABEL_TOKENS = 6
# At most this share of the window budget may go to the chapter outline.
OUTLINE_BUDGET_FRACTION = 0.1
OMITTED_MARKER = "(...)"

# Letters plus CJK punctuation / fullwidth forms: each counts as ~1 token.
_CJK_ESTIMATE = re.compile(
    "[" + CJK_CHAR_CLASS + "\u3000-\u303f\uff00-\uffef]"
)
_CJK_LETTER = re.compile("[" + CJK_CHAR_CLASS + "]")
_CJK_LANGUAGE_PREFIXES = ("zh", "ja", "ko")


class ChapterLike(Protocol):
    """Capture chapters use ``start_sec`` (not the yt-dlp ``start_time``)."""

    title: str
    start_sec: float


@dataclass(frozen=True, slots=True)
class Paragraph:
    index: int  # 1-based, stable for one transcript
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True, slots=True)
class ContextSelection:
    mode: Literal["full", "window"]
    paragraphs: tuple[Paragraph, ...]  # ordered by time
    outline: tuple[str, ...]  # window mode only
    total_paragraphs: int
    total_tokens: int
    selected_tokens: int
    playback_indices: tuple[int, ...] = ()
    bm25_indices: tuple[int, ...] = ()


def format_timestamp(seconds: float) -> str:
    """``MM:SS`` below one hour, ``H:MM:SS`` from one hour."""

    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_paragraph(paragraph: Paragraph) -> str:
    return f"[P{paragraph.index} {format_timestamp(paragraph.start_sec)}] {paragraph.text}"


def _join_cue_texts(texts: Sequence[str]) -> str:
    joined = ""
    for raw in texts:
        part = " ".join(raw.split())
        if not part:
            continue
        if joined and not (
            _CJK_ESTIMATE.fullmatch(joined[-1]) and _CJK_ESTIMATE.fullmatch(part[0])
        ):
            joined += " "
        joined += part
    return joined


def build_paragraphs(cues: Sequence[Cue]) -> list[Paragraph]:
    """Merge ordered cues into ~30 s paragraphs.

    A paragraph closes at a cue boundary once it spans at least
    ``PARAGRAPH_TARGET_SECONDS`` or when the next cue starts more than
    ``PARAGRAPH_MAX_GAP_SECONDS`` after the current paragraph ends.
    """

    paragraphs: list[Paragraph] = []
    texts: list[str] = []
    start = 0.0
    end = 0.0

    def close() -> None:
        nonlocal texts
        text = _join_cue_texts(texts)
        if text:
            paragraphs.append(Paragraph(len(paragraphs) + 1, start, end, text))
        texts = []

    for cue in cues:
        if texts and cue.start - end > PARAGRAPH_MAX_GAP_SECONDS:
            close()
        if not texts:
            start = cue.start
            end = cue.end
        else:
            end = max(end, cue.end)
        texts.append(cue.text)
        if end - start >= PARAGRAPH_TARGET_SECONDS:
            close()
    if texts:
        close()
    return paragraphs


def estimate_tokens(text: str) -> int:
    """Intentionally pessimistic: CJK characters x1.0 plus other words x1.4."""

    cjk = len(_CJK_ESTIMATE.findall(text))
    words = len(_CJK_ESTIMATE.sub(" ", text).split())
    return math.ceil(cjk * 1.0 + words * 1.4)


def paragraph_tokens(paragraph: Paragraph) -> int:
    return estimate_tokens(paragraph.text) + PARAGRAPH_LABEL_TOKENS


def script_mismatch(question: str, language: str | None) -> bool:
    """True when the question's script differs from the transcript language.

    The question is CJK when most of its letters are CJK; the transcript is CJK
    when its language code is Chinese, Japanese or Korean.  An unknown language
    never triggers a rewrite.
    """

    if not language:
        return False
    transcript_is_cjk = language.strip().lower().startswith(_CJK_LANGUAGE_PREFIXES)
    letters = [char for char in question if char.isalpha()]
    if not letters:
        return False
    cjk_letters = sum(1 for char in letters if _CJK_LETTER.fullmatch(char))
    question_is_cjk = cjk_letters / len(letters) >= 0.3
    return question_is_cjk != transcript_is_cjk


def _outline(
    chapters: Sequence[ChapterLike], budget_tokens: int
) -> tuple[tuple[str, ...], int]:
    lines: list[str] = []
    used = 0
    for chapter in sorted(chapters, key=lambda value: (value.start_sec, value.title)):
        title = " ".join(chapter.title.split())
        if not title:
            continue
        line = f"{format_timestamp(chapter.start_sec)} {title}"
        cost = estimate_tokens(line) + 2
        if used + cost > budget_tokens:
            break
        lines.append(line)
        used += cost
    return tuple(lines), used


def _distance_to_playback(paragraph: Paragraph, playback_sec: float) -> float:
    if paragraph.start_sec <= playback_sec <= paragraph.end_sec:
        return 0.0
    return min(
        abs(paragraph.start_sec - playback_sec), abs(paragraph.end_sec - playback_sec)
    )


def select_context(
    paragraphs: Sequence[Paragraph],
    *,
    query: str,
    playback_sec: float | None,
    chapters: Sequence[ChapterLike] = (),
    full_budget: int,
    window_budget: int,
    top_k: int = BM25_TOP_K,
    window_seconds: float = PLAYBACK_WINDOW_SECONDS,
) -> ContextSelection:
    """Pick the paragraphs that go into the model context.

    Small transcripts (estimated tokens <= ``full_budget``) are sent whole.
    Otherwise the context holds the paragraphs overlapping
    ``playback_sec +/- window_seconds`` and the BM25 top ``top_k`` for
    ``query``, plus a chapter outline, capped at ``window_budget`` tokens.
    Under pressure the lowest-scored BM25 hits are dropped first, then the
    playback paragraphs farthest from the playback position.
    """

    costs = {paragraph.index: paragraph_tokens(paragraph) for paragraph in paragraphs}
    total_tokens = sum(costs.values())
    if total_tokens <= full_budget:
        return ContextSelection(
            mode="full",
            paragraphs=tuple(paragraphs),
            outline=(),
            total_paragraphs=len(paragraphs),
            total_tokens=total_tokens,
            selected_tokens=total_tokens,
        )

    outline, outline_tokens = _outline(
        chapters, int(window_budget * OUTLINE_BUDGET_FRACTION)
    )
    paragraph_budget = max(0, window_budget - outline_tokens)

    playback_priority: list[Paragraph] = []
    if playback_sec is not None:
        low, high = playback_sec - window_seconds, playback_sec + window_seconds
        playback_priority = sorted(
            (p for p in paragraphs if p.end_sec >= low and p.start_sec <= high),
            key=lambda p: (_distance_to_playback(p, playback_sec), p.index),
        )
    playback_ids = {p.index for p in playback_priority}

    bm25_priority: list[Paragraph] = []
    if paragraphs and query.strip():
        index = BM25Index([paragraph.text for paragraph in paragraphs])
        bm25_priority = [
            paragraphs[position]
            for position, _score in index.top_k(query, top_k)
            if paragraphs[position].index not in playback_ids
        ]

    priority = playback_priority + bm25_priority
    if not priority and paragraphs:
        # Nothing anchors the question (no playback position, no keyword hit):
        # sample the whole video evenly instead of sending an empty context.
        stride = max(1, math.ceil(total_tokens / max(1, paragraph_budget)))
        priority = list(paragraphs[::stride])

    chosen: list[Paragraph] = []
    used = 0
    for paragraph in priority:
        cost = costs[paragraph.index]
        if used + cost > paragraph_budget:
            continue
        chosen.append(paragraph)
        used += cost
    chosen.sort(key=lambda paragraph: paragraph.index)
    chosen_ids = {paragraph.index for paragraph in chosen}
    return ContextSelection(
        mode="window",
        paragraphs=tuple(chosen),
        outline=outline,
        total_paragraphs=len(paragraphs),
        total_tokens=total_tokens,
        selected_tokens=used + outline_tokens,
        playback_indices=tuple(
            sorted(p.index for p in playback_priority if p.index in chosen_ids)
        ),
        bm25_indices=tuple(
            sorted(p.index for p in bm25_priority if p.index in chosen_ids)
        ),
    )


def render_context(selection: ContextSelection) -> str:
    """Render the selected paragraphs (and outline) as prompt data lines."""

    lines: list[str] = []
    if selection.outline:
        lines.append("Chapters:")
        lines.extend(selection.outline)
        lines.append("")
    previous_index: int | None = None
    for paragraph in selection.paragraphs:
        if previous_index is not None and paragraph.index != previous_index + 1:
            lines.append(OMITTED_MARKER)
        lines.append(render_paragraph(paragraph))
        previous_index = paragraph.index
    return "\n".join(lines)
