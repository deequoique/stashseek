"""CJK tokenization for the Chinese lexical (full-text) retrieval path.

Neon has no Chinese parser (no zhparser / pg_jieba), so Chinese text is turned
into whitespace-separated *character bigrams* before PostgreSQL's ``simple``
configuration sees it. The same function builds both the stored ``tsvector``
(ingestion and reindex) and the search ``tsquery``, which keeps the two sides
consistent: the PostgreSQL parser only has to split on spaces.

``电脑价格`` -> ``电脑 脑价 价格``; a lone CJK character stays a unigram; ASCII
words (``DDR5``, ``5070``) are kept whole and lower-cased.
"""

from __future__ import annotations

import re

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

# Same CJK range as the query-language check in ``app.retrieval.search``.
_TOKEN_RE = re.compile(r"([㐀-鿿]+)|([A-Za-z0-9]+)")

# Bigrams containing a sentence-final particle ("的视", "吗？") are noise.
_PARTICLES = frozenset("的了吗呢吧啊呀")

QUERY_TOKEN_LIMIT = 64


def cjk_tokens(text: str, *, max_tokens: int | None = None) -> list[str]:
    """Split ``text`` into deduplicated bigram / ASCII-word tokens.

    Tokens appear in order of first occurrence. ``max_tokens`` caps the result
    (used for queries); stored document text is tokenized without a cap so a
    long segment keeps every term. Returns ``[]`` when there is nothing to
    index (empty text, punctuation only, particle-only runs).
    """

    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> bool:
        if token in seen:
            return False
        seen.add(token)
        tokens.append(token)
        return max_tokens is not None and len(tokens) >= max_tokens

    for match in _TOKEN_RE.finditer(text):
        cjk_run, ascii_word = match.groups()
        if ascii_word is not None:
            if add(ascii_word.lower()):
                break
            continue
        if len(cjk_run) == 1:
            if add(cjk_run):
                break
            continue
        full = False
        for index in range(len(cjk_run) - 1):
            bigram = cjk_run[index : index + 2]
            if bigram[0] in _PARTICLES or bigram[1] in _PARTICLES:
                continue
            if add(bigram):
                full = True
                break
        if full:
            break
    return tokens


def zh_tsvector(text: str) -> ColumnElement:
    """SQL expression for the stored ``fts`` value of a Chinese segment."""

    return func.to_tsvector("simple", " ".join(cjk_tokens(text)))


def zh_tsquery(query: str) -> ColumnElement | None:
    """SQL ``tsquery`` matching any query token, or ``None`` if none remain.

    Tokens are joined with ``OR`` for ``websearch_to_tsquery``: every token is
    an ASCII alphanumeric word or CJK characters, so no websearch operator can
    be injected, and ``ts_rank_cd`` ranks segments matching more tokens higher.
    """

    tokens = cjk_tokens(query, max_tokens=QUERY_TOKEN_LIMIT)
    if not tokens:
        return None
    return func.websearch_to_tsquery("simple", " OR ".join(tokens))
