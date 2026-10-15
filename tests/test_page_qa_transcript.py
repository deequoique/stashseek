"""Pure unit tests for page Q&A paragraph merging, BM25 and context selection."""

from __future__ import annotations

import pytest

from app.browser_capture import CaptureChapter
from app.connectors.base import Cue
from app.page_qa.bm25 import BM25Index, tokenize
from app.page_qa.transcript import (
    PARAGRAPH_LABEL_TOKENS,
    build_paragraphs,
    estimate_tokens,
    format_timestamp,
    paragraph_tokens,
    render_context,
    render_paragraph,
    script_mismatch,
    select_context,
)


def cues(count: int, *, step: float = 10.0, text=lambda i: f"cue number {i} talks about topic {i}"):
    return [Cue(i * step, i * step + step, text(i)) for i in range(count)]


# --- BM25 ----------------------------------------------------------------


def test_tokenize_lowercases_latin_words_and_splits_cjk_into_bigrams():
    assert tokenize("Hello, World 42!") == ["hello", "world", "42"]
    assert tokenize("机器学习") == ["机器", "器学", "学习"]
    assert tokenize("学") == ["学"]
    # Mixed script: CJK bigrams first, then the Latin words.
    assert tokenize("用 GPU 训练") == ["用", "训练", "gpu"]
    assert tokenize("") == []


def test_bm25_ranks_the_matching_document_first_and_skips_zero_scores():
    index = BM25Index(
        [
            "the weather is nice today",
            "gradient descent minimizes the loss function",
            "a recipe for tomato soup",
        ]
    )

    ranked = index.top_k("what is gradient descent", 5)

    assert ranked[0][0] == 1
    assert [position for position, _ in ranked] == [1, 0]  # "is" also matches doc 0
    assert index.top_k("zzzz unrelated", 5) == []
    assert index.top_k("gradient", 0) == []


def test_bm25_handles_chinese_bigrams_and_is_deterministic_on_ties():
    documents = ["今天讨论神经网络的训练", "今天吃饭", "神经网络的训练", "完全无关的内容"]
    index = BM25Index(documents)

    first = index.top_k("神经网络训练", 3)
    second = BM25Index(documents).top_k("神经网络训练", 3)

    assert first == second
    assert {position for position, _ in first[:2]} == {0, 2}

    identical = BM25Index(["same words here", "same words here", "same words here"])
    assert [position for position, _ in identical.top_k("same words", 3)] == [0, 1, 2]


def test_bm25_on_an_empty_corpus_is_safe():
    assert BM25Index([]).top_k("anything", 3) == []
    assert BM25Index([""]).top_k("anything", 3) == []


# --- paragraph merging ----------------------------------------------------


def test_paragraph_closes_at_the_cue_boundary_after_thirty_seconds():
    paragraphs = build_paragraphs(cues(7, step=10.0))

    assert [(p.index, p.start_sec, p.end_sec) for p in paragraphs] == [
        (1, 0.0, 30.0),
        (2, 30.0, 60.0),
        (3, 60.0, 70.0),
    ]
    assert paragraphs[0].text == "cue number 0 talks about topic 0 cue number 1 talks about topic 1 cue number 2 talks about topic 2"


def test_paragraph_splits_when_the_gap_exceeds_four_seconds_only():
    split = build_paragraphs(
        [Cue(0, 5, "alpha"), Cue(9.5, 12, "beta"), Cue(12, 14, "gamma")]
    )
    joined = build_paragraphs([Cue(0, 5, "alpha"), Cue(9.0, 12, "beta")])

    assert [(p.start_sec, p.end_sec, p.text) for p in split] == [
        (0, 5, "alpha"),
        (9.5, 14, "beta gamma"),
    ]
    assert len(joined) == 1  # a gap of exactly 4 s does not split


def test_paragraph_text_joins_cjk_without_spaces_and_latin_with_spaces():
    paragraphs = build_paragraphs(
        [Cue(0, 2, "今天我们"), Cue(2, 4, "讲机器学习"), Cue(4, 6, "with Python"), Cue(6, 8, "很有用")]
    )

    assert paragraphs[0].text == "今天我们讲机器学习 with Python 很有用"


def test_empty_input_produces_no_paragraphs():
    assert build_paragraphs([]) == []


def test_timestamp_labels():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(330.9) == "05:30"
    assert format_timestamp(3930) == "1:05:30"
    paragraph = build_paragraphs([Cue(330, 335, "hello")])[0]
    assert render_paragraph(paragraph) == "[P1 05:30] hello"


# --- token estimate -------------------------------------------------------


def test_token_estimate_is_pessimistic_per_script():
    assert estimate_tokens("一二三四五") == 5
    assert estimate_tokens("one two three four five") == 7  # ceil(5 * 1.4)
    assert estimate_tokens("训练 model 参数") == 6  # ceil(4 CJK + 1.4)
    paragraph = build_paragraphs([Cue(0, 1, "one two three four five")])[0]
    assert paragraph_tokens(paragraph) == 7 + PARAGRAPH_LABEL_TOKENS


def test_script_mismatch_compares_question_script_with_caption_language():
    assert script_mismatch("what is backpropagation", "zh-Hans")
    assert script_mismatch("什么是反向传播", "en")
    assert not script_mismatch("什么是反向传播", "zh")
    assert not script_mismatch("what is backprop", "en-US")
    assert not script_mismatch("什么是反向传播", None)
    assert not script_mismatch("12345 ???", "en")


