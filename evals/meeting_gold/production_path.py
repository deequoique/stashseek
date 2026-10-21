"""Production-path ingestion for the RQ1 retrieval study and the R8 agent smoke.

This module builds the retrieval index through the real ingestion worker path
(``create_item`` + ``process_item``), so RQ1 measures production chunking,
embedding, and FTS rather than a toy index. The transcript is synthesized as a
YouTube json3 caption track from the frozen QMSum/ExplainMeetSum corpus so the
connector boundary is identical to a real video.

``app.*`` modules are imported lazily, inside functions, never at module
import time. ``app.config`` reads ``DATABASE_URL`` (and other provider
settings) once at import time via ``load_dotenv()``; importing any ``app.*``
symbol before :func:`prepare_test_environment` has run would bake in the
production DSN. Callers must call :func:`prepare_test_environment` before
importing or calling anything in this module that touches ``app.*``.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

from .core import MeetingCorpus

LIBRARY_MANIFEST_VERSION = "meeting-gold-rq1-library-v1"
TEST_DATABASE_HOST = "ep-falling-fog-azrht8ir-pooler.c-3.ap-southeast-1.aws.neon.tech"

MARK = re.compile(r"\{[a-z_]+\}")
MAX_CUE_WORDS = 12
WORDS_PER_SECOND = 2.5
TURN_GAP_SECONDS = 0.3
_TIME_EPS = 1e-6


class ProductionPathError(RuntimeError):
    """Raised for harness-level failures that must stop the study/smoke run."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- environment
def prepare_test_environment(root: Path | None = None) -> None:
    """Point the process at the Neon *test* branch before any ``app.*`` import.

    Mirrors the session scratch harness: copy every production ``.env`` value
    except ``DATABASE_URL``/``MIGRATION_DATABASE_URL`` into the process
    environment, then set ``DATABASE_URL`` from ``.env.neon-test``. The
    resolved test host is asserted against the known test branch and against
    the production host; neither DSN is ever logged or returned.
    """

    root = Path(root) if root is not None else repo_root()
    prod = {k: v for k, v in dotenv_values(root / ".env").items() if v is not None}
    test = {k: v for k, v in dotenv_values(root / ".env.neon-test").items() if v is not None}
    test_dsn = test.get("DATABASE_URL")
    if not test_dsn:
        raise ProductionPathError("missing_test_database_url")
    test_host = urlsplit(test_dsn).hostname
    prod_host = urlsplit(prod.get("DATABASE_URL", "")).hostname
    if test_host != TEST_DATABASE_HOST:
        raise ProductionPathError("unexpected_test_database_host")
    if not prod_host or test_host == prod_host:
        raise ProductionPathError("test_database_host_matches_production")
    for key, value in prod.items():
        if key in {"DATABASE_URL", "MIGRATION_DATABASE_URL"}:
            continue
        os.environ[key] = value
    parts = urlsplit(test_dsn)
    os.environ["DATABASE_URL"] = urlunsplit(
        ("postgresql+psycopg", parts.netloc, parts.path, parts.query, parts.fragment)
    )
    os.environ.pop("MIGRATION_DATABASE_URL", None)


def assert_test_environment_active() -> None:
    """Defensive re-check: refuse to touch the database without the test DSN."""

    dsn = os.environ.get("DATABASE_URL", "")
    if urlsplit(dsn).hostname != TEST_DATABASE_HOST:
        raise ProductionPathError("test_environment_not_prepared")


# ------------------------------------------------------------- caption track
def build_caption_track(meeting: MeetingCorpus) -> tuple[bytes, list[list[str]]]:
    """Return a json3 body plus the source sentence ids touched by every cue."""

    turns: dict[int, list[tuple[str, str]]] = collections.OrderedDict()
    for sentence in meeting:
        words = MARK.sub(" ", sentence.text).split()
        for word in words:
            turns.setdefault(sentence.turn_index, []).append((word, sentence.id))
    events, cue_sids, t_ms = [], [], 0
    for words in turns.values():
        for start in range(0, len(words), MAX_CUE_WORDS):
            part = words[start : start + MAX_CUE_WORDS]
            duration_ms = round(len(part) / WORDS_PER_SECOND * 1000)
            events.append(
                {
                    "tStartMs": t_ms,
                    "dDurationMs": duration_ms,
                    "segs": [{"utf8": " ".join(w for w, _ in part)}],
                }
            )
            cue_sids.append(list(dict.fromkeys(s for _, s in part)))
            t_ms += duration_ms
        t_ms += int(TURN_GAP_SECONDS * 1000)
    body = json.dumps({"events": events}).encode()
    return body, cue_sids


