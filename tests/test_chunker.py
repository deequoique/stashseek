import math
import re

from app.connectors.base import Cue
from app.ingest.chunker import chunk, semantic_boundary_indices


def _cues(texts, *, step=10, gaps=None):
    gaps = gaps or {}
    result = []
    cursor = 0.0
    for index, text in enumerate(texts):
        cursor += gaps.get(index, 0)
        result.append(Cue(cursor, cursor + step, text))
        cursor += step
    return result


def _words(index, count, *, suffix=""):
    return " ".join(f"w{index}_{k}" for k in range(count)) + suffix


def _word_count(text):
    return len(re.findall(r"\b[\w']+\b", text))


def _cjk_count(text):
    return len(re.findall(r"[㐀-鿿]", text))


def test_chapters_are_first_priority():
    cues = _cues(["a", "b", "c", "d"])
    chapters = [{"start_time": 0, "end_time": 20}, {"start_time": 20, "end_time": 40}]
    parts = chunk(cues, lang="en", chapters=chapters)
    assert [part.boundary_kind for part in parts] == ["chapter", "chapter"]


def test_semantic_boundary_indices_still_finds_strict_local_minima():
    embeddings = [[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9], [0, 1]]
    assert semantic_boundary_indices(embeddings) == [2]


def test_short_whole_transcript_stays_one_chunk_below_lower_bound():
    cues = _cues(["one two three", "four five six", "seven eight nine"], step=2)
    parts = chunk(cues, lang="en")
    assert len(parts) == 1
    assert parts[0].boundary_kind == "hard_cut"
    assert _word_count(parts[0].text) < 80


def test_dense_punctuation_track_merges_to_lower_and_upper_bounds():
    texts = [_words(i, 5, suffix=".") for i in range(100)]
    cues = _cues(texts, step=1)
    parts = chunk(cues, lang="en")
    assert len(parts) > 1
    for part in parts:
        words = _word_count(part.text)
        assert 80 <= words <= 200
        assert part.end_sec - part.start_sec <= 120


def test_overlap_shares_whole_cues_and_always_advances():
    texts = [_words(i, 5, suffix=".") for i in range(100)]
    cues = _cues(texts, step=1)
    parts = chunk(cues, lang="en")
    assert len(parts) >= 2
    for prev, nxt in zip(parts, parts[1:]):
        assert nxt.start_sec < prev.end_sec
        assert nxt.start_sec > prev.start_sec
    # Steady-state windows pick the exact 140-word midpoint (28 cues), with a
    # 15% whole-cue overlap of round(0.15 * 28) == 4 cues == 4 seconds here.
    assert parts[1].start_sec == parts[0].end_sec - 4


def test_semantic_deepest_dip_is_chosen():
    # 40 cues (not 30): the tail after the dip must still reach the lower
    # bound on its own, otherwise it would hard-cut and then tail-merge back
    # into the first chunk, hiding the cut this test wants to observe.
    texts = [_words(i, 5) for i in range(40)]
    cues = _cues(texts, step=1)
    embeddings = [[1.0, 0.0]] * 20 + [[0.0, 1.0]] * 20
    parts = chunk(cues, lang="en", semantic_embedder=lambda _: embeddings)
    assert parts[0].boundary_kind == "semantic"
    assert abs(parts[0].end_sec - cues[19].end) < 1e-9
    words = _word_count(parts[0].text)
    assert 80 <= words <= 200


def test_sentence_end_preferred_within_half_max_depth():
    texts = [_words(i, 10, suffix=("." if i == 9 else "")) for i in range(30)]
    cues = _cues(texts, step=1)

    angle_b = math.radians(70)
    angle_c = math.radians(160)
    plateau_a = (1.0, 0.0)
    plateau_b = (math.cos(angle_b), math.sin(angle_b))
    plateau_c = (math.cos(angle_c), math.sin(angle_c))
    embeddings = [plateau_a] * 10 + [plateau_b] * 10 + [plateau_c] * 10

    parts = chunk(cues, lang="en", semantic_embedder=lambda _: embeddings)

    # The deepest dip sits at cue 19/20 (depth ~2.0, no sentence end there).
    # The shallower dip at cue 9/10 (depth ~1.3, >=50% of max) lines up with
    # a sentence end, so it must win instead of the purely deepest dip.
    assert parts[0].boundary_kind == "punct"
    assert abs(parts[0].end_sec - cues[9].end) < 1e-9


