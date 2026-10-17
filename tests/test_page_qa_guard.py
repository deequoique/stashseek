"""Streaming guard, rate limiter and settings for page Q&A."""

from __future__ import annotations

import pytest

from app.agent.answer_validation import NaturalAnswerValidationError
from app.config import Settings
from app.page_qa.guard import MAX_CITED_PARAGRAPHS, PageAnswerGuard
from app.page_qa.rate_limit import SlidingWindowRateLimiter


def run(chunks, valid=frozenset({1, 2, 3, 12})):
    guard = PageAnswerGuard(valid)
    emitted = [guard.feed(chunk) for chunk in chunks]
    emitted.append(guard.flush())
    return guard, emitted


def splits(text: str):
    """Every two-way split plus a character-by-character delivery."""

    for position in range(1, len(text)):
        yield [text[:position], text[position:]]
    yield list(text)


# --- markers ----------------------------------------------------------------------


def test_valid_markers_become_library_style_s_markers_in_first_use_order():
    guard, emitted = run(["Rates matter [P2] and ", "large ones diverge [P3][P1]."])

    assert guard.text == "Rates matter [S2] and large ones diverge [S3] [S1]."
    assert "".join(emitted) == guard.text
    assert guard.citation_ids == (2, 3, 1)


@pytest.mark.parametrize(
    "text",
    [
        "A claim [P2] and another [P12 05:30]. Done [P3, P1].",
        "前面的话[P1]，后面的话[P2][P3]。",
        "Unknown [P99] dropped, model-made [S2] dropped, [Summary] dropped, [Plan] kept.",
    ],
)
def test_every_chunking_yields_the_same_text_as_unsplit_delivery(text):
    reference, _ = run([text])

    for chunks in splits(text):
        guard, emitted = run(chunks)
        assert guard.text == reference.text, chunks
        assert guard.citation_ids == reference.citation_ids, chunks
        assert "".join(emitted).strip() == reference.text


def test_unknown_foreign_and_model_authored_citation_groups_are_dropped():
    guard, _ = run(["Fine [P99] text [S2] more [Summary] and [Plan] last [Pabc]."])

    assert guard.text == "Fine text more and [Plan] last [Pabc]."
    assert guard.citation_ids == ()


def test_timestamp_copied_from_the_label_and_comma_lists_are_understood():
    guard, _ = run(["See [P12 05:30] and [P1, P2] and [P3 1:02:03]."])

    assert guard.citation_ids == (12, 1, 2, 3)
    assert guard.text == "See [S12] and [S1] [S2] and [S3]."


def test_only_the_first_eight_distinct_paragraphs_become_citations():
    valid = frozenset(range(1, 13))
    text = "".join(f"x[P{n}]" for n in range(1, 13))
    guard, _ = run([text], valid=valid)

    assert guard.citation_ids == tuple(range(1, MAX_CITED_PARAGRAPHS + 1))
    assert "[S9]" not in guard.text
    # Re-citing an already cited paragraph stays allowed.
    again, _ = run([text + "again[P1]"], valid=valid)
    assert again.text.endswith("again [S1]")


def test_partial_marker_is_held_until_closed_and_dropped_if_dangling():
    guard = PageAnswerGuard({1})

    assert guard.feed("claim [P") == "claim "
    assert guard.feed("1") == ""
    assert guard.feed("] done") == "[S1] done"  # "claim " already ends in a space
    dangling = PageAnswerGuard({1})
    assert dangling.feed("tail [P1") == "tail "
    assert dangling.flush() == ""
    assert dangling.text == "tail"


def test_dropping_a_marker_after_a_space_does_not_leave_a_double_space():
    guard, _ = run(["alpha [P99] beta"])

    assert guard.text == "alpha beta"


# --- unsafe text ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "See https://evil.example/x now",
        "Visit www.evil.example today",
        "ftp://x.example",
    ],
)
def test_urls_are_rejected_at_every_chunk_boundary(text):
    for chunks in splits(text):
        with pytest.raises(NaturalAnswerValidationError):
            run(chunks)


