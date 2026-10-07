"""Small injectable client for Zhipu's OpenAI-compatible embedding API."""

from __future__ import annotations

import http.client
import json
import math
import random
import ssl
import time
from collections.abc import Callable
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Transient embedding failures are retried per batch, never per item. The
# bounds keep worst-case added latency well under the acceptance target of
# ~10 seconds per batch regardless of jitter draws.
_MAX_EMBED_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_MAX_DELAY_SECONDS = 4.0
_RETRY_BACKOFF_MULTIPLIER = 2.0


class EmbeddingError(RuntimeError):
    def __init__(self, message: str, *, code: str = "embedding_error") -> None:
        super().__init__(message)
        self.code = code


class EmbeddingProvider(Protocol):
    """The small, shared boundary used by ingestion and query retrieval."""

    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ZhipuEmbedder:
    MAX_BATCH_SIZE = 64

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "embedding-3",
        endpoint: str = "https://open.bigmodel.cn/api/paas/v4/embeddings",
        dimensions: int = 1536,
        batch_size: int = MAX_BATCH_SIZE,
        ssl_context: ssl.SSLContext | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ZHIPU_API_KEY is required for embedding")
        if dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        if not 1 <= batch_size <= self.MAX_BATCH_SIZE:
            raise ValueError(
                f"embedding batch_size must be between 1 and {self.MAX_BATCH_SIZE}"
            )
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._ssl_context = ssl_context
        # Injectable so tests exercise retry/backoff without real sleeping.
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            embeddings.extend(self._embed_batch(texts[start : start + self.batch_size]))
        return embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps(
            {
                "model": self.model,
                "input": texts,
                "dimensions": self.dimensions,
            }
        ).encode()
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        data = self._fetch_batch_response(request)
        try:
            rows = data.get("data", [])
            if not isinstance(rows, list):
                raise TypeError("data is not a list")
            ordered = sorted(rows, key=lambda row: row["index"])
            if len(ordered) != len(texts):
                raise EmbeddingError("embedding response count mismatch")
            if [row["index"] for row in ordered] != list(range(len(texts))):
                raise EmbeddingError("embedding response index mismatch")
            vectors = [row["embedding"] for row in ordered]
            return [
                _normalize_vector(
                    vector,
                    dimensions=self.dimensions,
                    dimension_error="embedding response dimension mismatch",
                    value_error="embedding response contains invalid values",
                )
                for vector in vectors
            ]
        except (EmbeddingError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, EmbeddingError):
                raise
            raise EmbeddingError("embedding response is invalid") from exc

    def _fetch_batch_response(self, request: Request):
        """Perform the network request, retrying only transient failures.

        Only the failing batch is re-sent; the whole item is never re-embedded
        here. Permanent failures (e.g. non-retryable HTTP 4xx, malformed JSON)
        raise immediately without consuming a retry.
        """

        last_exc: BaseException | None = None
        for attempt in range(_MAX_EMBED_ATTEMPTS):
            try:
                with urlopen(
                    request, timeout=60, context=self._ssl_context
                ) as response:
                    return json.load(response)
            except (
                HTTPError,
                URLError,
                TimeoutError,
                http.client.HTTPException,
                OSError,
                json.JSONDecodeError,
            ) as exc:
                last_exc = exc
                is_last_attempt = attempt == _MAX_EMBED_ATTEMPTS - 1
                if is_last_attempt or not self._is_transient_error(exc):
                    raise EmbeddingError("embedding request failed") from exc
                self._sleep(self._retry_delay_seconds(attempt))
        # Unreachable: the loop above always returns or raises before exiting,
        # but an explicit raise keeps the method's control flow unambiguous.
        raise EmbeddingError("embedding request failed") from last_exc

    @staticmethod
    def _is_transient_error(exc: BaseException) -> bool:
        if isinstance(exc, HTTPError):
            # Other 4xx (e.g. 400 invalid request, 401 unauthorized) are
            # permanent; 429 and 5xx are transient provider/throttle failures.
            return exc.code == 429 or 500 <= exc.code < 600
        if isinstance(exc, json.JSONDecodeError):
            return False
        return isinstance(
            exc, (http.client.HTTPException, OSError, URLError, TimeoutError)
        )

    def _retry_delay_seconds(self, attempt_index: int) -> float:
        cap = min(
            _RETRY_MAX_DELAY_SECONDS,
            _RETRY_BASE_DELAY_SECONDS * (_RETRY_BACKOFF_MULTIPLIER**attempt_index),
        )
        return self._rng.uniform(0, cap)


def _normalize_vector(
    vector: object,
    *,
    dimensions: int,
    dimension_error: str,
    value_error: str,
) -> list[float]:
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise EmbeddingError(dimension_error)
    normalized: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingError(value_error)
        try:
            converted = float(value)
        except (OverflowError, ValueError) as exc:
            raise EmbeddingError(value_error) from exc
        if not math.isfinite(converted):
            raise EmbeddingError(value_error)
        normalized.append(converted)
    return normalized
