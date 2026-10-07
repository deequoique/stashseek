"""Production-path rerun of the specific-30 meeting golden smoke.

Differences from /private/tmp/meeting_agent_smoke.py (all toward production):
- Ingestion goes through the real ``create_item`` + ``process_item`` worker path
  (production ``chunk()`` with the semantic embedder, chunk embeddings, FTS).
  The transcript enters as a YouTube json3 caption track: cues of <=12 words,
  a new cue at every speaker change, 2.5 words/s, 0.3 s pause between turns,
  dataset annotation tokens such as ``{disfmarker}`` removed.
- One isolated test tenant per meeting holding only that meeting.
- Production agent composition (real action services with a no-op dispatch
  publisher, streaming model when supported) and the web ``agent.stream`` path
  with a fresh thread (empty history and context).
- Retrieval is only recorded, never re-scoped.
Settings are the production ``.env`` values except DATABASE_URL, which points
at the Neon *test* branch.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import http.client
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

ROOT = Path('/Volumes/PeeB/projects/notebook-agent')
DATA = ROOT / 'data/meeting_gold/final'
PREVIOUS = Path('/private/tmp/meeting-agent-real-20261006-specific30')
OUT = Path(os.environ.get('OUT_DIR', '/private/tmp/meeting-agent-real-20261006-specific30-prodpath'))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

prod = {k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}
test = {k: v for k, v in dotenv_values(ROOT / '.env.neon-test').items() if v is not None}
test_host = urlsplit(test['DATABASE_URL']).hostname
assert test_host == 'ep-falling-fog-azrht8ir-pooler.c-3.ap-southeast-1.aws.neon.tech'
assert test_host != urlsplit(prod['DATABASE_URL']).hostname
for key, value in prod.items():
    if key in {'DATABASE_URL', 'MIGRATION_DATABASE_URL'}:
        continue
    os.environ[key] = value
p = urlsplit(test['DATABASE_URL'])
os.environ['DATABASE_URL'] = urlunsplit(('postgresql+psycopg', p.netloc, p.path, p.query, p.fragment))
os.environ.pop('MIGRATION_DATABASE_URL', None)

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.actions import AgentActionServices
from app.agent.context import TurnContext
from app.agent.management import KnowledgeItemManagementService
from app.agent.provider import build_model, model_supports_streaming
from app.agent.runtime import KnowledgeAgent
from app.agent.services import KnowledgeServices
from app.agent.types import AgentRequest
from app.bootstrap import build_embedding_provider
from app.channels.pending_actions import PendingConfirmationService
from app.channels.types import TenantContext
from app.config import get_settings
from app.connectors.base import ItemMeta, TextResult
from app.connectors.youtube import parse_json3
from app.diagnostics import LOGGER, RequestDiagnostics
from app.ingest.embed import EmbeddingError
from app.ingest.submission import build_ingest_submission_service
from app.ingest.tasks import build_worker_embedder, create_item, process_item
from app.models import AppUser, Segment
from app.tls import configure_trusted_ca
from evals.meeting_gold.artifacts import (
    _selection_hash_payload,
    _selection_shortfalls,
    canonical_hash,
    load_artifact_bundle,
    select_cases,
    validate_selection,
)
from evals.meeting_gold.integration import map_chunk_records
from evals.meeting_gold.scoring import _validate_case, score_cases

MARK = re.compile(r"\{[a-z_]+\}")
MAX_CUE_WORDS = 12
WORDS_PER_SECOND = 2.5
TURN_GAP_SECONDS = 0.3


# ---------------------------------------------------------------- ingestion
def build_caption_track(meeting) -> tuple[bytes, list[list[str]]]:
    """Return a json3 body plus the source sentence ids of every cue."""

    turns: dict[int, list[tuple[str, str]]] = collections.OrderedDict()
    for sentence in meeting:
        words = MARK.sub(' ', sentence.text).split()
        for word in words:
            turns.setdefault(sentence.turn_index, []).append((word, sentence.id))
    events, cue_sids, t_ms = [], [], 0
    for words in turns.values():
        for start in range(0, len(words), MAX_CUE_WORDS):
            part = words[start:start + MAX_CUE_WORDS]
            duration_ms = int(round(len(part) / WORDS_PER_SECOND * 1000))
            events.append({
                'tStartMs': t_ms,
                'dDurationMs': duration_ms,
                'segs': [{'utf8': ' '.join(w for w, _ in part)}],
            })
            cue_sids.append(list(dict.fromkeys(s for _, s in part)))
            t_ms += duration_ms
        t_ms += int(TURN_GAP_SECONDS * 1000)
    body = json.dumps({'events': events}).encode()
    return body, cue_sids


class GoldenMeetingConnector:
    platform = 'youtube'

    def __init__(self, video_id: str, title: str, body: bytes) -> None:
        self.video_id = video_id
        self.url = f'https://www.youtube.com/watch?v={video_id}'
        self.title = title
        self.body = body

    def match(self, url: str) -> str | None:
        return self.video_id if url == self.url else None

    def fetch_meta(self, platform_id: str) -> ItemMeta:
        cues = parse_json3(self.body)
        return ItemMeta(
            platform_id=platform_id, url=self.url, title=self.title,
            duration_sec=int(cues[-1].end) + 1, lang='en',
        )

    def fetch_text(self, platform_id: str) -> TextResult:
        return TextResult(
            raw_body=self.body, cues=parse_json3(self.body),
            source='official_cc', lang='en', format='json3',
        )


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key, body, content_type):
        self.objects[key] = body

    def get(self, key, max_bytes=None):
        return self.objects[key]


class RetryingEmbedder:
    """Production embedder; transient provider errors are retried like a worker retry."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.dimensions = inner.dimensions
        self.calls = 0

    def embed(self, texts):
        for attempt in range(5):
            try:
                self.calls += 1
                return self.inner.embed(texts)
            except (EmbeddingError, http.client.HTTPException, OSError):
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))


