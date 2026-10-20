from __future__ import annotations

from app.agent.types import Citation
from app.web.citation_markers import (
    MarkerNumbering,
    position_ordinals,
    renumber_markers,
)


def _citation(segment_id: int) -> Citation:
    return Citation(
        item_id=1,
        segment_id=segment_id,
        title=f"标题{segment_id}",
        excerpt="字幕依据",
        url=f"https://example.test/video?t={segment_id}",
        start_sec=float(segment_id),
    )


def test_single_marker_becomes_its_list_position():
    assert renumber_markers("结论 [S1691]", {1691: 1}) == "结论 [^1]"


def test_several_markers_use_positions_not_segment_ids():
    text = "甲 [S30] [S10] [S20]"
    assert renumber_markers(text, {10: 2, 20: 3, 30: 1}) == "甲 [^1] [^2] [^3]"


def test_same_segment_repeated_keeps_one_ordinal():
    text = "甲 [S7]。乙 [S8]。丙 [S7]"
    assert renumber_markers(text, {7: 1, 8: 2}) == "甲 [^1]。乙 [^2]。丙 [^1]"


def test_marker_outside_the_list_is_deleted_with_its_leading_space():
    assert renumber_markers("甲 [S9] 乙", {1: 1}) == "甲 乙"
    assert renumber_markers("甲 [S9] [S1]", {1: 1}) == "甲 [^1]"
    assert renumber_markers("[S9]甲", {1: 1}) == "甲"


def test_unknown_marker_never_leaks_an_internal_id():
    result = renumber_markers("甲 [S123456] [S1]", {1: 1})
    assert "123456" not in result and "[S" not in result


def test_overlong_digit_run_is_deleted_without_raising():
    text = "甲 [S" + "9" * 5000 + "]"
    assert renumber_markers(text, {1: 1}) == "甲"


def test_text_without_markers_is_unchanged():
    assert renumber_markers("没有标记 [1] [^2]", {1: 1}) == "没有标记 [1] [^2]"


def test_sources_block_markers_are_rewritten_too():
    text = (
        "回答 [S50] [S60]\n\n来源：\n- 视频\n"
        "  - [S50] https://example.test/a?t=1 — 摘录\n"
        "  - [S60] https://example.test/a?t=2 — 摘录"
    )
    result = renumber_markers(text, {50: 1, 60: 2})
    assert "[S" not in result
    assert result.startswith("回答 [^1] [^2]\n\n来源：")
    assert "  - [^1] https://example.test/a?t=1" in result
    assert "  - [^2] https://example.test/a?t=2" in result


def test_position_ordinals_counts_unaddressable_rows_and_keeps_first_duplicate():
    assert position_ordinals([11, None, 12, 11]) == {11: 1, 12: 3}
    assert position_ordinals([0, -3, True, 5]) == {5: 4}


def test_numbering_is_contiguous_across_sections_and_reuses_known_ids():
    numbering = MarkerNumbering()
    assert numbering.extend([_citation(11), _citation(12)]) == [1, 2]
    assert numbering.extend([_citation(13), _citation(11)]) == [3, 1]
    assert dict(numbering.ordinals) == {11: 1, 12: 2, 13: 3}
    assert numbering.rewrite("甲 [S13] 乙 [S11] [S99]") == "甲 [^3] 乙 [^1]"


def test_ordered_puts_unnumbered_last_and_keeps_original_order():
    numbering = MarkerNumbering()
    numbering.extend([_citation(2), _citation(1)])
    final = [_citation(5), _citation(1), _citation(4), _citation(2)]
    ordered = numbering.ordered(final)
    assert [item.segment_id for item in ordered] == [2, 1, 5, 4]


def test_ordered_supports_wrapped_items_with_a_key():
    numbering = MarkerNumbering()
    numbering.extend([_citation(2), _citation(1)])
    pairs = [(_citation(1), "b"), (_citation(2), "a"), (_citation(9), "z")]
    ordered = numbering.ordered(pairs, key=lambda pair: pair[0])
    assert [label for _, label in ordered] == ["a", "b", "z"]
