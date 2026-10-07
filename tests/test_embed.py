import http.client
import io
import json
from urllib.error import HTTPError

import pytest

from app.ingest.embed import EmbeddingError, ZhipuEmbedder


def _response(data):
    return io.BytesIO(json.dumps({"data": data}).encode())


class _RecordingSleep:
    """Collects retry delays without ever sleeping for real."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _ZeroJitterRandom:
    """Deterministic stand-in for ``random.Random`` used by backoff jitter."""

    def uniform(self, _a: float, _b: float) -> float:
        return 0.0


def test_embed_splits_inputs_into_ordered_batches_of_64(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout, context):
        payload = json.loads(request.data)
        calls.append((request, timeout, context, payload))
        rows = [
            {"index": index, "embedding": [float(text.removeprefix("cue-"))]}
            for index, text in enumerate(payload["input"])
        ]
        return _response(list(reversed(rows)))

    monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
    embedder = ZhipuEmbedder("secret", dimensions=1)

    embeddings = embedder.embed([f"cue-{index}" for index in range(130)])

    assert [len(call[3]["input"]) for call in calls] == [64, 64, 2]
    assert embeddings == [[float(index)] for index in range(130)]
    assert all(call[1] == 60 for call in calls)
    assert all(call[2] is None for call in calls)
    assert all(call[3]["model"] == "embedding-3" for call in calls)
    assert all(call[3]["dimensions"] == 1 for call in calls)
    assert all(call[0].get_header("Authorization") == "Bearer secret" for call in calls)


def test_embed_empty_input_does_not_call_api(monkeypatch):
    monkeypatch.setattr(
        "app.ingest.embed.urlopen",
        lambda *_args, **_kwargs: pytest.fail("API should not be called"),
    )
    assert ZhipuEmbedder("secret").embed([]) == []


def test_embed_rejects_batch_size_above_zhipu_limit():
    with pytest.raises(ValueError, match="between 1 and 64"):
        ZhipuEmbedder("secret", batch_size=65)


def test_embed_rejects_incomplete_batch_response(monkeypatch):
    monkeypatch.setattr(
        "app.ingest.embed.urlopen",
        lambda *_args, **_kwargs: _response(
            [{"index": 0, "embedding": [1.0]}]
        ),
    )

    with pytest.raises(EmbeddingError, match="response count mismatch"):
        ZhipuEmbedder("secret").embed(["first", "second"])


@pytest.mark.parametrize(
    ("embedding", "message"),
    [([1.0], "dimension mismatch"), ([float("nan"), 1.0], "invalid values")],
)
def test_embed_rejects_invalid_vector_shape_or_values(monkeypatch, embedding, message):
    monkeypatch.setattr(
        "app.ingest.embed.urlopen",
        lambda *_args, **_kwargs: _response([{"index": 0, "embedding": embedding}]),
    )

    with pytest.raises(EmbeddingError, match=message):
        ZhipuEmbedder("secret", dimensions=2).embed(["redacted-input"])


def test_embed_normalizes_huge_integer_values_to_safe_embedding_error(monkeypatch):
    monkeypatch.setattr(
        "app.ingest.embed.urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"ignored"),
    )
    monkeypatch.setattr(
        "app.ingest.embed.json.load",
        lambda _response: {"data": [{"index": 0, "embedding": [10**5000]}]},
    )

    with pytest.raises(EmbeddingError, match="invalid values") as caught:
        ZhipuEmbedder("secret", dimensions=1).embed(["redacted-input"])
    assert caught.value.code == "embedding_error"


def test_embed_rejects_non_positive_dimensions():
    with pytest.raises(ValueError, match="dimensions must be positive"):
        ZhipuEmbedder("secret", dimensions=0)


def test_embed_retries_incomplete_read_then_succeeds(monkeypatch):
    attempts: list[int] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(1)
        if len(attempts) == 1:
            raise http.client.IncompleteRead(b"")
        return _response([{"index": 0, "embedding": [1.0]}])

    monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
    sleep = _RecordingSleep()
    embedder = ZhipuEmbedder(
        "secret", dimensions=1, sleep=sleep, rng=_ZeroJitterRandom()
    )

    assert embedder.embed(["redacted-input"]) == [[1.0]]
    assert len(attempts) == 2
    assert len(sleep.calls) == 1


def test_embed_retries_http_429_and_503_then_succeeds(monkeypatch):
    for status_code in (429, 503):
        attempts: list[int] = []

        def fake_urlopen(request, timeout, context, _attempts=attempts, _code=status_code):
            _attempts.append(1)
            if len(_attempts) == 1:
                raise HTTPError(request.full_url, _code, "throttled", None, None)
            return _response([{"index": 0, "embedding": [1.0]}])

        monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
        sleep = _RecordingSleep()
        embedder = ZhipuEmbedder(
            "secret", dimensions=1, sleep=sleep, rng=_ZeroJitterRandom()
        )

        assert embedder.embed(["redacted-input"]) == [[1.0]]
        assert len(attempts) == 2
        assert len(sleep.calls) == 1


def test_embed_raises_after_three_attempts_on_repeated_connection_reset(monkeypatch):
    attempts: list[int] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(1)
        raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
    sleep = _RecordingSleep()
    embedder = ZhipuEmbedder(
        "secret", dimensions=1, sleep=sleep, rng=_ZeroJitterRandom()
    )

    with pytest.raises(EmbeddingError, match="embedding request failed") as caught:
        embedder.embed(["redacted-input"])

    assert len(attempts) == 3
    assert len(sleep.calls) == 2
    assert isinstance(caught.value.__cause__, ConnectionResetError)


def test_embed_http_400_fails_without_retry(monkeypatch):
    attempts: list[int] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(1)
        raise HTTPError(request.full_url, 400, "Bad Request", None, None)

    monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
    sleep = _RecordingSleep()
    embedder = ZhipuEmbedder(
        "secret", dimensions=1, sleep=sleep, rng=_ZeroJitterRandom()
    )

    with pytest.raises(EmbeddingError, match="embedding request failed") as caught:
        embedder.embed(["redacted-input"])

    assert len(attempts) == 1
    assert sleep.calls == []
    assert isinstance(caught.value.__cause__, HTTPError)


def test_embed_malformed_json_fails_without_retry(monkeypatch):
    attempts: list[int] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(1)
        return io.BytesIO(b"not json")

    monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
    sleep = _RecordingSleep()
    embedder = ZhipuEmbedder(
        "secret", dimensions=1, sleep=sleep, rng=_ZeroJitterRandom()
    )

    with pytest.raises(EmbeddingError, match="embedding request failed") as caught:
        embedder.embed(["redacted-input"])

    assert len(attempts) == 1
    assert sleep.calls == []
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)


def test_embed_retry_backoff_never_sleeps_for_real(monkeypatch):
    monkeypatch.setattr(
        "time.sleep", lambda _seconds: pytest.fail("must not sleep for real")
    )
    attempts: list[int] = []

    def fake_urlopen(request, timeout, context):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("connection reset")
        return _response([{"index": 0, "embedding": [1.0]}])

    monkeypatch.setattr("app.ingest.embed.urlopen", fake_urlopen)
    sleep = _RecordingSleep()
    embedder = ZhipuEmbedder(
        "secret", dimensions=1, sleep=sleep, rng=_ZeroJitterRandom()
    )

    assert embedder.embed(["redacted-input"]) == [[1.0]]
    assert len(attempts) == 3
    assert len(sleep.calls) == 2
