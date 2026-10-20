"""The per-item "title row": title + description as one retrievable segment.

Titles and descriptions never appear in the transcript, so a query that only
matches the title ("what was that video about computer prices") cannot find the
item through body segments.  Each ready item therefore carries one extra
``segment`` row at ``seq = -1`` whose text is the title plus the first
``TITLE_ROW_DESCRIPTION_CHARS`` characters of the description.  It has its own
embedding and ``fts``, so it joins both retrieval paths like any body segment.

Ingestion and ``app.cli reindex`` share these helpers so the two writers can
never disagree about what a title row looks like.  Body enumeration (neighbors,
``open_at``, page Q&A) must exclude ``boundary_kind == TITLE_ROW_KIND``.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from app.models import Segment
from app.retrieval.zh_tokens import zh_tsvector

# Same CJK range as the query-language check in ``app.retrieval.search``.
_CJK_RE = re.compile(r"[㐀-鿿]")

TITLE_ROW_SEQ = -1
TITLE_ROW_KIND = "title"
TITLE_ROW_DESCRIPTION_CHARS = 400


def title_row_text(item: Any) -> str:
    """Title (or platform ID) plus a bounded description excerpt."""

    title = (item.title or "").strip() or str(item.platform_id)
    description = (item.description or "").strip()
    if not description:
        return title
    return f"{title}\n{description[:TITLE_ROW_DESCRIPTION_CHARS]}".strip()


def title_row_fts(text: str, lang: str | None) -> ColumnElement:
    """Stored ``fts`` expression for the title row.

    Chinese items use the bigram vector like their body segments. Other items
    use the ``english`` configuration, but when the title text itself
    contains CJK (a Chinese title over English captions, common for reposted
    videos) the bigram vector is concatenated as well, so a Chinese query can
    still match the title lexically; ``english`` alone would index the whole
    Chinese title as one opaque token.
    """

    if lang and lang.startswith("zh"):
        return zh_tsvector(text)
    english = func.to_tsvector("english", text)
    if _CJK_RE.search(text):
        return english.op("||")(zh_tsvector(text))
    return english


def build_title_row(item: Any, *, text: str, embedding: list[float]) -> Segment:
    """Unattached title-row ``Segment`` for ``item`` (caller adds it)."""

    return Segment(
        item_id=item.id,
        seq=TITLE_ROW_SEQ,
        start_sec=0,
        end_sec=0,
        text=text,
        embedding=embedding,
        fts=title_row_fts(text, item.lang),
        boundary_kind=TITLE_ROW_KIND,
    )