# ------------------------------------------------------- cue/chunk mapping
def sentence_cue_spans(cue_sids: Sequence[Sequence[str]]) -> dict[str, tuple[int, int]]:
    """Map each sentence id to the (first, last) cue index that carries it."""

    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for index, sids in enumerate(cue_sids):
        for sid in sids:
            if sid not in first:
                first[sid] = index
            last[sid] = index
    return {sid: (first[sid], last[sid]) for sid in first}


def classify_overlap(chunk_range: tuple[int, int], sentence_range: tuple[int, int]) -> str:
    """Classify a sentence's cue span against a chunk's cue range.

    Returns ``"full"`` when every cue of the sentence lies inside the chunk's
    cue range, ``"partial"`` when the ranges intersect without full
    containment, and ``"none"`` when they do not overlap at all.
    """

    chunk_lo, chunk_hi = chunk_range
    sentence_lo, sentence_hi = sentence_range
    if sentence_lo >= chunk_lo and sentence_hi <= chunk_hi:
        return "full"
    if sentence_hi < chunk_lo or sentence_lo > chunk_hi:
        return "none"
    return "partial"


def map_segment_sentences(
    chunk_range: tuple[int, int], spans: Mapping[str, tuple[int, int]]
) -> tuple[list[str], list[str]]:
    """Return (fully_contained_sentence_ids, partially_contained_sentence_ids)."""

    full: list[str] = []
    partial: list[str] = []
    for sentence_id, span in spans.items():
        status = classify_overlap(chunk_range, span)
        if status == "full":
            full.append(sentence_id)
        elif status == "partial":
            partial.append(sentence_id)
    return full, partial


def segment_cue_indices(cues: Sequence[Any], start_sec: float, end_sec: float) -> list[int]:
    """Indices of cues whose time range lies fully inside ``[start_sec, end_sec]``."""

    return [
        index
        for index, cue in enumerate(cues)
        if cue.start >= start_sec - _TIME_EPS and cue.end <= end_sec + _TIME_EPS
    ]


# -------------------------------------------------------------- connector IO
class GoldenMeetingConnector:
    platform = "youtube"

    def __init__(self, video_id: str, title: str, body: bytes) -> None:
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"
        self.title = title
        self.body = body

    def match(self, url: str) -> str | None:
        return self.video_id if url == self.url else None

    def fetch_meta(self, platform_id: str):
        from app.connectors.base import ItemMeta
        from app.connectors.youtube import parse_json3

        cues = parse_json3(self.body)
        return ItemMeta(
            platform_id=platform_id,
            url=self.url,
            title=self.title,
            duration_sec=int(cues[-1].end) + 1,
            lang="en",
        )

    def fetch_text(self, platform_id: str):
        from app.connectors.base import TextResult
        from app.connectors.youtube import parse_json3

        return TextResult(
            raw_body=self.body,
            cues=parse_json3(self.body),
            source="official_cc",
            lang="en",
            format="json3",
        )


class MemoryStore:
    """An in-memory object store so the harness never touches real storage."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = body

    def get(self, key: str, max_bytes: int | None = None) -> bytes:
        return self.objects[key]


class RetryingEmbedder:
    """Wrap the production embedder with a harness-only retry.

    Production worker retry behavior is out of scope for this task (see
    ``fix-embed-transient-errors``); this keeps a 35-meeting ingest from
    failing on one transient provider hiccup.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.dimensions = inner.dimensions
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        import http.client

        from app.ingest.embed import EmbeddingError

        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                self.calls += 1
                return self.inner.embed(texts)
            except (EmbeddingError, http.client.HTTPException, OSError) as exc:
                last_exc = exc
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))
        raise last_exc  # pragma: no cover - unreachable, satisfies type checkers


# -------------------------------------------------------------------- git sha
def _git_sha(root: Path | None = None) -> str:
    root = root or repo_root()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProductionPathError("git_sha_unavailable") from exc
    return result.stdout.strip()


def split_meeting_ids(bundle: Mapping[str, Any], split: str) -> list[str]:
    """Every meeting in the artifact bundle's ``split``, sorted for determinism.

    D2 requires scope B to search every meeting of the split in one tenant
    (for val, 15 of the 35 are distractors not referenced by the fixed 30-case
    selection), not just the meetings the selected cases happen to touch.
    """

    return sorted({case.meeting_id for case in bundle["cases"] if case.split == split})


def val_meeting_ids(bundle: Mapping[str, Any]) -> list[str]:
    """Every meeting in the artifact bundle's val split (see ``split_meeting_ids``)."""

    return split_meeting_ids(bundle, "val")


