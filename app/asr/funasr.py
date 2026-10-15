"""Alibaba Cloud Model Studio (DashScope) Fun-ASR asynchronous file recognition.

The client submits one signed audio URL, polls the task until it finishes,
downloads the sentence-level result and converts it to :class:`AsrTranscript`.
No ``language_hints`` are sent so the model auto-detects mixed Chinese/English
speech.  Raised errors carry only stable codes: never the audio URL, the API
key, provider messages or transcript text.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from app.asr._common import (
    client_scope,
    finite_number,
    optional_seconds,
    request_json,
    require_dict,
)
from app.asr.types import (
    AsrCue,
    AsrProtocolError,
    AsrProviderUnavailable,
    AsrTranscript,
)

SUBMIT_PATH = "/api/v1/services/audio/asr/transcription"
TASK_PATH = "/api/v1/tasks/"
PROVIDER = "fun_asr"
# Provider status/code values.
_PENDING_STATES = frozenset({"PENDING", "RUNNING"})
_NO_SPEECH_CODE = "SUCCESS_WITH_NO_VALID_FRAGMENT"
# One initial try plus three retries, each with its own backoff.
_ATTEMPTS = 4
_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_API_RESPONSE_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 50 * 1024 * 1024
_TASK_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class FunAsrClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key or not base_url or not model:
            raise ValueError("Fun-ASR client requires a key, base URL and model")
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("Fun-ASR timeouts must be positive")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic

    def transcribe_url(self, audio_url: str) -> AsrTranscript:
        deadline = self._monotonic() + self._timeout_seconds
        with client_scope(self._client) as client:
            task_id = self._submit(client, audio_url)
            payload = self._wait(client, task_id, deadline)
            return self._fetch_result(client, payload)

    def _headers(self, *, json_body: bool = False, asynchronous: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if asynchronous:
            headers["X-DashScope-Async"] = "enable"
        return headers

    def _submit(self, client: httpx.Client, audio_url: str) -> str:
        payload = request_json(
            client,
            "POST",
            f"{self._base_url}{SUBMIT_PATH}",
            headers=self._headers(json_body=True, asynchronous=True),
            json_body={
                "model": self._model,
                "input": {"file_urls": [audio_url]},
                "parameters": {},
            },
            timeout=_REQUEST_TIMEOUT,
            max_response_bytes=_MAX_API_RESPONSE_BYTES,
            attempts=_ATTEMPTS,
            sleep=self._sleep,
        )
        output = require_dict(require_dict(payload).get("output"))
        task_id = output.get("task_id")
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise AsrProtocolError("invalid_task_id")
        return task_id

    def _wait(self, client: httpx.Client, task_id: str, deadline: float) -> dict:
        """Poll until the task succeeds and return the whole status payload."""

        while True:
            payload = request_json(
                client,
                "GET",
                f"{self._base_url}{TASK_PATH}{task_id}",
                headers=self._headers(),
                timeout=_REQUEST_TIMEOUT,
                max_response_bytes=_MAX_API_RESPONSE_BYTES,
                attempts=_ATTEMPTS,
                sleep=self._sleep,
            )
            output = require_dict(require_dict(payload).get("output"))
            status = output.get("task_status")
            if status == "SUCCEEDED":
                return payload
            if status not in _PENDING_STATES:
                # FAILED, CANCELED, UNKNOWN or an unrecognized value.
                raise AsrProviderUnavailable("task_failed")
            if self._monotonic() >= deadline:
                raise AsrProviderUnavailable("task_timeout")
            self._sleep(self._poll_interval_seconds)

    def _fetch_result(self, client: httpx.Client, payload: dict) -> AsrTranscript:
        output = require_dict(payload.get("output"))
        results = output.get("results")
        if not isinstance(results, list) or not results:
            raise AsrProtocolError("missing_results")
        first = require_dict(results[0])
        # DashScope reports billed seconds in the envelope's ``usage``; accept
        # the same object nested under ``output`` as well.
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            usage = output.get("usage")
        billed = optional_seconds(
            usage.get("duration") if isinstance(usage, dict) else None
        )
        if first.get("code") == _NO_SPEECH_CODE:
            # The provider reports a valid but silent file: not a fallback case.
            return AsrTranscript((), None, PROVIDER, self._model, None, billed)
        if first.get("subtask_status") != "SUCCEEDED":
            raise AsrProviderUnavailable("subtask_failed")
        transcription_url = first.get("transcription_url")
        if not isinstance(transcription_url, str):
            raise AsrProtocolError("missing_transcription_url")
        parts = urlsplit(transcription_url)
        if parts.scheme != "https" or not parts.hostname:
            raise AsrProtocolError("invalid_transcription_url")
        # No Authorization header: this is a third-party pre-signed URL.
        body = request_json(
            client,
            "GET",
            transcription_url,
            timeout=_REQUEST_TIMEOUT,
            max_response_bytes=_MAX_RESULT_BYTES,
            attempts=_ATTEMPTS,
            sleep=self._sleep,
        )
        return self._parse_result(require_dict(body), billed)

    def _parse_result(self, body: dict, billed_seconds: float | None) -> AsrTranscript:
        transcripts = body.get("transcripts")
        if not isinstance(transcripts, list) or not transcripts:
            raise AsrProtocolError("missing_transcripts")
        transcript = require_dict(transcripts[0])
        sentences = transcript.get("sentences")
        if sentences is None:
            sentences = []
        if not isinstance(sentences, list):
            raise AsrProtocolError("invalid_sentences")
        cues: list[AsrCue] = []
        for sentence in sentences:
            item = require_dict(sentence)
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            start = finite_number(item.get("begin_time")) / 1000
            end = finite_number(item.get("end_time")) / 1000
            if start < 0 or end < start:
                raise AsrProtocolError("invalid_timing")
            cues.append(AsrCue(start, end, text.strip()))
        if not cues:
            spoken = transcript.get("text")
            if isinstance(spoken, str) and spoken.strip():
                # Text without timestamped sentences cannot become cues.
                raise AsrProtocolError("no_timestamped_sentences")
        cues.sort(key=lambda cue: cue.start_sec)
        properties = body.get("properties")
        audio_seconds = optional_seconds(
            properties.get("original_duration_in_milliseconds")
            if isinstance(properties, dict)
            else None,
            scale=1000.0,
        )
        return AsrTranscript(
            cues=tuple(cues),
            language=None,
            provider=PROVIDER,
            model=self._model,
            audio_seconds=audio_seconds,
            billed_seconds=billed_seconds,
        )
