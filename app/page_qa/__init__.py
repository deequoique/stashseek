"""Instant page Q&A: answer from a caller-supplied transcript without ingestion.

The package ``__init__`` stays import-light on purpose: only identifiers that
other layers (conversation history projection, request limits) need live here.
The transcript service, BM25 selection and the HTTP route are separate modules.
"""

from __future__ import annotations

import re

PAGE_QA_CONVERSATION_PREFIX = "page-"
PAGE_QA_CONVERSATION_ID_PATTERN = r"^page-[0-9a-f]{32}$"
PAGE_QA_STREAM_PATH_RE = re.compile(
    r"^/api/v1/page-qa/conversations/page-[0-9a-f]{32}/messages/stream$"
)
# Closed answer_status vocabulary persisted on page Q&A turns.
PAGE_QA_STATUS_GROUNDED = "page_grounded"
PAGE_QA_STATUS_NO_CAPTIONS = "page_no_captions"
PAGE_QA_STATUS_UNAVAILABLE = "page_unavailable"


def is_page_qa_conversation(external_conversation_id: str | None) -> bool:
    """Recognize a page Q&A thread by its client-generated conversation id."""

    return bool(external_conversation_id) and str(external_conversation_id).startswith(
        PAGE_QA_CONVERSATION_PREFIX
    )