# --- context selection ----------------------------------------------------


def long_transcript():
    # 3 hours of 10 s cues -> 360 paragraphs; the keyword appears only far
    # from the playback position.
    def text(i: int) -> str:
        if 600 <= i < 603:
            return "the transformer attention mechanism explained in detail"
        return f"generic filler sentence {i} about nothing in particular here"

    return build_paragraphs(cues(1080, text=text))


def test_small_transcript_is_sent_whole_in_full_mode():
    paragraphs = build_paragraphs(cues(12))

    selection = select_context(
        paragraphs,
        query="topic",
        playback_sec=None,
        full_budget=100_000,
        window_budget=10,
    )

    assert selection.mode == "full"
    assert selection.paragraphs == tuple(paragraphs)
    assert selection.outline == ()
    assert selection.selected_tokens == selection.total_tokens


def test_window_mode_keeps_playback_window_and_bm25_hits_ordered_by_time():
    paragraphs = long_transcript()
    playback = 9000.0  # 2.5 h in

    selection = select_context(
        paragraphs,
        query="transformer attention",
        playback_sec=playback,
        full_budget=1_000,
        window_budget=3_000,
        chapters=[CaptureChapter(title="Intro", start_sec=0), CaptureChapter(title="Attention", start_sec=1800)],
    )

    assert selection.mode == "window"
    indices = [p.index for p in selection.paragraphs]
    assert indices == sorted(indices)
    # Every paragraph overlapping playback +/- 300 s is present.
    for paragraph in paragraphs:
        if paragraph.end_sec >= playback - 300 and paragraph.start_sec <= playback + 300:
            assert paragraph.index in indices
    # The far-away keyword paragraphs came from BM25.
    assert selection.bm25_indices
    assert all(6000 <= paragraphs[i - 1].start_sec < 6030 for i in selection.bm25_indices)
    assert selection.selected_tokens <= 3_000
    assert selection.outline == ("00:00 Intro", "30:00 Attention")
    assert selection.total_paragraphs == len(paragraphs)


def test_window_budget_drops_lowest_scored_bm25_hits_before_playback_paragraphs():
    paragraphs = long_transcript()
    playback = 9000.0
    playback_only = select_context(
        paragraphs, query="", playback_sec=playback, full_budget=1_000, window_budget=100_000
    )
    playback_cost = sum(paragraph_tokens(p) for p in playback_only.paragraphs)

    selection = select_context(
        paragraphs,
        query="transformer attention",
        playback_sec=playback,
        full_budget=1_000,
        window_budget=playback_cost + 5,  # no room for any BM25 paragraph
    )

    assert selection.bm25_indices == ()
    assert [p.index for p in selection.paragraphs] == [p.index for p in playback_only.paragraphs]
    assert selection.selected_tokens <= playback_cost + 5


def test_window_budget_shrinks_the_playback_window_from_the_far_end():
    paragraphs = long_transcript()
    selection = select_context(
        paragraphs, query="", playback_sec=9000.0, full_budget=1_000, window_budget=150
    )

    assert selection.selected_tokens <= 150
    assert selection.paragraphs
    nearest = min(selection.paragraphs, key=lambda p: abs(p.start_sec - 9000.0))
    assert nearest.start_sec <= 9000.0 <= nearest.end_sec + 30


def test_window_mode_without_playback_or_keyword_hits_samples_the_whole_video():
    paragraphs = long_transcript()

    selection = select_context(
        paragraphs,
        query="zzzqqq",
        playback_sec=None,
        full_budget=1_000,
        window_budget=2_000,
    )

    assert selection.paragraphs
    assert selection.selected_tokens <= 2_000
    starts = [p.start_sec for p in selection.paragraphs]
    assert min(starts) < 1_000 and max(starts) > 9_000  # spans the video


def test_selection_is_deterministic():
    paragraphs = long_transcript()
    kwargs = dict(query="transformer attention", playback_sec=4200.0, full_budget=1_000, window_budget=2_500)

    assert select_context(paragraphs, **kwargs) == select_context(paragraphs, **kwargs)


def test_chapters_must_use_start_sec_not_start_time():
    paragraphs = long_transcript()
    with pytest.raises(AttributeError):
        select_context(
            paragraphs,
            query="x",
            playback_sec=0.0,
            full_budget=10,
            window_budget=2_000,
            chapters=[{"title": "yt-dlp shape", "start_time": 0}],  # type: ignore[list-item]
        )
    # The capture shape (start_sec / optional end_sec) is what the route passes.
    selection = select_context(
        paragraphs,
        query="x",
        playback_sec=0.0,
        full_budget=10,
        window_budget=2_000,
        chapters=[CaptureChapter(title="Capture shape", start_sec=0, end_sec=60)],
    )
    assert selection.outline == ("00:00 Capture shape",)


def test_render_context_marks_omitted_ranges_and_prefixes_the_outline():
    paragraphs = long_transcript()
    selection = select_context(
        paragraphs,
        query="transformer attention",
        playback_sec=9000.0,
        full_budget=1_000,
        window_budget=3_000,
        chapters=[CaptureChapter(title="Intro", start_sec=0)],
    )

    rendered = render_context(selection)

    assert rendered.startswith("Chapters:\n00:00 Intro\n\n[P")
    assert "\n(...)\n" in rendered
    assert render_paragraph(selection.paragraphs[0]) in rendered
