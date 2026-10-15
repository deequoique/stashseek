import json

import httpx
import pytest

from app.asr.funasr import FunAsrClient
from app.asr.types import AsrProtocolError, AsrProviderUnavailable

BASE = "https://dashscope-intl.aliyuncs.com"
SUBMIT_URL = f"{BASE}/api/v1/services/audio/asr/transcription"
TASK_URL = f"{BASE}/api/v1/tasks/task-123"
RESULT_URL = "https://result.example-oss.aliyuncs.com/r.json?Signature=SECRET-SIG"
AUDIO_URL = "https://app.example.com/api/v1/asr-audio/abc.m4a?expires=1&sig=AUDIO-SIG"
API_KEY = "sk-test-api-key"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _succeeded(subtask_status="SUCCEEDED", usage=None, code=None):
    result = {"transcription_url": RESULT_URL, "subtask_status": subtask_status}
    if code:
        result["code"] = code
    output = {"task_id": "task-123", "task_status": "SUCCEEDED", "results": [result]}
    if usage is not None:
        output["usage"] = usage
    return {"output": output}


def _sentence(begin, end, text):
    return {"begin_time": begin, "end_time": end, "text": text, "sentence_id": 1}


def _result_body(sentences=None, text="hello", duration_ms=12_345, transcripts=True):
    if sentences is None:
        sentences = [_sentence(0, 1000, "hello")]
    body = {"properties": {"original_duration_in_milliseconds": duration_ms}}
    if transcripts:
        body["transcripts"] = [{"channel_id": 0, "text": text, "sentences": sentences}]
    return body


class Provider:
    """Scriptable DashScope fake keyed by request path."""

    def __init__(self, *, polls=None, result=None, submit=None):
        self.requests: list[httpx.Request] = []
        self.polls = list(polls or [_succeeded()])
        self.result = result if result is not None else _result_body()
        self.submit = submit or (200, {"output": {"task_id": "task-123", "task_status": "PENDING"}})
        self.poll_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url == SUBMIT_URL:
            status, body = self.submit
            return httpx.Response(status, json=body)
        if url == TASK_URL:
            self.poll_calls += 1
            item = self.polls[min(self.poll_calls - 1, len(self.polls) - 1)]
            if isinstance(item, httpx.Response):
                return item
            return httpx.Response(200, json=item)
        if url == RESULT_URL:
            if isinstance(self.result, httpx.Response):
                return self.result
            if isinstance(self.result, bytes):
                return httpx.Response(200, content=self.result)
            return httpx.Response(200, json=self.result)
        return httpx.Response(404, json={"unexpected": url})


def _client(provider, clock=None, *, timeout_seconds=600, poll_interval=5.0):
    clock = clock or FakeClock()
    http = httpx.Client(transport=httpx.MockTransport(provider.handler))
    return (
        FunAsrClient(
            api_key=API_KEY,
            base_url=BASE + "/",
            model="fun-asr",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval,
            client=http,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        ),
        clock,
    )


def test_submit_request_format_has_async_header_parameters_and_no_language_hints():
    provider = Provider()
    client, _ = _client(provider)

    client.transcribe_url(AUDIO_URL)

    submit = provider.requests[0]
    assert (submit.method, str(submit.url)) == ("POST", SUBMIT_URL)
    assert submit.headers["authorization"] == f"Bearer {API_KEY}"
    assert submit.headers["content-type"] == "application/json"
    assert submit.headers["x-dashscope-async"] == "enable"
    assert json.loads(submit.content) == {
        "model": "fun-asr",
        "input": {"file_urls": [AUDIO_URL]},
        "parameters": {},
    }
    assert b"language_hints" not in submit.content
    poll = provider.requests[1]
    assert (poll.method, str(poll.url)) == ("GET", TASK_URL)
    assert poll.headers["authorization"] == f"Bearer {API_KEY}"


def test_result_download_never_sends_the_api_key_to_the_presigned_host():
    provider = Provider()
    client, _ = _client(provider)

    client.transcribe_url(AUDIO_URL)

    download = provider.requests[-1]
    assert str(download.url) == RESULT_URL
    assert "authorization" not in download.headers