def meeting_domains(bundle: Mapping[str, Any], split: str) -> dict[str, str]:
    """Meeting id -> source domain (Academic/Product/Committee) for ``split``.

    The domain is read from the cases (every case of one meeting carries the
    same domain); there is deliberately no meeting-id prefix list. Two meetings
    sharing a domain are "the same source" for the scope-B localization
    breakdown.
    """

    domains: dict[str, str] = {}
    for case in bundle["cases"]:
        if case.split != split:
            continue
        known = domains.setdefault(case.meeting_id, case.domain)
        if known != case.domain:
            raise ProductionPathError(f"meeting_domain_conflict:{case.meeting_id}")
    return domains


# ---------------------------------------------------------------- fingerprint
def chunker_fingerprint() -> dict[str, Any]:
    """A stable fingerprint of the production chunker's tunable parameters."""

    from app.ingest import chunker

    return {
        "lower_units": dict(chunker.LOWER_UNITS),
        "upper_units": dict(chunker.UPPER_UNITS),
        "max_chunk_seconds": chunker.MAX_CHUNK_SECONDS,
        "overlap_ratio": chunker.OVERLAP_RATIO,
    }


# -------------------------------------------------------------------- ingest
def ingest_meeting(
    meeting_id: str,
    meeting: MeetingCorpus,
    user_id: int,
    factory: Callable[[], Any],
    settings: Any,
) -> dict[str, Any]:
    """Ingest one meeting as a YouTube json3 caption track under ``user_id``."""

    from sqlalchemy import func, select

    from app.ingest.tasks import build_worker_embedder, create_item, process_item
    from app.models import Segment

    body, cue_sids = build_caption_track(meeting)
    video_id = hashlib.sha256(f"{meeting_id}:{uuid.uuid4().hex}".encode()).hexdigest()[:11]
    connector = GoldenMeetingConnector(video_id, f"Meeting recording {meeting_id}", body)
    item_id = create_item(
        connector.url, user_id=user_id, connector=connector, session_factory=factory
    )
    embedder = RetryingEmbedder(build_worker_embedder(settings))
    started = time.monotonic()
    state = process_item(
        item_id,
        connector=connector,
        embedder=embedder,
        object_store=MemoryStore(),
        session_factory=factory,
    )
    if state != "ready":
        raise ProductionPathError(f"ingest_not_ready:{meeting_id}:{state}")

    from app.connectors.youtube import parse_json3

    cues = parse_json3(body)
    if len(cues) != len(cue_sids):
        raise ProductionPathError(f"cue_count_mismatch:{meeting_id}")
    spans = sentence_cue_spans(cue_sids)

    with factory() as db:
        # The title row (``boundary_kind == "title"``, seq -1) holds the item
        # title and description, not transcript text: it has no cue range to
        # map, so it is counted separately and kept out of the segment list.
        rows = (
            db.execute(
                select(Segment)
                .where(Segment.item_id == item_id, Segment.boundary_kind != "title")
                .order_by(Segment.seq)
            )
            .scalars()
            .all()
        )
        title_rows = db.scalar(
            select(func.count())
            .select_from(Segment)
            .where(Segment.item_id == item_id, Segment.boundary_kind == "title")
        )
        segments: list[dict[str, Any]] = []
        for row in rows:
            start, end = float(row.start_sec), float(row.end_sec)
            idx = segment_cue_indices(cues, start, end)
            if not idx or row.text != " ".join(cues[k].text.strip() for k in idx):
                raise ProductionPathError(f"segment_cue_mapping_failed:{meeting_id}:{row.id}")
            chunk_range = (min(idx), max(idx))
            full, partial = map_segment_sentences(chunk_range, spans)
            segments.append(
                {
                    "id": row.id,
                    "seq": row.seq,
                    "item_id": item_id,
                    "text": row.text,
                    "words": len(row.text.split()),
                    "boundary_kind": row.boundary_kind,
                    "full_sentence_ids": full,
                    "partial_sentence_ids": partial,
                }
            )
    kinds = collections.Counter(s["boundary_kind"] for s in segments)
    words = sorted(s["words"] for s in segments)
    text_hash = hashlib.sha256(
        "\x1f".join(s["text"] for s in segments).encode("utf-8")
    ).hexdigest()
    return {
        "meeting_id": meeting_id,
        "item_id": item_id,
        "title": connector.title,
        "cues": len(cues),
        "segments": segments,
        "title_rows": int(title_rows or 0),
        "segment_text_hash": text_hash,
        "boundary_kinds": dict(sorted(kinds.items())),
        "median_words": words[len(words) // 2] if words else 0,
        "p10_words": words[len(words) // 10] if words else 0,
        "p90_words": words[9 * len(words) // 10] if words else 0,
        "embed_calls": embedder.calls,
        "ingest_seconds": round(time.monotonic() - started, 1),
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _load_existing_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _archive_stale_manifest(path: Path) -> None:
    stale = path.with_name(f"{path.stem}.stale-{int(time.time())}{path.suffix}")
    path.rename(stale)


def _create_tenant(factory: Callable[[], Any]) -> int:
    from app.models import AppUser

    with factory() as db:
        user = AppUser()
        db.add(user)
        db.commit()
        return user.id


def _meeting_still_valid(info: Mapping[str, Any], factory: Callable[[], Any]) -> bool:
    from sqlalchemy import select

    from app.models import ContentItem, Segment

    try:
        with factory() as db:
            item = db.get(ContentItem, info["item_id"])
            if item is None or item.state != "ready":
                return False
            # Transcript segments only: the title row is not part of the
            # manifest's segment list or text hash (see ``ingest_meeting``).
            rows = (
                db.execute(
                    select(Segment.text)
                    .where(
                        Segment.item_id == info["item_id"],
                        Segment.boundary_kind != "title",
                    )
                    .order_by(Segment.seq)
                )
                .scalars()
                .all()
            )
    except Exception:
        return False
    if len(rows) != len(info.get("segments", [])):
        return False
    text_hash = hashlib.sha256("\x1f".join(rows).encode("utf-8")).hexdigest()
    return text_hash == info.get("segment_text_hash")


def assert_manifest_meetings_within(
    manifest: Mapping[str, Any], meeting_ids: Sequence[str]
) -> None:
    """Refuse to reuse a library whose tenant holds meetings outside this run.

    Scope B searches the whole tenant. A manifest left by another split (or by
    a different meeting set) would silently add its meetings as extra
    distractors, so a mismatch stops the run instead of contaminating it.
    """

    extra = sorted(set(manifest.get("meetings", {})) - set(meeting_ids))
    if extra:
        raise ProductionPathError(
            f"library_manifest_has_unrequested_meetings:{len(extra)}"
        )


def ingest_library(
    bundle: Mapping[str, Any],
    meeting_ids: Sequence[str],
    run_dir: Path,
    *,
    settings: Any,
) -> dict[str, Any]:
    """Ingest every requested meeting into one tenant, idempotently.

    The manifest is checkpointed to ``run_dir/library.json`` after every
    meeting completes, so a retry after a crash or timeout reuses the
    meetings already ingested instead of re-ingesting the whole library.
    """

    assert_test_environment_active()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    run_dir = Path(run_dir)
    manifest_path = run_dir / "library.json"
    fingerprint = chunker_fingerprint()
    dataset_hash = bundle["dataset_hash"]

    manifest = _load_existing_manifest(manifest_path)
    if (
        manifest is not None
        and (
            manifest.get("dataset_hash") != dataset_hash
            or manifest.get("chunker_fingerprint") != fingerprint
            or manifest.get("schema_version") != LIBRARY_MANIFEST_VERSION
        )
    ):
        _archive_stale_manifest(manifest_path)
        manifest = None

    if manifest is not None:
        assert_manifest_meetings_within(manifest, meeting_ids)

    if manifest is None:
        manifest = {
            "schema_version": LIBRARY_MANIFEST_VERSION,
            "git_sha": _git_sha(),
            "dataset_hash": dataset_hash,
            "chunker_fingerprint": fingerprint,
            "tenant_user_id": None,
            "meetings": {},
        }

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)

    if manifest.get("tenant_user_id") is None:
        manifest["tenant_user_id"] = _create_tenant(factory)
        _atomic_write_json(manifest_path, manifest)
    user_id = manifest["tenant_user_id"]

    for meeting_id in meeting_ids:
        existing = manifest["meetings"].get(meeting_id)
        if existing is not None and _meeting_still_valid(existing, factory):
            continue
        info = ingest_meeting(meeting_id, bundle["corpus"][meeting_id], user_id, factory, settings)
        manifest["meetings"][meeting_id] = info
        _atomic_write_json(manifest_path, manifest)

    engine.dispose()
    return manifest


def session_factory_from_manifest() -> Any:
    """Build a session factory bound to the current (test) ``DATABASE_URL``."""

    assert_test_environment_active()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


__all__ = [
    "LIBRARY_MANIFEST_VERSION",
    "TEST_DATABASE_HOST",
    "ProductionPathError",
    "GoldenMeetingConnector",
    "MemoryStore",
    "RetryingEmbedder",
    "assert_manifest_meetings_within",
    "assert_test_environment_active",
    "build_caption_track",
    "chunker_fingerprint",
    "classify_overlap",
    "ingest_library",
    "ingest_meeting",
    "map_segment_sentences",
    "meeting_domains",
    "prepare_test_environment",
    "repo_root",
    "segment_cue_indices",
    "sentence_cue_spans",
    "session_factory_from_manifest",
    "split_meeting_ids",
    "val_meeting_ids",
]