def test_gap_preferred_without_embeddings():
    texts = [_words(i, 5, suffix="." if i == 24 else "") for i in range(40)]
    cues = _cues(texts, step=1, gaps={20: 3})
    parts = chunk(cues, lang="en")
    assert parts[0].boundary_kind == "gap"
    assert abs(parts[0].end_sec - cues[19].end) < 1e-9


def test_long_span_with_no_boundary_uses_hard_cut():
    texts = [f"solo{i}" for i in range(50)]
    cues = _cues(texts, step=20)
    parts = chunk(cues, lang="en")
    assert len(parts) > 1
    assert all(part.boundary_kind == "hard_cut" for part in parts)
    assert all(part.end_sec - part.start_sec <= 120 for part in parts)


def test_hard_cut_has_overlap():
    cues = _cues([str(i) for i in range(30)], step=10)
    parts = chunk(cues, lang="zh")
    assert len(parts) >= 3
    assert parts[1].start_sec < parts[0].end_sec
    overlap = parts[0].end_sec - parts[1].start_sec
    assert overlap / (parts[0].end_sec - parts[0].start_sec) >= 0.15
    assert all(part.end_sec - part.start_sec <= 120 for part in parts)


def test_hard_cut_advances_across_a_gap_larger_than_the_duration_ceiling():
    cues = [
        Cue(0, 1, "first"),
        Cue(500, 501, "second"),
        Cue(502, 503, "third"),
    ]

    parts = chunk(cues, lang="en")

    assert [part.text for part in parts] == ["first", "second third"]
    assert all(part.end_sec >= part.start_sec for part in parts)


def test_language_specific_upper_bound_without_signals():
    english = chunk(_cues(["word " * 50] * 8, step=5), lang="en")
    chinese = chunk(_cues(["字" * 100] * 8, step=5), lang="zh")
    assert english[0].boundary_kind == "hard_cut"
    assert chinese[0].boundary_kind == "hard_cut"
    assert _word_count(english[0].text) <= 200
    assert _cjk_count(chinese[0].text) <= 330
    assert english[0].end_sec - english[0].start_sec <= 120
    assert chinese[0].end_sec - chinese[0].start_sec <= 120


def test_tail_merge_within_limits():
    texts = [_words(i, 5, suffix=".") for i in range(32)]
    cues = _cues(texts, step=1)
    parts = chunk(cues, lang="en")
    assert len(parts) == 1
    assert parts[0].boundary_kind == "punct"
    assert _word_count(parts[0].text) == 160


def test_tail_kept_when_merge_would_exceed_bounds():
    texts = [_words(i, 5) for i in range(46)]
    cues = _cues(texts, step=1, gaps={40: 3})
    parts = chunk(cues, lang="en")
    assert len(parts) == 2
    assert parts[0].boundary_kind == "gap"
    assert parts[1].boundary_kind == "hard_cut"
    assert _word_count(parts[1].text) < 80


def test_overlap_retreat_never_produces_a_zero_progress_duplicate_chunk():
    # A ~140s mid-transcript pause starts right after the first chunk's gap
    # cut. The 15% overlap retreats into the last couple of cues before that
    # pause, which alone can't reach the lower bound before the pause pushes
    # duration past the 120s ceiling again -- so without a guard, the
    # hard-cut fallback from the retreated start lands on the exact same cue
    # as the previous chunk, twice, producing fully-redundant chunks nested
    # inside the first one.
    cues = [Cue(float(i), float(i) + 1.0, "w " * 10) for i in range(12)]
    cues.append(Cue(152.0, 153.0, "tail one"))
    cues.append(Cue(154.0, 155.0, "tail two"))

    parts = chunk(cues, lang="en")

    assert [(p.start_sec, p.end_sec) for p in parts] == [(0.0, 12.0), (152.0, 155.0)]
    # Every chunk must strictly extend coverage past the previous one.
    for prev, nxt in zip(parts, parts[1:]):
        assert nxt.end_sec > prev.end_sec


def test_cjk_unit_bounds():
    texts = ["测" * 10 + "。" for _ in range(60)]
    cues = _cues(texts, step=1)
    parts = chunk(cues, lang="zh")
    assert len(parts) > 1
    for part in parts:
        chars = _cjk_count(part.text)
        assert chars <= 330
        assert part.end_sec - part.start_sec <= 120
    # every part but a merged/kept tail should also respect the lower bound
    assert all(_cjk_count(part.text) >= 130 for part in parts[:-1])