def test_polls_until_success_and_converts_milliseconds_to_seconds():
    provider = Provider(
        polls=[
            {"output": {"task_status": "PENDING"}},
            {"output": {"task_status": "RUNNING"}},
            _succeeded(usage={"duration": 13}),
        ],
        result=_result_body(
            sentences=[
                _sentence(4200, 6000, "second sentence"),
                _sentence(0, 1500, "  first sentence "),
                _sentence(1500, 4200, "   "),
            ],
            duration_ms=12_345,
        ),
    )
    client, clock = _client(provider, poll_interval=5.0)

    transcript = client.transcribe_url(AUDIO_URL)

    assert provider.poll_calls == 3
    assert clock.sleeps == [5.0, 5.0]
    assert [(c.start_sec, c.end_sec, c.text) for c in transcript.cues] == [
        (0.0, 1.5, "first sentence"),
        (4.2, 6.0, "second sentence"),
    ]
    assert transcript.provider == "fun_asr"
    assert transcript.model == "fun-asr"
    assert transcript.language is None
    assert transcript.audio_seconds == pytest.approx(12.345)
    assert transcript.billed_seconds == 13


def test_httpx_request_logs_never_contain_the_presigned_result_query(caplog):
    # httpx logs ``HTTP Request: GET <url>`` at INFO; a worker running with
    # --loglevel=INFO must not persist the signature that unlocks the transcript.
    caplog.set_level("INFO", logger="httpx")
    client, _ = _client(Provider())

    client.transcribe_url(AUDIO_URL)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "HTTP Request: GET https://result.example-oss.aliyuncs.com/r.json " in logged
    assert "SECRET-SIG" not in logged and "Signature" not in logged
    # Request bodies (which carry the signed audio URL) are never logged.
    assert "AUDIO-SIG" not in logged and "sig=" not in logged


def test_redirects_are_never_followed():
    provider = Provider(
        result=httpx.Response(302, headers={"location": "https://evil.example/steal"})
    )
    client, _ = _client(provider)

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_url(AUDIO_URL)

    assert all(request.url.host != "evil.example" for request in provider.requests)


def test_owned_client_does_not_follow_redirects(monkeypatch):
    import ssl

    from app.asr import _common

    monkeypatch.setattr(_common, "_trusted_ssl_context", ssl.create_default_context)

    with _common.client_scope(None) as owned:
        assert owned.follow_redirects is False


def test_billed_seconds_are_read_from_the_envelope_usage():
    envelope = _succeeded()
    envelope["usage"] = {"duration": 7}
    client, _ = _client(Provider(polls=[envelope]))

    assert client.transcribe_url(AUDIO_URL).billed_seconds == 7


def test_subtask_failure_is_unavailable():
    client, _ = _client(Provider(polls=[_succeeded(subtask_status="FAILED")]))

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_url(AUDIO_URL)


@pytest.mark.parametrize("status", ["FAILED", "CANCELED", "UNKNOWN", "WEIRD"])
def test_terminal_task_failure_is_unavailable(status):
    client, _ = _client(Provider(polls=[{"output": {"task_status": status}}]))

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_url(AUDIO_URL)


def test_overall_timeout_while_task_never_finishes():
    provider = Provider(polls=[{"output": {"task_status": "RUNNING"}}])
    client, clock = _client(provider, timeout_seconds=30, poll_interval=10.0)

    with pytest.raises(AsrProviderUnavailable, match="task_timeout"):
        client.transcribe_url(AUDIO_URL)

    assert clock.now >= 30
    assert provider.poll_calls == 4


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_unavailable_without_retry(status):
    provider = Provider(submit=(status, {"code": "InvalidApiKey", "message": API_KEY}))
    client, clock = _client(provider)

    with pytest.raises(AsrProviderUnavailable) as caught:
        client.transcribe_url(AUDIO_URL)

    assert len(provider.requests) == 1
    assert clock.sleeps == []
    assert API_KEY not in str(caught.value)


def test_429_and_5xx_are_retried_with_backoff_then_succeed():
    provider = Provider(
        polls=[
            httpx.Response(429, json={}),
            httpx.Response(503, json={}),
            _succeeded(),
        ]
    )
    client, clock = _client(provider)

    transcript = client.transcribe_url(AUDIO_URL)

    assert [cue.text for cue in transcript.cues] == ["hello"]
    assert provider.poll_calls == 3
    assert clock.sleeps == [1.0, 2.0]