def ingest_meeting(meeting_id, meeting, factory, settings):
    body, cue_sids = build_caption_track(meeting)
    video_id = hashlib.sha256(f'{meeting_id}:{uuid.uuid4().hex}'.encode()).hexdigest()[:11]
    connector = GoldenMeetingConnector(video_id, f'Meeting recording {meeting_id}', body)
    with factory() as db:
        user = AppUser()
        db.add(user)
        db.commit()
        user_id = user.id
    item_id = create_item(connector.url, user_id=user_id, connector=connector, session_factory=factory)
    embedder = RetryingEmbedder(build_worker_embedder(settings))
    started = time.monotonic()
    state = process_item(item_id, connector=connector, embedder=embedder, object_store=MemoryStore(), session_factory=factory)
    if state != 'ready':
        raise RuntimeError(f'ingest_not_ready:{meeting_id}:{state}')
    cues = parse_json3(body)
    assert len(cues) == len(cue_sids)
    with factory() as db:
        rows = db.execute(select(Segment).where(Segment.item_id == item_id).order_by(Segment.seq)).scalars().all()
        segments = []
        for row in rows:
            start, end = float(row.start_sec), float(row.end_sec)
            idx = [k for k, cue in enumerate(cues) if cue.start >= start - 1e-6 and cue.end <= end + 1e-6]
            if not idx or row.text != ' '.join(cues[k].text.strip() for k in idx):
                raise RuntimeError(f'segment_cue_mapping_failed:{meeting_id}:{row.id}')
            sids = list(dict.fromkeys(s for k in idx for s in cue_sids[k]))
            segments.append({'id': row.id, 'seq': row.seq, 'text': row.text, 'words': len(row.text.split()),
                             'boundary_kind': row.boundary_kind, 'sids': sids})
    kinds = collections.Counter(s['boundary_kind'] for s in segments)
    words = sorted(s['words'] for s in segments)
    return {
        'meeting_id': meeting_id, 'user_id': user_id, 'item_id': item_id, 'title': connector.title,
        'cues': len(cues), 'segments': segments, 'boundary_kinds': dict(kinds),
        'median_words': words[len(words) // 2], 'p10_words': words[len(words) // 10],
        'p90_words': words[9 * len(words) // 10], 'embed_calls': embedder.calls,
        'ingest_seconds': round(time.monotonic() - started, 1),
    }


# ---------------------------------------------------------------- agent
class RecordingServices(KnowledgeServices):
    def __init__(self, *args, trace: list, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = trace

    def _record(self, tool, *, query=None, args=None, results, status='succeeded', error_type=None):
        self.trace.append({
            'tool': tool, 'query': query, 'args': args, 'status': status, 'error_type': error_type,
            'results': [{'segment_id': c.segment_id, 'item_id': c.item_id, 'score': getattr(c, '_retrieval_score', None),
                         'excerpt': c.excerpt} for c in results],
        })

    def search_segments(self, query, *, limit=10, item_id=None):
        try:
            result = super().search_segments(query, limit=limit, item_id=item_id)
        except Exception as exc:
            self._record('search_segments', query=query, args={'limit': limit, 'item_id': item_id}, results=[],
                         status='failed', error_type=type(exc).__name__)
            raise
        self._record('search_segments', query=query, args={'limit': limit, 'item_id': item_id}, results=result)
        return result

    def get_neighbors(self, segment_id, *, radius=1):
        try:
            result = super().get_neighbors(segment_id, radius=radius)
        except Exception as exc:
            self._record('get_neighbors', args={'segment_id': segment_id, 'radius': radius}, results=[],
                         status='failed', error_type=type(exc).__name__)
            raise
        self._record('get_neighbors', args={'segment_id': segment_id, 'radius': radius}, results=result)
        return result

    def open_at(self, segment_id):
        try:
            result = super().open_at(segment_id)
        except Exception as exc:
            self._record('open_at', args={'segment_id': segment_id}, results=[], status='failed', error_type=type(exc).__name__)
            raise
        self._record('open_at', args={'segment_id': segment_id}, results=[result])
        return result


def chunk_metrics(ranked_sids: list[list[str]], groups: list[set], gold: set, words: list[int]) -> dict:
    out = {}
    for k in (1, 3, 5, 10):
        top = set().union(*ranked_sids[:k]) if ranked_sids else set()
        out[f'chunk_hit@{k}'] = float(bool(top & gold))
        out[f'chunk_recall@{k}'] = len(top & gold) / len(gold)
        out[f'chunk_group_any@{k}'] = sum(bool(top & g) for g in groups) / len(groups)
    allsids = set().union(*ranked_sids) if ranked_sids else set()
    out['pool_chunks'] = len(ranked_sids)
    out['pool_words'] = sum(words)
    out['pool_hit'] = float(bool(allsids & gold))
    out['pool_recall'] = len(allsids & gold) / len(gold)
    return out


def citation_metrics(cited_sids: list[list[str]], groups: list[set], gold: set) -> dict:
    allsids = set().union(*cited_sids) if cited_sids else set()
    return {
        'cited_chunks': len(cited_sids),
        'cited_chunk_precision': (sum(bool(set(s) & gold) for s in cited_sids) / len(cited_sids)) if cited_sids else 0.0,
        'cited_gold_recall': len(allsids & gold) / len(gold),
        'cited_group_any': sum(bool(allsids & g) for g in groups) / len(groups),
        'cited_any_gold': float(bool(allsids & gold)),
    }


def one_case_selection(bundle, case_id):
    selection = select_cases(bundle['cases'], dataset_hash=bundle['dataset_hash'], split='val',
                             limit=1, seed=0, leakage=bundle['audit']['leakage'])
    selection['selected_case_ids'] = [case_id]
    target = next(c for c in bundle['cases'] if c.case_id == case_id)
    selection['shortfalls'] = _selection_shortfalls([c for c in bundle['cases'] if c.split == 'val'], [target])
    selection['selection_hash'] = canonical_hash(_selection_hash_payload(selection))
    validate_selection(selection, bundle['cases'], dataset_hash=bundle['dataset_hash'], leakage=bundle['audit']['leakage'])
    return {k: selection[k] for k in ('selection_version', 'dataset_hash', 'split', 'selected_case_ids', 'seed',
                                      'selection_policy', 'selection_hash')}


async def run_case(case, ingest, *, agent, settings, factory, provider_responses, bundle):
    trace: list = []
    seg_by_id = {s['id']: s for s in ingest['segments']}
    known = {s.id: s for m in [bundle['corpus'][case.meeting_id]] for s in m}

    run_tag = uuid.uuid4().hex[:12]
    agent._service_factory = lambda request: RecordingServices(
        request.tenant, factory, embedder=agent_embedder, trace=trace)
    request = AgentRequest(
        question=case.query,
        tenant=TenantContext(ingest['user_id'], 1, 'web', 'eval', f'eval-{run_tag}'),
        thread_db_id=1, thread_public_id=f'eval-{run_tag}',
        message_id=f'm-{run_tag}', request_id=f'r-{run_tag}',
        history=(), context=TurnContext(),
    )
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            payload = getattr(record, 'diagnostic_payload', None)
            if isinstance(payload, dict):
                records.append(dict(payload))

    handler = Capture()
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    diagnostics = RequestDiagnostics.start(request.request_id, ingest['user_id'], environment='development',
                                           allow_retrieval_content=False)
    provider_responses.clear()
    # Instrumentation only: record *why* the stream path failed (exception
    # class/message and the validator rule), never the model prose itself.
    import traceback  # noqa: F401
    from app.agent import answer_pipeline as ap
    from app.agent.answer_validation import NaturalAnswerValidationError, _CITATION_LIKE_MARKER, _VALID_MARKER
    stream_failures, guard_rejections, aborted = [], [], []
    original_failure = ap.AnswerPipeline.__dict__['_stream_failure'].__func__

    def patched_failure(req, code, text):
        exc = sys.exc_info()[1]
        cause = exc.__cause__ if exc is not None else None
        stream_failures.append({'code': code, 'exception': type(exc).__name__ if exc else None,
                                'message': str(exc)[:160] if exc else None,
                                'cause': f'{type(cause).__name__}: {str(cause)[:160]}' if cause else None})
        return original_failure(req, code, text)

    ap.AnswerPipeline._stream_failure = staticmethod(patched_failure)
    original_forbidden = ap._StreamingTextGuard.__dict__['_has_forbidden_text'].__func__

    def patched_forbidden(value):
        try:
            ap.validate_natural_answer(value)
        except NaturalAnswerValidationError as exc:
            markers = [m.group(0)[:24] for m in _CITATION_LIKE_MARKER.finditer(value)
                       if _VALID_MARKER.fullmatch(m.group(0)) is None]
            guard_rejections.append({'rule': str(exc), 'malformed_markers': markers[:3],
                                     'valid_markers': len(_VALID_MARKER.findall(value))})
            return True
        return False

    ap._StreamingTextGuard._has_forbidden_text = staticmethod(patched_forbidden)
    events = collections.Counter()
    final = None
    started = time.monotonic()
    try:
        async for event in agent.stream(request, diagnostics=diagnostics):
            events[event.type] += 1
            if event.type == 'section_aborted':
                aborted.append(event.reason)
            if event.type == 'completed':
                final = event
    finally:
        LOGGER.removeHandler(handler)
    elapsed = round(time.monotonic() - started, 1)
    answer = final.answer

    # ----- retrieval pool (same policy as before: best score per chunk, then first seen)
    pool, rejected = {}, []
    order = 0
    for event_index, event in enumerate(trace):
        for hit in event['results']:
            seg = seg_by_id.get(hit['segment_id'])
            if seg is None or hit['excerpt'] != seg['text']:
                rejected.append({'event_index': event_index, 'segment_id': hit['segment_id']})
                continue
            prev = pool.get(hit['segment_id'])
            score = hit['score']
            if prev is None:
                pool[hit['segment_id']] = {'first_seen': order, 'score': score}
                order += 1
            elif score is not None and (prev['score'] is None or score > prev['score']):
                prev['score'] = score
    ranked = sorted(pool.items(), key=lambda kv: (kv[1]['score'] is None, -(kv[1]['score'] or 0.0), kv[1]['first_seen']))
    chunk_records = [{'rank': r, 'chunk_id': str(sid), 'meeting_id': case.meeting_id,
                      'source_sentence_ids': seg_by_id[sid]['sids'], 'text': seg_by_id[sid]['text']}
                     for r, (sid, _) in enumerate(ranked, 1)]
    mapped = map_chunk_records(chunk_records, bundle['corpus'], meeting_id=case.meeting_id) if chunk_records else {'retrieved_sentence_ids': [], 'status': 'complete'}
    cited = [c for c in answer.citations]
    citation_records = [{'rank': r, 'chunk_id': str(c.segment_id), 'meeting_id': case.meeting_id,
                         'source_sentence_ids': seg_by_id[c.segment_id]['sids'], 'text': seg_by_id[c.segment_id]['text']}
                        for r, c in enumerate(cited, 1) if c.segment_id in seg_by_id]
    mapped_cited = map_chunk_records(citation_records, bundle['corpus'], meeting_id=case.meeting_id) if citation_records else {'retrieved_sentence_ids': [], 'status': 'complete'}

    succeeded = sum(e['status'] == 'succeeded' for e in trace)
    failed = sum(e['status'] == 'failed' for e in trace)
    retrieval_status = 'failed' if not succeeded else 'partial' if (failed or rejected) else 'completed'
    retrieval_completed = retrieval_status == 'completed'
    answer_completed = retrieval_completed and answer.status in {'ok', 'not_found'} and answer.error_code is None \
        and len(citation_records) == len(cited)

    selection = one_case_selection(bundle, case.case_id)
    record = {'case_id': case.case_id, 'retrieved_sentence_ids': mapped['retrieved_sentence_ids'],
              'cited_sentence_ids': mapped_cited['retrieved_sentence_ids']}
    base = {'schema_version': 'meeting-gold-predictions-v1', 'dataset_hash': bundle['dataset_hash'],
            'selection_hash': selection['selection_hash']}
    sentence_report = {}
    for stage, include, names in (
        ('retrieval', retrieval_completed, ('retrieval_recall@1', 'retrieval_recall@3', 'retrieval_recall@5', 'retrieval_mrr', 'retrieval_hit@5')),
        ('citation', answer_completed, ('gold_evidence_recall', 'gold_citation_precision', 'reference_group_any_hit', 'reference_group_all_hit')),
    ):
        raw = score_cases([case], {**base, 'records': [record] if include else []}, corpus=bundle['corpus'],
                          selection=selection, dataset_hash=bundle['dataset_hash'], mode='complete')
        agg = raw.get('aggregates')
        sentence_report[stage] = {'status': raw['status'], 'eligible': raw['coverage']['eligible_count'],
                                  'metrics': {n: agg['query_macro']['metrics'][n] for n in names} if agg else None}

    eligible = case.eligibility['ces'] == 'citation_scorable'
    chunk_report = None
    if eligible:
        validated = _validate_case(case, known_sentences=known, corpus_meetings={case.meeting_id}, policy='ces')
        groups = [set(g) for g in validated.groups]
        gold = set(validated.gold_ids)
        chunk_report = {
            'retrieval': chunk_metrics([seg_by_id[sid]['sids'] for sid, _ in ranked], groups, gold,
                                       [seg_by_id[sid]['words'] for sid, _ in ranked]) if retrieval_completed else None,
            'citation': citation_metrics([seg_by_id[c.segment_id]['sids'] for c in cited if c.segment_id in seg_by_id],
                                         groups, gold) if answer_completed else None,
            'gold_chunks': sum(bool(set(s['sids']) & gold) for s in ingest['segments']),
        }

    tool_events = collections.Counter((r.get('tool_name'), r.get('tool_outcome')) for r in records if r.get('stage') == 'tool_call')
    payload = {
        'case_id': case.case_id, 'meeting_id': case.meeting_id, 'domain': case.domain, 'query': case.query,
        'item_id': ingest['item_id'], 'user_id': ingest['user_id'],
        'status': 'completed' if answer_completed else 'incomplete',
        'retrieval_status': retrieval_status, 'answer_status': 'completed' if answer_completed else 'failed',
        'agent_status': answer.status, 'agent_error_code': answer.error_code,
        'agent_citation_count': len(cited), 'elapsed_seconds': elapsed,
        'stream_events': dict(events),
        'retrieval_trace': [{'tool': e['tool'], 'query': e['query'], 'args': e['args'], 'status': e['status'],
                             'error_type': e['error_type'], 'segment_ids': [h['segment_id'] for h in e['results']],
                             'scores': [h['score'] for h in e['results']]} for e in trace],
        'tool_events': {f'{k[0]}:{k[1]}': v for k, v in tool_events.items()},
        'limits': [{'kind': r.get('limit_kind'), 'phase': r.get('agent_phase')} for r in records if r.get('limit_kind')],
        'citation_failures': [r.get('failure_reason') for r in records if r.get('stage') == 'citation_validated' and r.get('failure_reason')],
        'final_citations': [{'segment_id': c.segment_id, 'words': seg_by_id.get(c.segment_id, {}).get('words')} for c in cited],
        'diagnostics': records, 'provider_responses': list(provider_responses), 'rejected_hits': rejected,
        'sentence_report': sentence_report, 'chunk_report': chunk_report,
        'ces_eligible': eligible,
        'stream_failures': stream_failures, 'guard_rejections': guard_rejections, 'section_aborted': aborted,
    }
    (OUT / f"{case.case_id.replace(':', '_')}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    return payload


async def main_async(bundle, case_ids, ingests, factory, settings):
    global agent_embedder
    trusted_ca = configure_trusted_ca(settings.tls_ca_bundle)
    agent_embedder = build_embedding_provider(settings, trusted_ca=trusted_ca)
    provider_responses: list = []

    async def capture(response):
        if response.request.url.path.endswith('/chat/completions'):
            provider_responses.append({'status': response.status_code})

    model = build_model(settings)
    client = httpx.AsyncClient(event_hooks={'response': [capture]}, timeout=httpx.Timeout(60.0))
    provider = getattr(model, '_provider', None)
    if provider is not None:
        provider._set_http_client(client)
    stream_model = model if model_supports_streaming(model) else None
    action_services = AgentActionServices(
        submission=build_ingest_submission_service(factory, lambda *a, **k: None, settings),
        pending=PendingConfirmationService(factory),
        management=KnowledgeItemManagementService(factory, retention_days=settings.trash_retention_days),
    )
    agent = KnowledgeAgent(model, settings, lambda r: None, action_factory=lambda _r: action_services,
                           stream_model=stream_model, multimodal_embedding_provider=None)
    print(json.dumps({'stream_model': stream_model is not None,
                      'streaming_enabled': settings.agent_streaming_enabled,
                      'multimodal': settings.multimodal_retrieval_enabled}), flush=True)
    cases = {c.case_id: c for c in bundle['cases']}
    results = []
    for case_id in case_ids:
        case = cases[case_id]
        try:
            result = await run_case(case, ingests[case.meeting_id], agent=agent, settings=settings, factory=factory,
                                    provider_responses=provider_responses, bundle=bundle)
        except Exception as exc:  # keep going; record the harness failure explicitly
            result = {'case_id': case_id, 'status': 'harness_error', 'error': f'{type(exc).__name__}: {exc}'}
            (OUT / f"{case_id.replace(':', '_')}.json").write_text(json.dumps(result, indent=1))
        results.append(result)
        cr = (result.get('chunk_report') or {}).get('retrieval') or {}
        print(json.dumps({'case': case_id, 'status': result.get('status'), 'err': result.get('agent_error_code'),
                          'cites': result.get('agent_citation_count'), 'hit@5': cr.get('chunk_hit@5'),
                          'fails': result.get('citation_failures'), 'limits': result.get('limits'),
                          's': result.get('elapsed_seconds'), 'aborted': result.get('section_aborted'),
                          'stream_failures': result.get('stream_failures'),
                          'guard': [g['rule'] for g in result.get('guard_rejections') or []]}, ensure_ascii=False), flush=True)
    await client.aclose()
    return results


def main():
    settings = get_settings()
    bundle = load_artifact_bundle(DATA)
    case_ids = sorted(
        json.loads(f.read_text())['case_id'] for f in PREVIOUS.glob('qmsum_*.json')
    )
    if os.environ.get('CASE_LIMIT'):
        case_ids = case_ids[: int(os.environ['CASE_LIMIT'])]
    if os.environ.get('CASE_ID'):
        case_ids = [os.environ['CASE_ID']]
    cases = {c.case_id: c for c in bundle['cases']}
    engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    ingest_path = OUT / 'ingest.json'
    ingests = json.loads(ingest_path.read_text()) if ingest_path.exists() else {}
    for meeting_id in dict.fromkeys(cases[c].meeting_id for c in case_ids):
        if meeting_id in ingests:
            continue
        info = ingest_meeting(meeting_id, bundle['corpus'][meeting_id], factory, settings)
        ingests[meeting_id] = info
        ingest_path.write_text(json.dumps(ingests))
        print(json.dumps({'ingested': meeting_id, 'item_id': info['item_id'], 'cues': info['cues'],
                          'segments': len(info['segments']), 'median_words': info['median_words'],
                          'p10': info['p10_words'], 'p90': info['p90_words'], 'kinds': info['boundary_kinds'],
                          'seconds': info['ingest_seconds']}), flush=True)
    if os.environ.get('CASE_ID'):
        asyncio.run(main_async(bundle, [os.environ['CASE_ID']], ingests, factory, settings))
        return
    # One process per case (as the original runner did) so a leaked async
    # generator from one turn can never cancel the next turn in the harness.
    import subprocess
    summary = []
    for case_id in case_ids:
        env = dict(os.environ, CASE_ID=case_id, OUT_DIR=str(OUT))
        env.pop('CASE_LIMIT', None)
        try:
            proc = subprocess.run([sys.executable, '-u', __file__], env=env, capture_output=True, text=True, timeout=420)
            line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith('{"case"')), None)
            print(line or json.dumps({'case': case_id, 'status': 'harness_error', 'rc': proc.returncode,
                                      'stderr_tail': proc.stderr[-400:]}), flush=True)
            if proc.stderr.strip():
                (OUT / f"{case_id.replace(':', '_')}.stderr.txt").write_text(proc.stderr[-20000:])
        except subprocess.TimeoutExpired:
            print(json.dumps({'case': case_id, 'status': 'harness_timeout'}), flush=True)
        summary.append(case_id)
    (OUT / 'results.json').write_text(json.dumps(summary, indent=1))


agent_embedder = None

if __name__ == '__main__':
    main()
