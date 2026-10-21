"""Browser-facing citation marker numbering.

The Agent writes ``[S<segment_id>]`` markers after validating the Citation
allow-list.  Those identifiers are internal: the browser must never learn them.
Before an answer leaves the Web surface, each marker is rewritten to
``[^<n>]`` where ``n`` is the 1-based position of that citation in the public
``citations`` list returned with the same answer.  The browser can therefore
resolve a marker with ``citations[n - 1]`` and nothing else.

Rules shared by the non-streaming, streaming, and history paths:

* The ordinal is the citation's position in the public list (after any
  citation the browser projection dropped has been removed).
* A segment that appears several times in the text always gets the same
  ordinal.
* A marker whose segment is not in the public list is deleted, together with
  one preceding space, rather than leaking an internal identifier.
* ``[^n]`` is used instead of ``[n]`` so it can never collide with a model
  written ``[1]`` and is never parsed as a Markdown link or reference.

This module is pure: it has no database or framework dependency.  MCP and
LangBot text never passes through it and keeps the ``[S<id>]`` form.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TypeVar

from app.agent.types import Citation

MARKER_RE = re.compile(r"\[S(\d+)\]")

# One optional leading space is consumed together with a deleted marker so that
# removing "text [S9] more" leaves "text more" and not "text  more".
_MARKER_WITH_SPACE_RE = re.compile(" ?" + MARKER_RE.pattern)

# int() of a pathologically long digit run raises ValueError; no real segment
# identifier is anywhere near this long.
_MAX_ID_DIGITS = 18

_T = TypeVar("_T")


def position_ordinals(segment_ids: Iterable[int | None]) -> dict[int, int]:
    """Map each segment ID to its 1-based position in a public citation list.

    ``None`` entries (a public citation without an addressable segment) still
    consume a position so that ordinal == list position.  When the same
    segment appears twice the first position wins.
    """

    ordinals: dict[int, int] = {}
    for position, segment_id in enumerate(segment_ids, start=1):
        if type(segment_id) is int and segment_id > 0:
            ordinals.setdefault(segment_id, position)
    return ordinals


def renumber_markers(text: str, ordinals: Mapping[int, int]) -> str:
    """Rewrite ``[S<id>]`` markers to ``[^<ordinal>]``.

    Markers whose ID is not in ``ordinals`` are removed along with one
    preceding space.
    """

    if "[S" not in text:
        return text

    def replace(match: re.Match[str]) -> str:
        digits = match.group(1)
        ordinal = ordinals.get(int(digits)) if len(digits) <= _MAX_ID_DIGITS else None
        if ordinal is None:
            return ""
        lead = " " if match.group(0).startswith(" ") else ""
        return f"{lead}[^{ordinal}]"

    return _MARKER_WITH_SPACE_RE.sub(replace, text)


class MarkerNumbering:
    """Accumulate ordinals across the sections of one streamed answer.

    ``extend`` is called once per public section with the citations the browser
    projection kept, in the order they are published.  A segment seen before
    keeps its earlier ordinal (the public stream already rejects a segment that
    repeats across sections, so this is defensive).
    """

    def __init__(self) -> None:
        self._ordinals: dict[int, int] = {}

    @property
    def ordinals(self) -> Mapping[int, int]:
        return self._ordinals

    def extend(self, citations: Sequence[Citation]) -> list[int]:
        """Number a batch of citations and return their ordinals in order."""

        numbers: list[int] = []
        for citation in citations:
            number = self._ordinals.get(citation.segment_id)
            if number is None:
                number = len(self._ordinals) + 1
                self._ordinals[citation.segment_id] = number
            numbers.append(number)
        return numbers

    def rewrite(self, delta: str) -> str:
        """Apply the accumulated ordinals to one text fragment."""

        return renumber_markers(delta, self._ordinals)

    def ordered(
        self,
        items: Sequence[_T],
        *,
        key: Callable[[_T], Citation] = lambda item: item,  # type: ignore[assignment,return-value]
    ) -> list[_T]:
        """Order items so numbered citations come first, by ordinal.

        Items whose citation has no ordinal follow in their original order.
        ``key`` extracts the ``Citation`` when ``items`` wraps it.
        """

        numbered: list[tuple[int, int, _T]] = []
        rest: list[_T] = []
        for index, item in enumerate(items):
            number = self._ordinals.get(key(item).segment_id)
            if number is None:
                rest.append(item)
            else:
                numbered.append((number, index, item))
        numbered.sort(key=lambda row: (row[0], row[1]))
        return [item for _, _, item in numbered] + rest