def test_submit_retries_then_gives_up_after_three_retries():
    provider = Provider(submit=(503, {"message": "down"}))
    client, clock = _client(provider)

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_url(AUDIO_URL)

    assert len(provider.requests) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_network_errors_are_retried_and_do_not_chain_the_url():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    clock = FakeClock()
    client = FunAsrClient(
        api_key=API_KEY,
        base_url=BASE,
        model="fun-asr",
        timeout_seconds=60,
        poll_interval_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    with pytest.raises(AsrProviderUnavailable) as caught:
        client.transcribe_url(AUDIO_URL)

    assert calls["n"] == 4
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "dashscope" not in str(caught.value)


def test_other_client_errors_are_not_retried():
    provider = Provider(submit=(400, {"code": "InvalidParameter"}))
    client, clock = _client(provider)

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_url(AUDIO_URL)

    assert len(provider.requests) == 1
    assert clock.sleeps == []


def test_valid_empty_transcript_returns_no_cues():
    provider = Provider(result=_result_body(sentences=[], text=""))
    client, _ = _client(provider)

    transcript = client.transcribe_url(AUDIO_URL)

    assert transcript.cues == ()
    assert transcript.audio_seconds == pytest.approx(12.345)


def test_no_valid_fragment_code_is_a_valid_empty_transcript():
    provider = Provider(
        polls=[_succeeded(subtask_status="FAILED", code="SUCCESS_WITH_NO_VALID_FRAGMENT")]
    )
    client, _ = _client(provider)

    transcript = client.transcribe_url(AUDIO_URL)

    assert transcript.cues == ()
    assert len(provider.requests) == 2  # submit + poll, no result download


@pytest.mark.parametrize(
    "result",
    [
        _result_body(transcripts=False),
        {"transcripts": []},
        {"transcripts": ["not-an-object"]},
        _result_body(sentences=[_sentence(5000, 1000, "backwards")]),
        _result_body(sentences=[_sentence(-1, 1000, "negative")]),
        _result_body(sentences=[_sentence("1", 1000, "stringly")]),
        _result_body(sentences=[_sentence(True, 1000, "bool")]),
        _result_body(sentences=[], text="speech without timestamps"),
        _result_body(sentences=["not-an-object"]),
        b'{"transcripts":[{"sentences":[{"begin_time":NaN,"end_time":1,"text":"x"}]}]}',
        b"not json at all",
    ],
)
def test_protocol_errors(result):
    client, _ = _client(Provider(result=result))

    with pytest.raises(AsrProtocolError):
        client.transcribe_url(AUDIO_URL)


@pytest.mark.parametrize(
    "submit",
    [
        (200, {"output": {}}),
        (200, {"output": {"task_id": "../../etc/passwd"}}),
        (200, {"output": {"task_id": 7}}),
        (200, {}),
    ],
)
def test_invalid_submit_response_is_a_protocol_error(submit):
    client, _ = _client(Provider(submit=submit))

    with pytest.raises(AsrProtocolError):
        client.transcribe_url(AUDIO_URL)


@pytest.mark.parametrize(
    "poll",
    [
        {"output": {"task_status": "SUCCEEDED"}},
        {"output": {"task_status": "SUCCEEDED", "results": []}},
        {"output": {"task_status": "SUCCEEDED", "results": [{"subtask_status": "SUCCEEDED"}]}},
        {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [
                    {"subtask_status": "SUCCEEDED", "transcription_url": "http://insecure.example/r.json"}
                ],
            }
        },
    ],
)
def test_invalid_success_payload_is_a_protocol_error(poll):
    client, _ = _client(Provider(polls=[poll]))

    with pytest.raises(AsrProtocolError):
        client.transcribe_url(AUDIO_URL)


def test_oversized_result_is_a_protocol_error():
    big = httpx.Response(
        200, content=b"{}", headers={"content-length": str(60 * 1024 * 1024)}
    )
    client, _ = _client(Provider(result=big))

    with pytest.raises(AsrProtocolError):
        client.transcribe_url(AUDIO_URL)


def test_errors_never_contain_urls_or_secrets():
    cases = [
        Provider(result=b"not json"),
        Provider(polls=[_succeeded(subtask_status="FAILED")]),
        Provider(submit=(401, {"message": API_KEY})),
    ]
    for provider in cases:
        client, _ = _client(provider)
        with pytest.raises((AsrProtocolError, AsrProviderUnavailable)) as caught:
            client.transcribe_url(AUDIO_URL)
        text = str(caught.value)
        for secret in (API_KEY, "SECRET-SIG", "AUDIO-SIG", "aliyuncs", "app.example.com"):
            assert secret not in text


def test_constructor_rejects_missing_credentials_and_bad_timeouts():
    with pytest.raises(ValueError):
        FunAsrClient(api_key="", base_url=BASE, model="m", timeout_seconds=1, poll_interval_seconds=1)
    with pytest.raises(ValueError):
        FunAsrClient(api_key="k", base_url=BASE, model="m", timeout_seconds=0, poll_interval_seconds=1)
