from __future__ import annotations

import collections
import json
from pathlib import Path

from evals.meeting_gold.agent_smoke import (
    CATEGORY_COMPLETED,
    CATEGORY_COMPOSER_FALLBACK,
    CATEGORY_OTHER_FAILED,
    CATEGORY_STREAM_GUARD_WHITESPACE,
    CATEGORY_STREAM_PLAN_OVER_CITE,
    categorize_failure,
)

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "meeting_gold"
    / "prodpath-rerun-20261006.cases.json"
)


def test_categorize_failure_completed_case() -> None:
    assert categorize_failure({"answer_status": "completed"}) == CATEGORY_COMPLETED


def test_categorize_failure_stream_guard_whitespace() -> None:
    record = {
        "answer_status": "failed",
        "guard_rejections": [{"rule": "answer text must not be empty"}],
        "stream_failures": [{"message": "unsafe streamed text"}],
        "section_aborted": ["provider_failure"],
        "citation_failures": [],
    }
    assert categorize_failure(record) == CATEGORY_STREAM_GUARD_WHITESPACE


def test_categorize_failure_stream_plan_over_cite() -> None:
    record = {
        "answer_status": "failed",
        "guard_rejections": [],
        "stream_failures": [
            {
                "exception": "UnexpectedModelBehavior",
                "message": "Exceeded maximum output retries (0)",
                "cause": "ToolRetryError: too many segments",
            }
        ],
        "section_aborted": [],
        "citation_failures": [],
    }
    assert categorize_failure(record) == CATEGORY_STREAM_PLAN_OVER_CITE


def test_categorize_failure_composer_fallback() -> None:
    record = {
        "answer_status": "failed",
        "guard_rejections": [],
        "stream_failures": [],
        "section_aborted": [],
        "citation_failures": ["too_many_segments", "duplicate_citation", "too_many_segments"],
    }
    assert categorize_failure(record) == CATEGORY_COMPOSER_FALLBACK


def test_categorize_failure_falls_back_to_other() -> None:
    record = {
        "answer_status": "failed",
        "guard_rejections": [],
        "stream_failures": [],
        "section_aborted": [],
        "citation_failures": [],
    }
    assert categorize_failure(record) == CATEGORY_OTHER_FAILED


def test_categorize_failure_reproduces_20261006_research_breakdown() -> None:
    """R8 acceptance spot-check against the frozen pre-fix scratch results.

    findings-20261006.md records 18/30 completed and a 12/30 failure split of
    6 stream-guard-whitespace, 3 stream-plan-over-cite, 3 composer-fallback.
    """

    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 30

    counts = collections.Counter(categorize_failure(row) for row in rows)
    assert counts[CATEGORY_COMPLETED] == 18
    assert counts[CATEGORY_STREAM_GUARD_WHITESPACE] == 6
    assert counts[CATEGORY_STREAM_PLAN_OVER_CITE] == 3
    assert counts[CATEGORY_COMPOSER_FALLBACK] == 3
    assert counts[CATEGORY_OTHER_FAILED] == 0
    assert sum(counts.values()) == 30
