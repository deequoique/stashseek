"""ASR integration: handoff, asr queue task, failure mapping, and backfill.

Three layers:

* ``process_item`` with tiny fake sessions (state/return contract);
* ``process_dispatch`` handoff/convergence with a fake dispatch session;
* real PostgreSQL rows in an isolated schema (single completion event per
  dispatch, ASR end to end, backfill selection).  These need ``DATABASE_URL``
  to point at the test branch; the fixture skips when it cannot build a schema.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app import cli
from app.asr.audio import DownloadedAudio
from app.asr.connector import AsrFallbackConnector
from app.asr.transcriber import AsrTranscriber
from app.asr.types import (
    ASR_ERROR_CODES,
    AsrCue,
    AsrError,
    AsrTranscript,
)
from app.browser_capture import parse_canonical_transcript
from app.config import Settings
from app.connectors.base import (
    ItemMeta,
    NeedsASR,
    TransientFetchError,
)
from app.db import get_engine
from app.ingest import asr_backfill
from app.ingest.tasks import (
    ASR_PENDING,
    _asr_processor,
    _completion_diagnostic,
    _handoff_dispatch_to_asr,
    celery_app,
    process_dispatch,
    process_item,
    publish_asr_dispatch,
    transcribe_dispatch_task,
)
from app.models import (
    AppUser,
    Base,
    BrowserCapture,
    ChannelIdentity,
    ContentItem,
    ConversationThread,
    IngestCompletionEvent,
    IngestDispatch,
    Segment,
)
from app.web.library import _safe_error

CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SPOKEN = "PRIVATE-TRANSCRIPT-WORDS"
SECRET = "s" * 40


def _settings(**overrides):
    values = {
        "asr_enabled": True,
        "dashscope_api_key": "test-dashscope-key",
        "asr_public_base_url": "https://notebookai.example.com",
        "asr_audio_url_secret": SECRET,
    }
    values.update(overrides)
    return replace(Settings(), **values)


@pytest.fixture
def runtime_records():
    """Collect runtime diagnostics even if another test disabled propagation."""

    logger = logging.getLogger("notebook_agent.runtime")
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collector(level=logging.INFO)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _payloads(records):
    return [
        record.diagnostic_payload
        for record in records
        if hasattr(record, "diagnostic_payload")
    ]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _meta(**overrides):
    values = {
        "platform_id": "dQw4w9WgXcQ",
        "url": CANONICAL,
        "duration_sec": 321,
        "title": "A talk",
    }
    values.update(overrides)
    return ItemMeta(**values)


class Inner:
    """Platform connector with no caption track."""

    platform = "youtube"

    def __init__(self, text=None):
        self.text = text if text is not None else NeedsASR()
        self.calls: list[str] = []

    def match(self, _url):
        return "dQw4w9WgXcQ"

    def fetch_meta(self, platform_id):
        self.calls.append("meta")
        return _meta(platform_id=platform_id)

    def fetch_text(self, _platform_id):
        self.calls.append("text")
        return self.text


class AudioStore:
    """Temporary-audio object store used by the transcriber."""

    def __init__(self):
        self.puts: list[str] = []
        self.deleted: list[str] = []

    def put_file(self, key, path, _content_type):
        self.puts.append(key)
        return Path(path).stat().st_size

    def delete_object(self, key, **_kwargs):
        self.deleted.append(key)


class RawStore:
    """Transcript object store used by ``process_item``."""

    def __init__(self):
        self.puts: list[tuple[str, bytes, str]] = []

    def put(self, key, body, content_type):
        self.puts.append((key, body, content_type))

    def get(self, *_args, **_kwargs):
        raise AssertionError("ASR path must not read a stored capture")

    def delete_object(self, _key):
        return None


class Embedder:
    def embed(self, values):
        return [[0.25] * 1536 for _ in values]


def _downloader(*, platform, url, dest_dir, settings, runner=None):
    path = Path(dest_dir) / "audio.m4a"
    path.write_bytes(b"audio-bytes")
    return DownloadedAudio(path, "m4a", 11, "audio/mp4")


class FunAsr:
    def __init__(self, cues=None):
        self.cues = cues if cues is not None else [AsrCue(0.0, 2.0, SPOKEN)]
        self.urls: list[str] = []

    def transcribe_url(self, audio_url):
        self.urls.append(audio_url)
        return AsrTranscript(
            cues=tuple(self.cues),
            language="en",
            provider="fun_asr",
            model="fun-asr",
            audio_seconds=2.0,
            billed_seconds=2.0,
        )


def _asr_connector(*, funasr=None, audio_store=None, platform="youtube"):
    transcriber = AsrTranscriber(
        settings=_settings(),
        object_store=audio_store or AudioStore(),
        funasr=funasr or FunAsr(),
        whisper=None,
        downloader=_downloader,
    )
    return AsrFallbackConnector(Inner(), transcriber, platform=platform)


def _item(**overrides):
    values = {
        "id": 41,
        "user_id": 7,
        "platform": "youtube",
        "platform_id": "dQw4w9WgXcQ",
        "url": CANONICAL,
        "title": None,
        "author": None,
        "published_at": None,
        "duration_sec": None,
        "lang": None,
        "description": None,
        "tags": None,
        "chapters": None,
        "cover_url": None,
        "state": "pending",
        "raw_object_key": None,
        "raw_format": "json3",
        "content_hash": None,
        "text_source": "none",
        "fail_reason": None,
        "deleted_at": None,
        "purge_claimed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ItemDB:
    """Just enough session for ``process_item``."""

    def __init__(self, item, capture=None):
        self.item = item
        self.capture = capture
        self.commits: list[str] = []
        self.added: list[object] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _model, _item_id):
        return self.item

    def scalar(self, _statement):
        return self.capture

    def commit(self):
        self.commits.append(self.item.state)

    def refresh(self, _value):
        return None

    def execute(self, _statement):
        return None

    def add(self, value):
        self.added.append(value)


# ---------------------------------------------------------------------------
# process_item contract
# ---------------------------------------------------------------------------


def test_asr_enabled_handoff_keeps_item_fetching_and_returns_sentinel(monkeypatch):
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    item = _item()
    db = ItemDB(item)

    state = process_item(
        item.id, connector=Inner(), asr_handoff=True, session_factory=lambda: db
    )

    assert state == ASR_PENDING == "asr_pending"
    assert item.state == "fetching"
    assert db.commits == ["fetching", "fetching"]


def test_asr_disabled_keeps_needs_asr_even_when_handoff_is_requested(monkeypatch):
    monkeypatch.setattr(
        "app.ingest.tasks.get_settings", lambda: _settings(asr_enabled=False)
    )
    item = _item()
    db = ItemDB(item)

    state = process_item(
        item.id, connector=Inner(), asr_handoff=True, session_factory=lambda: db
    )

    assert state == "needs_asr"
    assert item.state == "needs_asr"
    assert db.commits == ["fetching", "needs_asr"]


def test_synchronous_callers_never_hand_off_even_when_asr_is_enabled(monkeypatch):
    # ``ingest_url`` (CLI) has no dispatch to hand over, so the default must
    # remain today's needs_asr.
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    item = _item()

    state = process_item(
        item.id, connector=Inner(), session_factory=lambda: ItemDB(item)
    )

    assert state == "needs_asr"
    assert item.state == "needs_asr"


def test_unsupported_platform_stays_needs_asr_with_handoff_requested(monkeypatch):
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    item = _item(platform="ntu_kaltura", platform_id="1_abc")

    state = process_item(
        item.id,
        connector=Inner(),
        asr_handoff=True,
        session_factory=lambda: ItemDB(item),
    )

    assert state == "needs_asr"
    assert item.state == "needs_asr"


def test_browser_capture_without_captions_never_hands_off(monkeypatch):
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    monkeypatch.setattr(
        "app.ingest.tasks._connector",
        lambda _url: pytest.fail("capture path must not use a remote connector"),
    )
    item = _item()
    capture = SimpleNamespace(caption_status="unavailable")

    state = process_item(
        item.id,
        asr_handoff=True,
        session_factory=lambda: ItemDB(item, capture),
    )

    assert state == "needs_asr"
    assert item.state == "needs_asr"
    assert item.text_source == "none"


def test_asr_transcript_is_stored_as_capture_v1_and_item_becomes_ready(monkeypatch):
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    item = _item()
    db = ItemDB(item)
    raw_store = RawStore()
    audio_store = AudioStore()

    state = process_item(
        item.id,
        connector=_asr_connector(audio_store=audio_store),
        embedder=Embedder(),
        object_store=raw_store,
        session_factory=lambda: db,
    )

    assert state == "ready"
    assert item.raw_format == "capture_v1"
    assert item.text_source == "asr_whisper"
    assert item.lang == "en"
    assert len(raw_store.puts) == 1
    key, body, content_type = raw_store.puts[0]
    assert key == item.raw_object_key
    assert key.startswith(f"{item.user_id}/youtube/dQw4w9WgXcQ/")
    assert key.endswith(".capture.json")
    assert content_type == "application/json"
    parsed = parse_canonical_transcript(body, source="asr_whisper", language="en")
    assert [cue.text for cue in parsed.cues] == [SPOKEN]
    assert [type(row) for row in db.added] == [Segment]
    # The temporary audio object never outlives the transcription.
    assert audio_store.deleted == audio_store.puts and len(audio_store.puts) == 1


# ---------------------------------------------------------------------------
# process_dispatch handoff with a fake dispatch session
# ---------------------------------------------------------------------------


class DispatchDB:
    def __init__(self, dispatch, item):
        self.dispatch = dispatch
        self.item = item

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _statement):
        return self.dispatch

    def get(self, model, object_id):
        if model is ContentItem and object_id == self.item.id:
            return self.item
        return None

    def commit(self):
        return None


def _dispatch_rows(*, item_state="fetching", dispatch_state="pending"):
    dispatch = IngestDispatch(
        id=71,
        public_id="dispatch",
        item_id=41,
        request_key="request",
        attempt=1,
        state=dispatch_state,
    )
    item = ContentItem(
        id=41,
        user_id=57,
        platform="youtube",
        platform_id="dQw4w9WgXcQ",
        kind="video",
        url=CANONICAL,
        state=item_state,
    )
    return dispatch, item, (lambda: DispatchDB(dispatch, item))


def test_process_dispatch_hands_the_same_dispatch_to_the_asr_queue(monkeypatch):
    dispatch, item, factory = _dispatch_rows()
    published = []
    monkeypatch.setattr(
        "app.ingest.tasks.publish_asr_dispatch",
        lambda dispatch_id: published.append(dispatch_id) or "asr-task-id",
    )
    monkeypatch.setattr(
        "app.ingest.tasks._complete_dispatch",
        lambda *_a, **_k: pytest.fail("handoff must not complete the dispatch"),
    )

    result = process_dispatch(
        71,
        task_id="ingest-task",
        processor=lambda _item_id: ASR_PENDING,
        session_factory=factory,
    )

    assert result == "asr_pending"
    assert (dispatch.state, dispatch.task_id) == ("enqueued", None)
    assert item.state == "fetching"
    assert published == [71]


def test_handoff_publish_failure_converges_to_needs_asr_without_leaking(
    monkeypatch, runtime_records
):
    dispatch, item, factory = _dispatch_rows()

    def broken(_dispatch_id):
        raise RuntimeError("BROKER-SECRET-TEXT redis://:password@host")

    monkeypatch.setattr("app.ingest.tasks.publish_asr_dispatch", broken)

    result = process_dispatch(
        71,
        task_id="ingest-task",
        processor=lambda _item_id: ASR_PENDING,
        session_factory=factory,
    )

    assert result == "asr_pending"
    assert (dispatch.state, dispatch.error_code) == ("completed", None)
    assert item.state == "needs_asr"
    payloads = _payloads(runtime_records)
    assert {"event": "asr_handoff_publish_failed", "error_code": "broker_unavailable"} in payloads
    rendered = json.dumps(payloads) + " ".join(
        record.getMessage() for record in runtime_records
    )
    assert "BROKER-SECRET-TEXT" not in rendered and "password" not in rendered


def test_handoff_is_a_noop_when_the_dispatch_is_not_ours(monkeypatch):
    dispatch, _item_row, factory = _dispatch_rows(dispatch_state="completed")
    monkeypatch.setattr(
        "app.ingest.tasks.publish_asr_dispatch",
        lambda _id: pytest.fail("must not publish for a foreign dispatch"),
    )

    assert _handoff_dispatch_to_asr(71, "ingest-task", session_factory=factory) is False
    assert dispatch.state == "completed"

    dispatch.state = "running"
    dispatch.task_id = "another-task"
    assert _handoff_dispatch_to_asr(71, "ingest-task", session_factory=factory) is False
    assert (dispatch.state, dispatch.task_id) == ("running", "another-task")


def test_default_dispatch_processor_requests_the_asr_handoff(monkeypatch):
    _dispatch, item, factory = _dispatch_rows()
    calls = []

    def fake_process_item(item_id, **kwargs):
        calls.append((item_id, kwargs))
        item.state = "ready"
        return "ready"

    monkeypatch.setattr("app.ingest.tasks.process_item", fake_process_item)

    assert process_dispatch(71, task_id="t", session_factory=factory) == "ready"
    assert calls == [(41, {"asr_handoff": True})]


def test_asr_error_marks_the_dispatch_failed_with_its_stable_code():
    dispatch, item, factory = _dispatch_rows(dispatch_state="enqueued")
    attempts = []

    def no_speech(item_id):
        attempts.append(item_id)
        raise AsrError("asr_no_speech")

    with pytest.raises(RuntimeError) as caught:
        process_dispatch(
            71, task_id="asr-task", processor=no_speech, session_factory=factory
        )

    assert str(caught.value) == "asr_no_speech"
    assert attempts == [41]
    assert (dispatch.state, dispatch.error_code) == ("failed", "asr_no_speech")
    assert (item.state, item.fail_reason) == ("failed", "asr_no_speech")


def test_transient_error_releases_the_dispatch_for_a_celery_retry():
    dispatch, item, factory = _dispatch_rows(dispatch_state="enqueued")

    def rate_limited(_item_id):
        raise TransientFetchError("youtube_rate_limited PRIVATE")

    with pytest.raises(TransientFetchError) as caught:
        process_dispatch(
            71, task_id="asr-task", processor=rate_limited, session_factory=factory
        )

    assert str(caught.value) == "transient_fetch_failed"
    assert (dispatch.state, dispatch.task_id) == ("enqueued", "asr-task")
    assert item.state == "fetching"


# ---------------------------------------------------------------------------
# The asr task, queue routing, publishing and error-code allow-lists
# ---------------------------------------------------------------------------


def test_transcribe_task_declares_queue_retries_and_time_limits():
    assert transcribe_dispatch_task.max_retries == 5
    assert transcribe_dispatch_task.retry_backoff == 8
    assert transcribe_dispatch_task.retry_backoff_max == 600
    assert transcribe_dispatch_task.time_limit == 3600
    assert transcribe_dispatch_task.soft_time_limit == 3480
    assert TransientFetchError in transcribe_dispatch_task.autoretry_for
    assert {queue.name for queue in celery_app.conf.task_queues} >= {
        "ingest",
        "maintenance",
        "asr",
    }
    route = celery_app.amqp.router.route(
        {}, "app.ingest.tasks.transcribe_dispatch_task"
    )
    assert route["queue"].name == "asr"
    fetch_route = celery_app.amqp.router.route({}, "app.ingest.tasks.fetch_text_task")
    assert fetch_route["queue"].name == "ingest"


def test_transcribe_task_runs_the_asr_processor_under_its_own_task_id(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.ingest.tasks.process_dispatch",
        lambda dispatch_id, **kwargs: calls.append((dispatch_id, kwargs)) or "ready",
    )
    transcribe_dispatch_task.push_request(id="asr-task-id")
    try:
        assert transcribe_dispatch_task.run(71) == "ready"
    finally:
        transcribe_dispatch_task.pop_request()

    assert calls == [(71, {"task_id": "asr-task-id", "processor": _asr_processor})]


def test_publish_asr_dispatch_sends_only_the_dispatch_id(monkeypatch):
    from kombu import Connection

    calls = []

    class Result:
        id = "asr-task-id"

    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **_kwargs: Connection("memory://"),
    )
    monkeypatch.setattr(
        transcribe_dispatch_task,
        "apply_async",
        lambda *, args, **kwargs: calls.append((args, kwargs)) or Result(),
    )

    assert publish_asr_dispatch(71) == "asr-task-id"
    assert calls[0][0] == [71]
    assert calls[0][1]["retry"] is True
    assert calls[0][1]["timeout"] > 0


def test_asr_processor_wraps_the_platform_connector(monkeypatch):
    item = _item()
    inner = Inner()
    captured = {}

    class Store:
        pass

    class Transcriber:
        def __init__(self, **kwargs):
            captured["transcriber"] = kwargs

    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    monkeypatch.setattr(
        "app.ingest.tasks.get_session_factory", lambda: (lambda: ItemDB(item))
    )
    monkeypatch.setattr("app.ingest.tasks._connector", lambda url: inner)
    monkeypatch.setattr("app.ingest.tasks.RawObjectStore", Store)
    monkeypatch.setattr("app.ingest.tasks.AsrTranscriber", Transcriber)
    monkeypatch.setattr(
        "app.ingest.tasks.process_item",
        lambda item_id, **kwargs: captured.update(item_id=item_id, **kwargs)
        or "ready",
    )

    assert _asr_processor(41) == "ready"

    connector = captured["connector"]
    assert isinstance(connector, AsrFallbackConnector)
    assert connector._inner is inner
    assert connector.platform == "youtube"
    assert isinstance(captured["transcriber"]["object_store"], Store)
    assert "asr_handoff" not in captured  # the asr worker never hands off again


def test_asr_processor_degrades_to_plain_processing_when_asr_was_disabled(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        "app.ingest.tasks.get_settings", lambda: _settings(asr_enabled=False)
    )
    monkeypatch.setattr(
        "app.ingest.tasks._connector",
        lambda _url: pytest.fail("no ASR wrapper when ASR is disabled"),
    )
    monkeypatch.setattr(
        "app.ingest.tasks.process_item",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "needs_asr",
    )

    assert _asr_processor(41) == "needs_asr"
    assert calls == [((41,), {})]


def test_asr_error_codes_are_allow_listed_for_web_and_diagnostics(runtime_records):
    for code in ASR_ERROR_CODES:
        assert _safe_error(code) == code
    assert _safe_error("provider said: sk-secret") == "ingestion_failed"

    from app.diagnostics import _ERRORS

    assert ASR_ERROR_CODES <= _ERRORS

    _completion_diagnostic(
        "completion_event_created",
        event_id=3,
        outcome="failed",
        item_state="failed",
        error_code="asr_no_speech",
    )
    _completion_diagnostic("probe", error_code="https://leak.example/secret")
    payloads = _payloads(runtime_records)
    assert payloads[0]["error_code"] == "asr_no_speech"
    assert "error_code" not in payloads[1]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, capsys, argv, *, asr_enabled=True, result=None):
    calls = []
    monkeypatch.setattr("sys.argv", ["stashseek", *argv])
    monkeypatch.setattr(
        cli, "get_settings", lambda: SimpleNamespace(asr_enabled=asr_enabled)
    )

    def fake_backfill(**kwargs):
        calls.append(kwargs)
        return result or asr_backfill.BackfillResult((), (), (), (), False)

    monkeypatch.setattr(cli, "backfill_needs_asr", fake_backfill)
    try:
        cli.main()
        code = 0
    except SystemExit as exc:
        code = exc.code
    return calls, code, capsys.readouterr()


def test_backfill_command_refuses_when_asr_is_disabled(monkeypatch, capsys):
    calls, code, _out = _run_cli(
        monkeypatch, capsys, ["asr-backfill", "--dry-run"], asr_enabled=False
    )

    assert calls == []
    assert code == "ASR is disabled; set ASR_ENABLED=true to backfill"


def test_backfill_command_defaults_and_prints_only_counts_and_item_ids(
    monkeypatch, capsys
):
    result = asr_backfill.BackfillResult((4, 5, 6), (4,), (5,), (6,), False)
    calls, code, out = _run_cli(
        monkeypatch, capsys, ["asr-backfill"], result=result
    )

    assert calls == [{"limit": 20, "user_id": None, "dry_run": False}]
    assert code == 2  # a publish failure is a non-zero exit
    assert out.out.splitlines() == [
        "selected=3 queued=1 skipped=1 failed=1 dry_run=false",
        "item=4 queued",
        "item=5 skipped",
        "item=6 failed",
    ]


def test_backfill_command_dry_run_passes_options_through(monkeypatch, capsys):
    result = asr_backfill.BackfillResult((9,), (), (), (), True)
    calls, code, out = _run_cli(
        monkeypatch,
        capsys,
        ["asr-backfill", "--limit", "500", "--user-id", "3", "--dry-run"],
        result=result,
    )

    assert calls == [{"limit": 500, "user_id": 3, "dry_run": True}]
    assert code == 0
    assert out.out.splitlines() == [
        "selected=1 queued=0 skipped=0 failed=0 dry_run=true",
        "item=9 selected",
    ]


@pytest.mark.parametrize("limit", ["0", "501", "-3", "many"])
def test_backfill_command_rejects_out_of_range_limits(monkeypatch, capsys, limit):
    calls, code, _out = _run_cli(
        monkeypatch, capsys, ["asr-backfill", "--limit", limit]
    )

    assert calls == []
    assert code == 2  # argparse usage error


@pytest.mark.parametrize("limit", [0, 501, True])
def test_backfill_function_rejects_out_of_range_limits(limit):
    with pytest.raises(ValueError):
        asr_backfill.backfill_needs_asr(limit=limit, session_factory=lambda: None)


# ---------------------------------------------------------------------------
# Real PostgreSQL rows (isolated schema on the test branch)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_factory():
    engine = get_engine()
    schema = f"test_asr_{uuid4().hex}"
    tables = [
        AppUser.__table__,
        ChannelIdentity.__table__,
        ConversationThread.__table__,
        ContentItem.__table__,
        IngestDispatch.__table__,
        IngestCompletionEvent.__table__,
        BrowserCapture.__table__,
        Segment.__table__,
    ]
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            Base.metadata.create_all(connection, tables=tables, checkfirst=False)
    except Exception as exc:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            pytest.skip(
                f"isolated PostgreSQL schema unavailable: {type(exc).__name__}"
            )

    def factory():
        db = Session(bind=engine, expire_on_commit=False)

        # SET LOCAL lasts one transaction, and ``process_item`` commits
        # several times per session, so re-apply it whenever one begins.
        @event.listens_for(db, "after_begin")
        def _search_path(_session, _transaction, connection):
            connection.exec_driver_sql(
                f'SET LOCAL search_path TO "{schema}", public'
            )

        return db

    try:
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture
def worker_env(monkeypatch):
    """Patch settings and broker-facing calls; record asr and notification sends."""

    env = SimpleNamespace(asr_published=[], notified=[], publish_error=None)

    def publish(dispatch_id):
        if env.publish_error is not None:
            raise env.publish_error
        env.asr_published.append(dispatch_id)
        return "asr-task-id"

    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: _settings())
    monkeypatch.setattr("app.ingest.tasks.publish_asr_dispatch", publish)
    monkeypatch.setattr(
        "app.ingest.tasks.enqueue_ingest_notification",
        lambda event_id, **_kw: env.notified.append(event_id) or "notification-id",
    )
    return env


def _seed(
    factory,
    *,
    state="pending",
    platform="youtube",
    dispatch_state="pending",
    user_id=None,
    with_dispatch=True,
    **item_overrides,
):
    with factory() as db:
        if user_id is None:
            user = AppUser()
            db.add(user)
            db.flush()
            user_id = user.id
        item = ContentItem(
            public_id=uuid4().hex,
            user_id=user_id,
            platform=platform,
            platform_id=f"v{uuid4().hex[:10]}",
            kind="video",
            url=CANONICAL,
            state=state,
            **item_overrides,
        )
        db.add(item)
        db.flush()
        dispatch_id = None
        if with_dispatch:
            dispatch = IngestDispatch(
                public_id=uuid4().hex,
                item_id=item.id,
                request_key=uuid4().hex,
                attempt=1,
                state=dispatch_state,
            )
            db.add(dispatch)
            db.flush()
            dispatch_id = dispatch.id
        db.commit()
        return user_id, item.id, dispatch_id


def _events(factory, dispatch_id):
    with factory() as db:
        return list(
            db.scalars(
                select(IngestCompletionEvent).where(
                    IngestCompletionEvent.dispatch_id == dispatch_id
                )
            )
        )


def _row(factory, model, row_id):
    with factory() as db:
        return db.get(model, row_id)


def _ingest_phase(factory, connector):
    return lambda current: process_item(
        current,
        connector=connector,
        asr_handoff=True,
        session_factory=factory,
    )


def test_asr_flow_end_to_end_creates_exactly_one_ready_event(pg_factory, worker_env):
    user_id, item_id, dispatch_id = _seed(pg_factory)

    # 1. ingest queue: no caption track -> same dispatch moves to the asr queue.
    assert (
        process_dispatch(
            dispatch_id,
            task_id="ingest-task",
            processor=_ingest_phase(pg_factory, Inner()),
            session_factory=pg_factory,
        )
        == "asr_pending"
    )
    dispatch = _row(pg_factory, IngestDispatch, dispatch_id)
    item = _row(pg_factory, ContentItem, item_id)
    assert (dispatch.state, dispatch.task_id) == ("enqueued", None)
    assert item.state == "fetching"
    assert worker_env.asr_published == [dispatch_id]
    assert _events(pg_factory, dispatch_id) == []
    assert worker_env.notified == []

    # 2. asr queue: transcribe, store capture_v1 and finish.
    audio_store, raw_store = AudioStore(), RawStore()
    funasr = FunAsr()

    def asr_phase(current):
        return process_item(
            current,
            connector=_asr_connector(funasr=funasr, audio_store=audio_store),
            embedder=Embedder(),
            object_store=raw_store,
            session_factory=pg_factory,
        )

    assert (
        process_dispatch(
            dispatch_id,
            task_id="asr-task",
            processor=asr_phase,
            session_factory=pg_factory,
        )
        == "ready"
    )

    dispatch = _row(pg_factory, IngestDispatch, dispatch_id)
    item = _row(pg_factory, ContentItem, item_id)
    assert (dispatch.state, dispatch.task_id, dispatch.error_code) == (
        "completed",
        "asr-task",
        None,
    )
    assert item.state == "ready"
    assert item.fail_reason is None
    assert item.raw_format == "capture_v1"
    assert item.text_source == "asr_whisper"
    assert item.raw_object_key.startswith(f"{user_id}/youtube/")
    assert item.raw_object_key.endswith(".capture.json")
    assert raw_store.puts[0][0] == item.raw_object_key
    assert raw_store.puts[0][2] == "application/json"
    with pg_factory() as db:
        segments = list(db.scalars(select(Segment).where(Segment.item_id == item_id)))
        dims = db.scalar(
            text("select vector_dims(embedding) from segment where item_id = :i limit 1"),
            {"i": item_id},
        )
    assert len(segments) >= 1
    assert dims == 1536
    assert SPOKEN in segments[0].text

    events = _events(pg_factory, dispatch_id)
    assert len(events) == 1
    assert (events[0].outcome, events[0].item_state, events[0].error_code) == (
        "completed",
        "ready",
        None,
    )
    assert worker_env.notified == [events[0].id]
    # The signed URL was handed to the provider, then the audio was deleted.
    assert len(funasr.urls) == 1
    assert audio_store.deleted == audio_store.puts and len(audio_store.puts) == 1

    # A duplicate delivery of either task changes nothing.
    for task_id in ("ingest-task", "asr-task"):
        assert (
            process_dispatch(
                dispatch_id,
                task_id=task_id,
                processor=lambda _i: pytest.fail("terminal dispatch re-ran"),
                session_factory=pg_factory,
            )
            == "duplicate"
        )
    assert len(_events(pg_factory, dispatch_id)) == 1
    assert len(worker_env.notified) == 1


def test_handoff_publish_failure_creates_one_needs_asr_event(pg_factory, worker_env):
    _user_id, item_id, dispatch_id = _seed(pg_factory)
    worker_env.publish_error = RuntimeError("broker down")

    assert (
        process_dispatch(
            dispatch_id,
            task_id="ingest-task",
            processor=_ingest_phase(pg_factory, Inner()),
            session_factory=pg_factory,
        )
        == "asr_pending"
    )

    dispatch = _row(pg_factory, IngestDispatch, dispatch_id)
    item = _row(pg_factory, ContentItem, item_id)
    assert (dispatch.state, dispatch.error_code) == ("completed", None)
    assert item.state == "needs_asr"
    events = _events(pg_factory, dispatch_id)
    assert len(events) == 1
    assert (events[0].outcome, events[0].item_state, events[0].error_code) == (
        "completed",
        "needs_asr",
        None,
    )
    assert worker_env.notified == [events[0].id]

    # A message that did reach the broker before the error is a safe duplicate.
    assert (
        process_dispatch(
            dispatch_id,
            task_id="asr-late",
            processor=lambda _i: pytest.fail("converged dispatch re-ran"),
            session_factory=pg_factory,
        )
        == "duplicate"
    )
    assert len(_events(pg_factory, dispatch_id)) == 1


def test_handoff_publish_failure_never_overwrites_a_running_asr_claim(
    pg_factory, worker_env, monkeypatch
):
    _user_id, item_id, dispatch_id = _seed(
        pg_factory, state="fetching", dispatch_state="running"
    )
    with pg_factory() as db:
        db.get(IngestDispatch, dispatch_id).task_id = "ingest-task"
        db.commit()

    def publish_then_fast_worker_claims(_dispatch_id):
        # The asr worker received the message and claimed it before the
        # publisher noticed its own error.
        with pg_factory() as db:
            claimed = db.get(IngestDispatch, dispatch_id)
            claimed.state = "running"
            claimed.task_id = "asr-task"
            db.commit()
        raise RuntimeError("timeout after the message was sent")

    monkeypatch.setattr(
        "app.ingest.tasks.publish_asr_dispatch", publish_then_fast_worker_claims
    )

    assert not _handoff_dispatch_to_asr(
        dispatch_id, "ingest-task", session_factory=pg_factory
    )

    dispatch = _row(pg_factory, IngestDispatch, dispatch_id)
    assert (dispatch.state, dispatch.task_id) == ("running", "asr-task")
    assert _row(pg_factory, ContentItem, item_id).state == "fetching"
    assert _events(pg_factory, dispatch_id) == []
    assert worker_env.notified == []


def test_no_speech_fails_the_dispatch_with_a_stable_code_and_one_event(
    pg_factory, worker_env
):
    _user_id, item_id, dispatch_id = _seed(
        pg_factory, state="fetching", dispatch_state="enqueued"
    )
    audio_store, raw_store = AudioStore(), RawStore()
    silent = FunAsr(cues=[AsrCue(0.0, 1.0, "   ")])

    def asr_phase(current):
        return process_item(
            current,
            connector=_asr_connector(funasr=silent, audio_store=audio_store),
            embedder=Embedder(),
            object_store=raw_store,
            session_factory=pg_factory,
        )

    with pytest.raises(RuntimeError) as caught:
        process_dispatch(
            dispatch_id,
            task_id="asr-task",
            processor=asr_phase,
            session_factory=pg_factory,
        )

    assert str(caught.value) == "asr_no_speech"
    dispatch = _row(pg_factory, IngestDispatch, dispatch_id)
    item = _row(pg_factory, ContentItem, item_id)
    assert (dispatch.state, dispatch.error_code) == ("failed", "asr_no_speech")
    assert (item.state, item.fail_reason) == ("failed", "asr_no_speech")
    events = _events(pg_factory, dispatch_id)
    assert len(events) == 1
    assert (events[0].outcome, events[0].item_state, events[0].error_code) == (
        "failed",
        "failed",
        "asr_no_speech",
    )
    assert worker_env.notified == [events[0].id]
    assert raw_store.puts == []
    assert audio_store.deleted == audio_store.puts and len(audio_store.puts) == 1


def test_transient_download_error_releases_without_an_event(pg_factory, worker_env):
    _user_id, _item_id, dispatch_id = _seed(
        pg_factory, state="fetching", dispatch_state="enqueued"
    )

    def rate_limited(_item_id):
        raise TransientFetchError("youtube_rate_limited")

    with pytest.raises(TransientFetchError):
        process_dispatch(
            dispatch_id,
            task_id="asr-task",
            processor=rate_limited,
            session_factory=pg_factory,
        )

    dispatch = _row(pg_factory, IngestDispatch, dispatch_id)
    assert (dispatch.state, dispatch.task_id) == ("enqueued", "asr-task")
    assert _events(pg_factory, dispatch_id) == []
    assert worker_env.notified == []


# ---------------------------------------------------------------------------
# Backfill against real rows
# ---------------------------------------------------------------------------


def _capture_row(factory, user_id, item_id, dispatch_id):
    with factory() as db:
        db.add(
            BrowserCapture(
                public_id=uuid4().hex,
                app_user_id=user_id,
                item_id=item_id,
                dispatch_id=dispatch_id,
                request_key=uuid4().hex,
                body_hash="0" * 64,
                protocol_version="capture.v1",
                client_version="test",
                caption_status="unavailable",
                capture_metadata={},
                state="ready",
            )
        )
        db.commit()


def test_backfill_selects_only_public_needs_asr_items_oldest_first(pg_factory):
    user_id, wanted_a, _ = _seed(pg_factory, state="needs_asr", with_dispatch=False)
    _, wanted_b, _ = _seed(
        pg_factory,
        state="needs_asr",
        platform="bilibili",
        user_id=user_id,
        with_dispatch=False,
    )
    _seed(pg_factory, state="needs_asr", platform="ntu_kaltura", user_id=user_id, with_dispatch=False)
    _seed(pg_factory, state="ready", user_id=user_id, with_dispatch=False)
    _seed(pg_factory, state="needs_asr", user_id=user_id, with_dispatch=False, deleted_at=func.now())
    _seed(pg_factory, state="needs_asr", user_id=user_id, with_dispatch=False, archived_at=func.now())
    _, captured, captured_dispatch = _seed(
        pg_factory, state="needs_asr", user_id=user_id, dispatch_state="completed"
    )
    _capture_row(pg_factory, user_id, captured, captured_dispatch)
    other_user_item = _seed(pg_factory, state="needs_asr", with_dispatch=False)[1]

    with pg_factory() as db:
        mine = asr_backfill.select_backfill_item_ids(db, limit=50, user_id=user_id)
        limited = asr_backfill.select_backfill_item_ids(db, limit=1, user_id=user_id)
        everyone = asr_backfill.select_backfill_item_ids(db, limit=500)

    assert mine == [wanted_a, wanted_b]
    assert limited == [wanted_a]
    assert other_user_item in everyone and captured not in everyone


def test_backfill_dry_run_changes_nothing_and_never_publishes(pg_factory):
    user_id, item_id, _ = _seed(pg_factory, state="needs_asr", with_dispatch=False)

    result = asr_backfill.backfill_needs_asr(
        limit=5,
        user_id=user_id,
        dry_run=True,
        session_factory=pg_factory,
        publisher=lambda _id: pytest.fail("dry run must not publish"),
    )

    assert (result.selected, result.queued, result.dry_run) == ((item_id,), (), True)
    assert _row(pg_factory, ContentItem, item_id).state == "needs_asr"
    with pg_factory() as db:
        assert db.scalar(
            select(IngestDispatch).where(IngestDispatch.item_id == item_id)
        ) is None


def test_backfill_creates_a_fresh_dispatch_and_publishes_it(pg_factory):
    user_id, item_id, old_dispatch = _seed(
        pg_factory, state="needs_asr", dispatch_state="completed"
    )
    published = []

    result = asr_backfill.backfill_needs_asr(
        limit=5,
        user_id=user_id,
        session_factory=pg_factory,
        publisher=lambda dispatch_id: published.append(dispatch_id) or "task-1",
    )

    assert result.queued == (item_id,) and result.failed == () and result.skipped == ()
    assert len(published) == 1 and published[0] != old_dispatch
    dispatch = _row(pg_factory, IngestDispatch, published[0])
    assert (dispatch.item_id, dispatch.state, dispatch.task_id) == (
        item_id,
        "enqueued",
        "task-1",
    )
    assert dispatch.attempt == 2
    assert dispatch.request_key.startswith("asr-backfill-")
    assert dispatch.source_thread_id is None
    assert _row(pg_factory, ContentItem, item_id).state == "pending"

    # The item is now active, so a second run selects nothing for this user.
    again = asr_backfill.backfill_needs_asr(
        limit=5,
        user_id=user_id,
        session_factory=pg_factory,
        publisher=lambda _id: pytest.fail("no item should be selected"),
    )
    assert again.selected == ()


def test_backfill_respects_the_limit_and_skips_active_dispatches(pg_factory):
    user_id, first, _ = _seed(pg_factory, state="needs_asr", with_dispatch=False)
    _, second, _ = _seed(pg_factory, state="needs_asr", user_id=user_id, with_dispatch=False)
    _, busy, _busy_dispatch = _seed(
        pg_factory, state="needs_asr", user_id=user_id, dispatch_state="running"
    )
    published = []

    limited = asr_backfill.backfill_needs_asr(
        limit=2,
        user_id=user_id,
        session_factory=pg_factory,
        publisher=lambda dispatch_id: published.append(dispatch_id) or None,
    )
    assert limited.selected == (first, second) and limited.queued == (first, second)
    assert len(published) == 2

    # ``busy`` is selected (state needs_asr) but has an active dispatch.
    rest = asr_backfill.backfill_needs_asr(
        limit=5,
        user_id=user_id,
        session_factory=pg_factory,
        publisher=lambda _id: pytest.fail("an active dispatch must not be duplicated"),
    )
    assert rest.selected == (busy,) and rest.skipped == (busy,) and rest.queued == ()


def test_backfill_publish_failure_restores_needs_asr_and_is_reported(pg_factory):
    user_id, item_id, _ = _seed(pg_factory, state="needs_asr", with_dispatch=False)

    def broker_down(_dispatch_id):
        raise RuntimeError("redis://:password@broker")

    result = asr_backfill.backfill_needs_asr(
        limit=1,
        user_id=user_id,
        session_factory=pg_factory,
        publisher=broker_down,
    )

    assert result.failed == (item_id,) and result.queued == ()
    assert _row(pg_factory, ContentItem, item_id).state == "needs_asr"
    with pg_factory() as db:
        dispatch = db.scalar(
            select(IngestDispatch).where(IngestDispatch.item_id == item_id)
        )
    assert (dispatch.state, dispatch.error_code) == ("failed", "queue_unavailable")
    # The operator can simply run it again.
    retry = asr_backfill.backfill_needs_asr(
        limit=1,
        user_id=user_id,
        session_factory=pg_factory,
        publisher=lambda _id: "task-2",
    )
    assert retry.queued == (item_id,)
    with pg_factory() as db:
        attempts = sorted(
            db.scalars(
                select(IngestDispatch.attempt).where(IngestDispatch.item_id == item_id)
            )
        )
    assert attempts == [1, 2]
