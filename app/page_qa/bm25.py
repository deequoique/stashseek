"""A tiny, deterministic, in-process BM25 for a single transcript.

Page Q&A ranks the paragraphs of one caller-supplied transcript in memory.  It
deliberately avoids Postgres, ``jieba`` and any new dependency: Latin text is
tokenized into lowercase words and CJK runs into character bigrams, which is
enough for keyword recall inside one video.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

# Han, kana and Hangul letters are treated as "CJK" (punctuation is not a term).
CJK_CHAR_CLASS = (
    "\u3040-\u30ff"  # Hiragana + Katakana
    "\u3400-\u4dbf"  # CJK Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul syllables
)
_CJK_RUN = re.compile(f"[{CJK_CHAR_CLASS}]+")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercase Latin words plus CJK character bigrams (unigram for a lone char)."""

    if not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    tokens.extend(_WORD.findall(_CJK_RUN.sub(" ", lowered)))
    return tokens


class BM25Index:
    """Okapi BM25 over a fixed list of documents (``k1=1.2``, ``b=0.75``)."""

    def __init__(
        self,
        documents: Sequence[str],
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        self.k1 = k1
        self.b = b
        self._term_counts: list[Counter[str]] = [
            Counter(tokenize(document)) for document in documents
        ]
        self._lengths = [sum(counts.values()) for counts in self._term_counts]
        count = len(self._lengths)
        self._average_length = (sum(self._lengths) / count) if count else 0.0
        document_frequency: Counter[str] = Counter()
        for counts in self._term_counts:
            document_frequency.update(counts.keys())
        # Lucene-style IDF: always positive, so ubiquitous terms never subtract.
        self._idf = {
            term: math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def __len__(self) -> int:
        return len(self._lengths)

    def scores(self, query: str) -> list[float]:
        """Return one BM25 score per document (0.0 when nothing matches)."""

        query_terms = Counter(tokenize(query))
        scores = [0.0] * len(self._lengths)
        if not query_terms or not self._average_length:
            return scores
        for term in query_terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for index, counts in enumerate(self._term_counts):
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                normalizer = 1.0 - self.b + self.b * (
                    self._lengths[index] / self._average_length
                )
                scores[index] += idf * (
                    frequency * (self.k1 + 1.0) / (frequency + self.k1 * normalizer)
                )
        return scores

    def top_k(self, query: str, k: int) -> list[tuple[int, float]]:
        """Top ``k`` ``(document index, score)`` pairs with a positive score.

        Ties break toward the lower document index so the result is fully
        deterministic for a given transcript and question.
        """

        if k <= 0:
            return []
        ranked = sorted(
            (
                (index, score)
                for index, score in enumerate(self.scores(query))
                if score > 0.0
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return ranked[:k]
