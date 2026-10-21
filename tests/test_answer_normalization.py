"""Unit tests for server-side citation normalization (no fail-closed on
duplicate or over-cap segment selections).

See ``.trellis/tasks/archive/2026-10/10-06-fix-answer-fail-closed/design.md`` section B.
"""

from __future__ import annotations

from app.agent.answer_pipeline import (
    NormalizationReport,
    normalize_section_citations,
)


def test_normalize_is_a_no_op_under_the_cap_without_duplicates():
    rows, report = normalize_section_citations([[1, 2], [3]], cap=8)

    assert rows == [[1, 2], [3]]
    assert report == NormalizationReport(
        deduplicated=False, clamped=False, kept_distinct=3, dropped_distinct=0
    )


def test_normalize_drops_an_in_section_duplicate_keeping_first_occurrence():
    rows, report = normalize_section_citations([[1, 1, 2]], cap=8)

    assert rows == [[1, 2]]
    assert report.deduplicated is True
    assert report.clamped is False
    assert report.kept_distinct == 2
    assert report.dropped_distinct == 0


def test_normalize_keeps_a_cross_section_duplicate_counted_once():
    rows, report = normalize_section_citations([[1, 2], [2, 3]], cap=8)

    # The same segment (2) may still support both sections.
    assert rows == [[1, 2], [2, 3]]
    assert report.deduplicated is True
    assert report.clamped is False
    assert report.kept_distinct == 3
    assert report.dropped_distinct == 0


def test_normalize_round_robins_three_sections_of_five_ids_to_cap_eight():
    sections = [
        [1 + 10 * i, 2 + 10 * i, 3 + 10 * i, 4 + 10 * i, 5 + 10 * i] for i in range(3)
    ]

    rows, report = normalize_section_citations(sections, cap=8)

    assert report.clamped is True
    assert report.kept_distinct == 8
    distinct = {segment_id for row in rows for segment_id in row}
    assert len(distinct) == 8
    # Every grounded section keeps at least one citation.
    assert all(len(row) >= 1 for row in rows)
    # Each section's relative citation order survives clamping.
    for original, kept in zip(sections, rows):
        assert kept == [segment_id for segment_id in original if segment_id in distinct]


def test_normalize_round_robins_eight_sections_of_three_ids_to_one_each():
    sections = [[100 + 10 * i, 101 + 10 * i, 102 + 10 * i] for i in range(8)]

    rows, report = normalize_section_citations(sections, cap=8)

    assert report.clamped is True
    assert report.kept_distinct == 8
    assert [row for row in rows] == [[100 + 10 * i] for i in range(8)]


def test_normalize_preserves_section_and_within_section_order():
    rows, report = normalize_section_citations([[5, 3, 1], [9, 7]], cap=8)

    assert rows == [[5, 3, 1], [9, 7]]
    assert report.clamped is False


def test_normalize_empty_sections_are_harmless():
    rows, report = normalize_section_citations([[], [1], []], cap=8)

    assert rows == [[], [1], []]
    assert report.deduplicated is False
    assert report.clamped is False
    assert report.kept_distinct == 1
