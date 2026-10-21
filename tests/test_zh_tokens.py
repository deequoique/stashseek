"""CJK bigram tokenization for the Chinese full-text path."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.retrieval.zh_tokens import (
    QUERY_TOKEN_LIMIT,
    cjk_tokens,
    zh_tsquery,
    zh_tsvector,
)


PARTICLES = set("的了吗呢吧啊呀")


def distinct_chars(count: int) -> str:
    """``count`` distinct CJK characters, none of them a skipped particle."""

    chars = (chr(0x4E00 + n) for n in range(0x5000))
    return "".join(c for c in chars if c not in PARTICLES)[:count]


def compiled(expr):
    compiled_expr = expr.compile(dialect=postgresql.dialect())
    return str(compiled_expr), compiled_expr.params


def test_cjk_run_becomes_overlapping_bigrams():
    assert cjk_tokens("电脑价格") == ["电脑", "脑价", "价格"]


def test_single_cjk_character_run_stays_a_unigram():
    assert cjk_tokens("电") == ["电"]
    assert cjk_tokens("买 电 脑") == ["买", "电", "脑"]
    assert cjk_tokens("电，脑") == ["电", "脑"]


def test_ascii_words_are_kept_whole_and_lowercased():
    assert cjk_tokens("显卡 DDR5 内存 RTX5070") == ["显卡", "ddr5", "内存", "rtx5070"]


def test_cjk_and_ascii_split_where_the_script_changes():
    assert cjk_tokens("买DDR5内存") == ["买", "ddr5", "内存"]


def test_bigrams_with_a_sentence_particle_are_skipped():
    assert cjk_tokens("电脑相关的视频") == ["电脑", "脑相", "相关", "视频"]
    assert cjk_tokens("好吗") == []
    assert cjk_tokens("你呢吧") == []
    assert cjk_tokens("价格了吗") == ["价格"]


def test_duplicates_are_removed_keeping_first_occurrence():
    assert cjk_tokens("电脑 电脑价格 ddr5 DDR5") == ["电脑", "脑价", "价格", "ddr5"]


def test_empty_and_punctuation_only_text_has_no_tokens():
    assert cjk_tokens("") == []
    assert cjk_tokens("   ") == []
    assert cjk_tokens("，。！？、…… ---") == []
    assert zh_tsquery("，。！？") is None
    assert zh_tsquery("") is None
    assert zh_tsquery("的") is not None  # a lone particle is still a unigram token


def test_max_tokens_caps_the_result_and_documents_are_not_capped_by_default():
    long_text = distinct_chars(200)  # 199 distinct bigrams

    assert len(cjk_tokens(long_text)) == 199
    capped = cjk_tokens(long_text, max_tokens=QUERY_TOKEN_LIMIT)
    assert len(capped) == QUERY_TOKEN_LIMIT == 64
    assert capped == cjk_tokens(long_text)[:64]
    assert cjk_tokens("ab cd ef", max_tokens=2) == ["ab", "cd"]


def test_tsquery_joins_tokens_with_or_in_the_simple_configuration():
    sql, params = compiled(zh_tsquery("电脑相关的视频"))

    assert sql.startswith("websearch_to_tsquery(")
    assert params == {
        "websearch_to_tsquery_1": "simple",
        "websearch_to_tsquery_2": "电脑 OR 脑相 OR 相关 OR 视频",
    }


def test_tsquery_applies_the_query_token_limit():
    long_query = distinct_chars(200)

    _sql, params = compiled(zh_tsquery(long_query))

    assert params["websearch_to_tsquery_2"].count(" OR ") == QUERY_TOKEN_LIMIT - 1


def test_tsquery_cannot_carry_websearch_operators():
    _sql, params = compiled(zh_tsquery('电脑 -价格 "内存" OR (drop;)'))

    text = params["websearch_to_tsquery_2"]
    assert "-" not in text and '"' not in text and "(" not in text and ";" not in text
    assert text == "电脑 OR 价格 OR 内存 OR or OR drop"


def test_tsvector_is_built_from_the_space_joined_tokens():
    sql, params = compiled(zh_tsvector("电脑价格 DDR5"))

    assert sql.startswith("to_tsvector(")
    assert params == {
        "to_tsvector_1": "simple",
        "to_tsvector_2": "电脑 脑价 价格 ddr5",
    }
