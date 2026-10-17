"""Streaming guard for page Q&A answers.

The model cites transcript paragraphs as ``[P<n>]``.  This guard turns each
valid marker into the ``[S<n>]`` convention library answers already use (see
``ResponseEnvelope.project`` / ``_render_grounded_text``), so the shared
Markdown renderer and ``来源：`` projection work unchanged, and it releases only
text that is safe to show: no URLs, no source-block headings, and only markers
that cite a paragraph the model was actually given (first eight distinct).

Chunk boundaries are handled by retaining the shortest unsafe-to-release tail
(a partial ``[P1``, a partial ``http://``, or a line that may still become a
``Sources:`` heading), so nothing forbidden is ever exposed split across deltas.
"""

from __future__ import annotations

import re
from typing import Literal

from app.agent.answer_validation import NaturalAnswerValidationError

MAX_CITED_PARAGRAPHS = 8
_MAX_GROUP_CHARS = 48

_URL_PATTERN = re.compile(r"(?:https?://|ftp://|www\.)", re.IGNORECASE)
_URL_PREFIXES = ("http://", "https://", "ftp://", "www.")
_HEADING_KEYWORDS = ("sources", "source", "references", "reference", "来源", "参考来源")
_HEADING_LINE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:sources?|references?|来源|参考来源)[ \t]*(?:[:：]|$)",
    re.IGNORECASE,
)
_HEADING_WITH_COLON = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:sources?|references?|来源|参考来源)[ \t]*[:：]",
    re.IGNORECASE,
)
_LINE_LEAD = re.compile(r"[ \t]*(?:#{0,6}[ \t]*)")

# One cited paragraph: ``P12`` optionally followed by the label timestamp the
# model may copy from ``[P12 05:30]``.
_P_ID = r"[Pp]\s*(\d+)(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
_P_GROUP = re.compile(rf"\s*{_P_ID}(?:\s*[,，、;；]\s*{_P_ID})*\s*")
_P_LIKE = re.compile(r"\s*[Pp]\s*\d")
_S_LIKE = re.compile(r"\s*[Ss]")
_GROUP_START = re.compile(r"\s*[PpSs]")


def _unsafe(message: str) -> NaturalAnswerValidationError:
    return NaturalAnswerValidationError(message)


