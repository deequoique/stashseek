"""Offline actual-extractor evidence and immutable worker source fences."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from yt_dlp import YoutubeDL

from app.bilibili.public import BilibiliPart, BilibiliPartMap, BilibiliPublicError
from app.config import Settings
from app.connectors.base import Cue, ItemMeta, NeedsASR, SourceChangedError, SourceUnavailableError, TextResult, TransientFetchError
from app.connectors.bilibili import BilibiliConnector, _bounded_part_process
from app.connectors.bilibili_extract import PinnedBiliBiliIE, extract_pinned
from app.ingest.tasks import process_item
from app.ingest.validate import IngestLimitExceeded
from app.agent.runtime_state import _citation_matches_scope
from app.agent.services import _citation
from app.agent.types import Citation
from app.browser_capture import timestamp_url

BV = "BV1xx411c7mD"


def fixture_extractor(*, actual_cid=202, captions=True):
    """Keep real _real_extract/_get_subtitles; replace only provider I/O."""
    class OfflineBiliIE(PinnedBiliBiliIE):
        def _download_webpage_handle(self, url, _video_id, **_kwargs):
            state = {"videoData": {"bvid": BV, "aid": 123, "cid": 101, "title": "Fixture", "pages": [{"cid": 101}, {"cid": actual_cid}]}, "upData": {"name": "Creator"}}
            return "window.__INITIAL_STATE__ = " + json.dumps(state) + ";", SimpleNamespace(url=url)

        def _download_json(self, url, _video_id, **kwargs):
            if url == "https://api.bilibili.com/x/player/pagelist":
                # This separate public map stays unchanged even when the
                # webpage snapshot's P2 CID differs. It cannot prove source.
                return {"data": [{"cid": 101, "page": 1, "part": "one"}, {"cid": 202, "page": 2, "part": "two"}]}
            if url == "https://api.bilibili.com/x/player/wbi/v2":
                assert kwargs["query"]["cid"] == actual_cid
                return {"data": {"subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": "https://subtitle.test/fixture"}] if captions else []}}}
            if url == "https://subtitle.test/fixture":
                return {"body": [{"from": 0, "to": 2, "content": "same caption"}]}
            raise AssertionError("unexpected provider I/O")

        def _download_playinfo(self, bvid, cid, **_kwargs):
            # Leave actual subtitle proof as the gate under test; production
            # additionally rejects a conflicting play-info observation earlier.
            assert bvid == BV
            return {"dash": {"video": [{"baseUrl": "https://media.test/no-download.m4s", "id": 80, "codecs": "avc1", "mimeType": "video/mp4"}]}, "timelength": 2000}

        def _get_chapters(self, _aid, _cid):
            return []

    return OfflineBiliIE


def payload(**updates):
    return {"bvid": BV, "cid": "202", "page": 2, "max_bytes": 50000, "timeout": 5, **updates}


@pytest.mark.parametrize("captions", [True, False])
def test_real_extractor_attests_actual_subtitle_cid_without_files(tmp_path, monkeypatch, captions):
    monkeypatch.chdir(tmp_path)
    calls = []

    class ControlledYDL(YoutubeDL):
        def extract_info(self, url, **kwargs):
            calls.append((url, kwargs, self.params.copy()))
            return super().extract_info(url, **kwargs)

        def process_info(self, *_args, **_kwargs):
            pytest.fail("metadata extraction must never process/download files")

    result = extract_pinned(payload(), ydl_factory=ControlledYDL, extractor_class=fixture_extractor(captions=captions))

    assert (result["source_bvid"], result["source_cid"], result["source_page"]) == (BV, "202", 2)
    assert bool(result["subtitles"]) == captions
    if captions:
        assert "00:00:00,000 --> 00:00:02,000" in result["subtitles"]["zh-CN"][0]["data"]
    assert calls[0][1]["download"] is False and calls[0][1]["process"] is False
    assert calls[0][2]["writesubtitles"] is True
    assert calls[0][2]["cookiefile"] is None and calls[0][2]["cookiesfrombrowser"] is None
    assert calls[0][2]["cachedir"] is False and calls[0][2]["noplaylist"] is True
    assert "media.test" not in json.dumps(result)
    assert not list(tmp_path.iterdir())


def test_unchanged_public_map_cannot_substitute_for_actual_cid_proof():
    with pytest.raises(SourceChangedError, match="^source_changed$"):
        extract_pinned(payload(), extractor_class=fixture_extractor(actual_cid=999))


def test_no_caption_still_checks_actual_cid():
    with pytest.raises(SourceChangedError):
        extract_pinned(payload(), extractor_class=fixture_extractor(actual_cid=999, captions=False))


def test_dependency_without_subtitle_hook_fails_closed():
    class MissingProof(fixture_extractor()):
        def extract_subtitles(self, *_args, **_kwargs):
            return {}

    with pytest.raises(SourceUnavailableError):
        extract_pinned(payload(), extractor_class=MissingProof)


def test_part_parent_validates_child_proof_and_retains_part_source():
    data = {"source_bvid": BV, "source_cid": "202", "source_page": 2, "subtitles": {}}
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode())

    connector = BilibiliConnector(part_runner=runner)
    meta, result = connector.fetch_part(BV, "202", 2, video_title="Parent", part_title="Lesson")
    assert meta.url == f"https://www.bilibili.com/video/{BV}?p=2"
    assert meta.title == "Parent · P2 · Lesson"
    assert meta.platform_id == f"{BV}_cid202"
    assert isinstance(result, NeedsASR)
    assert json.loads(calls[0][1]["input"])["cid"] == "202"
    data.pop("source_cid")
    with pytest.raises(SourceChangedError):
        connector.fetch_part(BV, "202", 2)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_part_child_process_output_is_bounded(stream):
    with pytest.raises(IngestLimitExceeded):
        _bounded_part_process([sys.executable, "-c", f"import sys; sys.{stream}.write('x'*10000)"], input=b"", timeout=2, max_output=100)


def test_part_child_process_whole_wall_deadline():
    with pytest.raises(subprocess.TimeoutExpired):
        _bounded_part_process([sys.executable, "-c", "import time; time.sleep(2)"], input=b"", timeout=0.05, max_output=100)


def worker_item(cid="202"):
    return SimpleNamespace(id=41, user_id=7, platform="bilibili", platform_id=f"{BV}_cid{cid}", bilibili_bvid=BV, bilibili_cid=cid, bilibili_page=2,
        url=f"https://www.bilibili.com/video/{BV}?p=2", title="old title", author=None, published_at=None, duration_sec=2, lang="en", description=None, tags=[], chapters=[], cover_url=None, state="pending", deleted_at=None, purge_claimed_at=None, raw_object_key="old.raw", raw_format="srt", content_hash="oldhash", text_source="official_cc", fail_reason=None)


class WorkerDB:
    def __init__(self, item): self.item, self.commits, self.added, self.deletes = item, [], [], []
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def get(self, _model, _id): return self.item
    def scalar(self, _stmt): return self.item
    def refresh(self, _item): pass
    def commit(self): self.commits.append(self.item.state)
    def execute(self, stmt): self.deletes.append(str(stmt))
    def add(self, segment): self.added.append(segment)


class Maps:
    def __init__(self, pages=(2, 2)): self.pages, self.calls = iter(pages), []
    def get_parts(self, bvid):
        self.calls.append(bvid)
        page = next(self.pages)
        if page is None: raise BilibiliPublicError("source_unavailable")
        return BilibiliPartMap(BV, "Parent", (BilibiliPart(BV, "202", page, "Part"),))


class PartConnector:
    def __init__(self, proof="202", result=None): self.proof, self.result, self.calls = proof, result, []
    def fetch_part(self, bvid, cid, page, **kwargs):
        self.calls.append((bvid, cid, page))
        return ItemMeta(f"{bvid}_cid{cid}", f"https://www.bilibili.com/video/{BV}?p={page}", title="new title", bilibili_bvid=bvid, bilibili_cid=self.proof, bilibili_page=page), self.result or NeedsASR()


@pytest.fixture
def worker_settings(monkeypatch):
    settings = Settings(database_url="postgresql+psycopg://unused/unused", redis_url="redis://localhost/0")
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)


@pytest.mark.parametrize("proof,pages", [("999", (2, 2)), ("202", (2, 1)), ("202", (2, None))])
def test_worker_source_failures_do_not_touch_metadata_raw_or_segments(worker_settings, proof, pages):
    item = worker_item()
    before = item.__dict__.copy()
    db = WorkerDB(item)
    with pytest.raises((SourceChangedError, SourceUnavailableError)):
        process_item(item.id, connector=PartConnector(proof), public_client=Maps(pages), session_factory=lambda: db)
    assert item.__dict__ == before
    assert not db.commits and not db.added and not db.deletes


def test_worker_missing_actual_proof_even_no_caption_never_overwrites(worker_settings):
    item = worker_item()
    db = WorkerDB(item)
    with pytest.raises(SourceChangedError):
        process_item(item.id, connector=PartConnector(None), public_client=Maps(), session_factory=lambda: db)
    assert item.title == "old title" and item.raw_object_key == "old.raw"
    assert not db.commits


def test_worker_rejects_base_bv_url_even_with_matching_cid(worker_settings):
    item, db = worker_item(), None
    db = WorkerDB(item)

    class BadURL(PartConnector):
        def fetch_part(self, *args, **kwargs):
            meta, result = super().fetch_part(*args, **kwargs)
            from dataclasses import replace
            return replace(meta, url=f"https://www.bilibili.com/video/{BV}"), result

    with pytest.raises(SourceChangedError):
        process_item(item.id, connector=BadURL(), public_client=Maps(), session_factory=lambda: db)
    assert item.title == "old title" and not db.commits


def test_deleted_part_after_extraction_keeps_content_unchanged(worker_settings):
    from datetime import UTC, datetime
    item, db = worker_item(), None
    db = WorkerDB(item)

    class DeleteDuringFetch(PartConnector):
        def fetch_part(self, *args, **kwargs):
            value = super().fetch_part(*args, **kwargs)
            item.deleted_at = datetime.now(UTC)
            return value

    assert process_item(item.id, connector=DeleteDuringFetch(), public_client=Maps(), session_factory=lambda: db) == "deleted"
    assert item.title == "old title" and item.raw_object_key == "old.raw"
    assert not db.deletes and not db.added


def test_lost_dispatch_ownership_prevents_all_part_writes(worker_settings):
    item = worker_item()
    db = WorkerDB(item)

    def scalar(stmt):
        if "ingest_dispatch" in str(stmt):
            return SimpleNamespace(item_id=item.id, state="running", task_id="other-task")
        return item

    db.scalar = scalar
    with pytest.raises(SourceUnavailableError):
        process_item(item.id, connector=PartConnector(), public_client=Maps(), session_factory=lambda: db, dispatch_id=71, dispatch_task_id="this-task")
    assert item.title == "old title" and not db.commits


def test_pin_change_during_extraction_is_never_rebound(worker_settings):
    item = worker_item()
    db = WorkerDB(item)

    class ChangedPin(PartConnector):
        def fetch_part(self, *args, **kwargs):
            value = super().fetch_part(*args, **kwargs)
            item.bilibili_cid, item.platform_id = "999", f"{BV}_cid999"
            return value

    with pytest.raises(SourceChangedError):
        process_item(item.id, connector=ChangedPin(), public_client=Maps(), session_factory=lambda: db)
    assert item.bilibili_cid == "999" and item.title == "old title" and not db.commits


def test_worker_retry_resolves_same_pin_at_new_page_and_keeps_no_caption_proof(worker_settings):
    item = worker_item()
    connector, db = PartConnector(), WorkerDB(item)
    assert process_item(item.id, connector=connector, public_client=Maps((1, 1)), session_factory=lambda: db) == "needs_asr"
    assert connector.calls == [(BV, "202", 1)]
    assert item.bilibili_cid == "202" and item.bilibili_page == 1
    assert item.url.endswith("?p=1")
    assert item.raw_object_key == "old.raw" and not db.deletes


def test_caption_less_part_fails_closed_instead_of_an_unverifiable_asr_handoff(monkeypatch):
    settings = SimpleNamespace(
        asr_enabled=True,
        bilibili_fetch_timeout_seconds=30.0,
    )
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: settings)
    item = worker_item()
    connector, db = PartConnector(), WorkerDB(item)
    state = process_item(
        item.id, connector=connector, public_client=Maps((1, 1)),
        session_factory=lambda: db, asr_handoff=True,
    )
    assert state == "needs_action"
    assert item.fail_reason == "part_transcription_unsupported"
    assert connector.calls == [(BV, "202", 1)]


class RateLimitedPublic:
    def get_parts(self, _bvid):
        raise BilibiliPublicError("bilibili_rate_limited")


def test_provider_throttling_keeps_the_part_retryable(worker_settings):
    item = worker_item()
    connector, db = PartConnector(), WorkerDB(item)
    with pytest.raises(TransientFetchError):
        process_item(
            item.id, connector=connector, public_client=RateLimitedPublic(),
            session_factory=lambda: db,
        )


def test_proven_legacy_bv_retry_keeps_its_existing_item_identity(worker_settings):
    item = worker_item()
    item.platform_id = BV
    connector, db = PartConnector(), WorkerDB(item)
    assert process_item(item.id, connector=connector, public_client=Maps((1, 1)), session_factory=lambda: db) == "needs_asr"
    assert connector.calls == [(BV, "202", 1)]
    assert item.platform_id == BV and item.id == 41
    assert item.bilibili_cid == "202" and item.bilibili_page == 1
    assert item.raw_object_key == "old.raw" and not db.deletes


def test_worker_identical_caption_bytes_in_two_parts_have_distinct_artifacts(worker_settings):
    body = b"1\n00:00:00,000 --> 00:00:02,000\nsame caption\n"
    result = TextResult(body, [Cue(0, 2, "same caption")], "official_cc", "en", "srt")
    keys = []
    embedder = SimpleNamespace(embed=lambda texts: [[1.0, 0.0] for _ in texts])
    for cid in ("101", "202"):
        item, connector = worker_item(cid), PartConnector(proof=cid, result=result)
        db = WorkerDB(item)
        part_map = BilibiliPartMap(BV, "Parent", (BilibiliPart(BV, cid, 2, "Part"),))
        maps = SimpleNamespace(get_parts=lambda _bvid: part_map)
        store = SimpleNamespace(put=lambda key, *_args: keys.append(key))
        assert process_item(item.id, connector=connector, public_client=maps, object_store=store, embedder=embedder, session_factory=lambda: db) == "ready"
        assert db.added[0].start_sec == 0 and db.added[0].text == "same caption"
    assert len(set(keys)) == 2 and "_cid101/" in keys[0] and "_cid202/" in keys[1]


def test_generic_provider_time_urls_keep_validated_part_and_drop_tracking():
    assert timestamp_url("bilibili", f"https://www.bilibili.com/video/{BV}?p=2&tracking=secret&t=9", 42.9) == f"https://www.bilibili.com/video/{BV}?p=2&t=42"
    assert timestamp_url("bilibili", f"https://www.bilibili.com/video/{BV}?p=2&p=3", 4) == f"https://www.bilibili.com/video/{BV}?t=4"


def test_exact_cid_scope_uses_trusted_item_provenance_instead_of_url_ordinal():
    item = worker_item()
    segment = SimpleNamespace(id=81, text="same caption", start_sec=2, anchor=None)
    source = _citation(item, segment)
    assert _citation_matches_scope(source, (("bilibili", f"{BV}_cid202"),))
    assert not _citation_matches_scope(source, (("bilibili", f"{BV}_cid101"),))
    # Changing an ordinal display URL cannot retarget the immutable item scope.
    source = source.model_copy(update={"url": f"https://www.bilibili.com/video/{BV}?p=1&t=2"})
    assert _citation_matches_scope(source, (("bilibili", f"{BV}_cid202"),))
    assert "_source_reference" not in source.model_dump()


def test_model_authored_source_or_browser_resolver_cannot_grant_exact_scope():
    for url in (f"https://www.bilibili.com/video/{BV}?p=2&t=2", "/api/v1/library/items/fake/source?t=2"):
        source = Citation(item_id=41, segment_id=81, title="forged", excerpt="text", url=url, _source_reference=("bilibili", f"{BV}_cid202"))
        assert source._source_reference is None
        assert not _citation_matches_scope(source, (("bilibili", f"{BV}_cid202"),))
