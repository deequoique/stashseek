"""Text normalization shared by ASR indexing and review outputs.

The provider may switch between simplified and traditional Chinese for
different audio windows.  Search-facing text therefore needs one stable
script representation while leaving timestamps and non-Chinese text intact.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache


class TextNormalizationUnavailable(RuntimeError):
    """Raised when the configured Chinese script converter is not installed."""


@lru_cache(maxsize=1)
def _simplified_converter():
    try:
        from opencc import OpenCC
    except ImportError as exc:  # pragma: no cover - exercised in env diagnostics
        raise TextNormalizationUnavailable("opencc_dependency_missing") from exc
    return OpenCC("t2s")


def normalize_text(value: str) -> str:
    """Normalize Unicode/whitespace and convert traditional Chinese to simplified.

    OpenCC's ``t2s`` profile leaves Latin text, digits, punctuation, and code
    identifiers alone in the common case while converting Chinese script.  We
    intentionally do not normalize timestamps or infer/translate language.
    """

    if not isinstance(value, str):
        raise TypeError("text must be a string")
    cleaned = unicodedata.normalize("NFKC", value.replace("\x00", ""))
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    return _simplified_converter().convert(cleaned)


__all__ = ["TextNormalizationUnavailable", "normalize_text"]