class PageAnswerGuard:
    """Convert and validate a streamed answer chunk by chunk."""

    def __init__(self, valid_ids: frozenset[int] | set[int] = frozenset()) -> None:
        self._valid_ids = frozenset(valid_ids)
        self._pending = ""
        self._emitted: list[str] = []
        self._line_tail = ""  # emitted text of the current (incomplete) line
        self._cited: list[int] = []
        # A dropped marker after a space must not leave a double space behind.
        self._swallow_space = False

    # -- public API ---------------------------------------------------------

    def feed(self, delta: str) -> str:
        if not isinstance(delta, str) or not delta:
            return ""
        self._pending += delta
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    @property
    def text(self) -> str:
        return "".join(self._emitted).strip()

    @property
    def citation_ids(self) -> tuple[int, ...]:
        """Cited paragraph numbers in first-use order (at most eight)."""

        return tuple(self._cited)

    # -- internals ------------------------------------------------------------

    def _drain(self, *, final: bool) -> str:
        out: list[str] = []
        while self._pending:
            start = self._pending.find("[")
            if start == -1:
                self._pending = self._release(
                    out, self._pending, "final" if final else "stream"
                )
                break
            if start > 0:
                # Plain text before a bracket cannot be continued by it.
                self._release(out, self._pending[:start], "bracket")
                self._pending = self._pending[start:]
                continue
            group_end = self._group_end(self._pending)
            if group_end is not None:
                group = self._pending[: group_end + 1]
                self._pending = self._pending[group_end + 1 :]
                self._emit_group(out, group)
                continue
            tail = self._pending
            incomplete = "[" not in tail[1:] and "]" not in tail and "\n" not in tail
            if incomplete and self._could_become_group(tail):
                if final:
                    self._pending = ""  # drop a dangling partial marker
                break
            self._release(out, "[", "bracket")
            self._pending = self._pending[1:]
        return "".join(out)

    @staticmethod
    def _group_end(pending: str) -> int | None:
        close = pending.find("]")
        if close == -1:
            return None
        for stop in ("[", "\n"):
            position = pending.find(stop, 1)
            if position != -1 and position < close:
                return None
        return close

    @staticmethod
    def _could_become_group(pending: str) -> bool:
        content = pending[1:]
        return len(pending) <= _MAX_GROUP_CHARS and (
            not content.strip() or _GROUP_START.match(content) is not None
        )

    def _emit_group(self, out: list[str], group: str) -> None:
        content = group[1:-1]
        if _P_GROUP.fullmatch(content):
            markers: list[str] = []
            for raw_id in re.findall(r"[Pp]\s*(\d+)", content):
                paragraph_id = int(raw_id)
                if paragraph_id not in self._valid_ids:
                    continue
                if paragraph_id not in self._cited:
                    if len(self._cited) >= MAX_CITED_PARAGRAPHS:
                        continue
                    self._cited.append(paragraph_id)
                marker = f"[S{paragraph_id}]"
                if marker not in markers:
                    markers.append(marker)
            if markers:
                lead = " " if self._emitted and self._last_char() not in " \t\n" else ""
                self._append(out, lead + " ".join(markers))
            else:
                self._swallow_space = self._last_char() == " "
            return
        if _S_LIKE.match(content) or _P_LIKE.match(content):
            # Model-authored citation-looking text is never shown.
            self._swallow_space = self._last_char() == " "
            return
        self._release(out, group, "bracket")

    def _last_char(self) -> str:
        for part in reversed(self._emitted):
            if part:
                return part[-1]
        return ""

    def _append(self, out: list[str], text: str) -> None:
        if not text:
            return
        if self._swallow_space:
            self._swallow_space = False
            if text.startswith(" "):
                text = text[1:]
                if not text:
                    return
        out.append(text)
        self._emitted.append(text)
        if "\n" in text:
            self._line_tail = text.rsplit("\n", 1)[1]
        else:
            self._line_tail += text

    def _release(
        self,
        out: list[str],
        text: str,
        mode: Literal["stream", "bracket", "final"],
    ) -> str:
        """Validate and emit plain ``text``; return the tail that must wait.

        ``stream`` retains a tail that later text could turn into a URL or a
        source heading; ``bracket`` (a marker group follows) and ``final``
        release everything, and only ``final`` treats a bare heading word on
        the last line as a heading.
        """

        if not text:
            return ""
        if _URL_PATTERN.search(text):
            raise _unsafe("answer must not contain model-authored URLs")
        lines = text.split("\n")
        for position, line in enumerate(lines[:-1]):
            full_line = (self._line_tail + line) if position == 0 else line
            if _HEADING_LINE.search(full_line):
                raise _unsafe("answer must not contain a source block")
        last_line = (self._line_tail + lines[-1]) if len(lines) == 1 else lines[-1]
        retained = 0
        if mode == "stream":
            retained = self._url_hold(text)
            if self._could_be_heading_prefix(last_line):
                retained = max(retained, len(lines[-1]))
        if retained == 0:
            heading = _HEADING_LINE if mode == "final" else _HEADING_WITH_COLON
            if heading.search(last_line):
                raise _unsafe("answer must not contain a source block")
        elif _HEADING_WITH_COLON.search(last_line):
            raise _unsafe("answer must not contain a source block")
        retained = min(retained, len(text))
        releasable = text[: len(text) - retained]
        self._append(out, releasable)
        return text[len(releasable) :]

    @staticmethod
    def _url_hold(text: str) -> int:
        lowered = text.lower()
        longest = 0
        for prefix in _URL_PREFIXES:
            for length in range(1, min(len(prefix), len(lowered) + 1)):
                if lowered.endswith(prefix[:length]):
                    longest = max(longest, length)
        return longest

    @staticmethod
    def _could_be_heading_prefix(line: str) -> bool:
        match = _LINE_LEAD.match(line)
        rest = line[match.end() :].lower() if match else line.lower()
        return any(keyword.startswith(rest) for keyword in _HEADING_KEYWORDS)