@pytest.mark.parametrize(
    "text",
    ["Answer.\n来源：\n- x", "Answer.\nSources:\n- x", "Answer.\n## References:\n- x", "答案。\n参考来源：\n- x"],
)
def test_source_block_headings_are_rejected_at_every_chunk_boundary(text):
    for chunks in splits(text):
        with pytest.raises(NaturalAnswerValidationError):
            run(chunks)


def test_ordinary_prose_with_source_like_words_is_not_blocked():
    text = "The source of the bug is in the reference implementation.\nSource code is open.\nReferences to it are rare."

    for chunks in splits(text):
        guard, _ = run(chunks)
        assert guard.text == text


def test_a_trailing_bare_heading_word_is_rejected_when_the_stream_ends():
    with pytest.raises(NaturalAnswerValidationError):
        run(["Fine answer.\nSources"])


def test_held_url_prefix_is_released_when_it_turns_out_to_be_ordinary_text():
    guard, emitted = run(["we saw the wh", "ale in the ht", "ml spec"])

    assert guard.text == "we saw the whale in the html spec"
    assert emitted[0] == "we saw the w"  # the trailing "h" could still start "http://"


# --- rate limiter -------------------------------------------------------------------


def test_sliding_window_limit_retry_after_and_per_key_isolation():
    now = [1000.0]
    limiter = SlidingWindowRateLimiter(3, 600, clock=lambda: now[0])

    assert [limiter.acquire("a") for _ in range(3)] == [None, None, None]
    assert limiter.acquire("a") == 600
    assert limiter.acquire("b") is None
    now[0] += 100
    assert limiter.acquire("a") == 500
    now[0] += 500
    # The first three hits aged out: a fresh window admits three more.
    assert [limiter.acquire("a") for _ in range(3)] == [None, None, None]
    assert limiter.acquire("a") == 600


def test_limiter_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(0, 600)
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(1, 0)


# --- settings -------------------------------------------------------------------------


def settings(**overrides):
    return Settings(database_url="sqlite://", notebook_agent_env="development", **overrides)


def test_page_qa_defaults(monkeypatch):
    for name in (
        "PAGE_QA_ENABLED",
        "PAGE_QA_MAX_REQUEST_BYTES",
        "PAGE_QA_FULL_CONTEXT_TOKENS",
        "PAGE_QA_WINDOW_CONTEXT_TOKENS",
        "PAGE_QA_RATE_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)

    current = settings()

    assert current.page_qa_enabled is True
    assert current.page_qa_max_request_bytes == 2_000_000
    assert current.page_qa_full_context_tokens == 24_000
    assert current.page_qa_window_context_tokens == 12_000
    assert current.page_qa_rate_limit == 30


def test_page_qa_environment_overrides(monkeypatch):
    monkeypatch.setenv("PAGE_QA_ENABLED", "false")
    monkeypatch.setenv("PAGE_QA_MAX_REQUEST_BYTES", "1000000")
    monkeypatch.setenv("PAGE_QA_RATE_LIMIT", "5")

    current = settings()

    assert current.page_qa_enabled is False
    assert current.page_qa_max_request_bytes == 1_000_000
    assert current.page_qa_rate_limit == 5


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"page_qa_max_request_bytes": 0}, "PAGE_QA_MAX_REQUEST_BYTES"),
        (
            {"page_qa_max_request_bytes": 6_000_000, "browser_companion_max_request_bytes": 5_500_000},
            "PAGE_QA_MAX_REQUEST_BYTES",
        ),
        ({"page_qa_full_context_tokens": 0}, "PAGE_QA_FULL_CONTEXT_TOKENS"),
        ({"page_qa_window_context_tokens": 0}, "PAGE_QA_FULL_CONTEXT_TOKENS"),
        ({"page_qa_rate_limit": 0}, "PAGE_QA_RATE_LIMIT"),
        (
            {"page_qa_full_context_tokens": 1_000, "page_qa_window_context_tokens": 2_000},
            "PAGE_QA_WINDOW_CONTEXT_TOKENS",
        ),
    ],
)
def test_page_qa_settings_are_validated(overrides, message):
    with pytest.raises(ValueError, match=message):
        settings(**overrides)


def test_page_qa_enabled_rejects_a_non_boolean(monkeypatch):
    monkeypatch.setenv("PAGE_QA_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="PAGE_QA_ENABLED"):
        settings()
