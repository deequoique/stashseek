"""In-process sliding-window limiter for page Q&A questions.

Like the extension session index this is per process, not shared across
workers: it bounds accidental or abusive bursts from one user, it is not a
distributed quota.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Hashable

# Questions are counted per user over this window (``PAGE_QA_RATE_LIMIT``).
PAGE_QA_RATE_WINDOW_SECONDS = 600.0
_SWEEP_THRESHOLD = 10_000


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = PAGE_QA_RATE_WINDOW_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("rate limit and window must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[Hashable, deque[float]] = {}

    def acquire(self, key: Hashable) -> int | None:
        """Record one hit, or return the seconds to wait when over the limit."""

        now = self._clock()
        if len(self._hits) > _SWEEP_THRESHOLD:
            self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        self._prune(hits, now)
        if len(hits) >= self.limit:
            return max(1, math.ceil(hits[0] + self.window_seconds - now))
        hits.append(now)
        return None

    def _prune(self, hits: deque[float], now: float) -> None:
        horizon = now - self.window_seconds
        while hits and hits[0] <= horizon:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._hits):
            hits = self._hits[key]
            self._prune(hits, now)
            if not hits:
                del self._hits[key]
