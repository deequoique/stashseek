import collections, glob, json, statistics, sys
from pathlib import Path

RUN = Path(sys.argv[1])
OLD = Path('/private/tmp/meeting-agent-real-20261006-specific30')
rows = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(RUN / 'qmsum_*.json')))]
old = {json.loads(Path(f).read_text())['case_id']: json.loads(Path(f).read_text()) for f in glob.glob(str(OLD / 'qmsum_*.json'))}
ingest = json.loads((RUN / 'ingest.json').read_text())

print('== ingest (production chunk path)')
allw = []
for m, info in ingest.items():
    allw += [s['words'] for s in info['segments']]
    print(f"  {m:<13} cues={info['cues']:5d} segments={len(info['segments']):4d} median_words={info['median_words']:4d} p10={info['p10_words']:3d} p90={info['p90_words']:4d} kinds={info['boundary_kinds']}")
allw.sort()
print(f"  ALL segments={len(allw)} median_words={allw[len(allw)//2]} p25={allw[len(allw)//4]} p75={allw[3*len(allw)//4]}")

n = len(rows)
harness = [r for r in rows if r.get('status') == 'harness_error']
ok = [r for r in rows if r.get('answer_status') == 'completed']
print(f"\n== answers: completed {len(ok)}/{n}  harness_errors={len(harness)}  (old run: {sum(o['answer_status']=='completed' for o in old.values())}/30)")
for r in harness:
    print('  harness:', r['case_id'], r['error'])
rows = [r for r in rows if r.get('status') != 'harness_error']
print('  final error codes:', collections.Counter(r['agent_error_code'] for r in rows if r['answer_status'] != 'completed'))
ever = collections.Counter(x for r in rows for x in set(r['citation_failures']))
print('  cases with validator failure (any attempt):', dict(ever))
print('  cases hitting limits:', collections.Counter(l['kind'] for r in rows for l in r['limits']), 'cases', sum(bool(r['limits']) for r in rows))
skipped = sum(v for r in rows for k, v in r['tool_events'].items() if k.endswith(':skipped'))
executed = sum(v for r in rows for k, v in r['tool_events'].items() if k.endswith(':succeeded'))
print(f'  tool calls executed={executed} skipped={skipped}')
print('  provider calls median', statistics.median(len(r['provider_responses']) for r in rows), 'non-200', sum(1 for r in rows for p in r['provider_responses'] if p['status'] != 200))
print('  latency median s', statistics.median(r['elapsed_seconds'] for r in rows), 'p90', sorted(r['elapsed_seconds'] for r in rows)[int(.9 * len(rows))])
print('  citations per answer median', statistics.median(r['agent_citation_count'] for r in ok) if ok else None)

def macro(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

print('\n== official sentence-level scorer (ces, macro over eligible & completed stage)')
for stage, names in (('retrieval', ('retrieval_recall@1', 'retrieval_recall@3', 'retrieval_recall@5', 'retrieval_mrr', 'retrieval_hit@5')),
                     ('citation', ('gold_evidence_recall', 'gold_citation_precision', 'reference_group_any_hit', 'reference_group_all_hit'))):
    for name in names:
        new_v = macro([(r['sentence_report'][stage]['metrics'] or {}).get(name, {}).get('value') if r['sentence_report'][stage]['eligible'] else None for r in rows])
        old_v = macro([((o['report'][stage]['metrics'] or {}).get(name) or {}).get('value') if o['report'][stage]['coverage']['eligible_count'] else None for o in old.values()])
        fmt = lambda v: f'{v[0]:.3f} (n={v[1]})' if v[0] is not None else 'n/a'
        print(f'  {name:<26} prod-path {fmt(new_v):<16} old {fmt(old_v)}')

print('\n== chunk-level (ces eligible)')
el = [r for r in rows if r['ces_eligible'] and r['chunk_report'] and r['chunk_report']['retrieval']]
for k in ('chunk_hit@1', 'chunk_hit@3', 'chunk_hit@5', 'chunk_hit@10', 'chunk_recall@5', 'chunk_recall@10', 'chunk_group_any@5', 'pool_hit', 'pool_recall', 'pool_chunks', 'pool_words'):
    print(f'  {k:<18} {sum(r["chunk_report"]["retrieval"][k] for r in el) / len(el):8.3f}   (n={len(el)})')
print('  gold spread: median production chunks containing gold =', statistics.median(r['chunk_report']['gold_chunks'] for r in el))
ce = [r for r in rows if r['ces_eligible'] and r['chunk_report'] and r['chunk_report']['citation']]
for k in ('cited_chunks', 'cited_chunk_precision', 'cited_gold_recall', 'cited_group_any', 'cited_any_gold'):
    print(f'  {k:<22} {sum(r["chunk_report"]["citation"][k] for r in ce) / len(ce):8.3f}   (n={len(ce)})')

print('\n== by domain')
by = collections.defaultdict(list)
for r in rows:
    by[r['domain'].split('(')[0]].append(r)
for d, rs in by.items():
    e = [r for r in rs if r['ces_eligible'] and r['chunk_report'] and r['chunk_report']['retrieval']]
    print(f"  {d:<10} answered {sum(r['answer_status']=='completed' for r in rs)}/{len(rs)}  chunk_hit@5 {sum(r['chunk_report']['retrieval']['chunk_hit@5'] for r in e)}/{len(e)}")

print('\n== per case')
for r in rows:
    o = old[r['case_id']]
    cr = (r['chunk_report'] or {}).get('retrieval') or {}
    q = [t['query'] for t in r['retrieval_trace'] if t['tool'] == 'search_segments']
    print(f"  {r['case_id'][6:]:<26} {'OK ' if r['answer_status']=='completed' else 'FAIL'} (old {'OK ' if o['answer_status']=='completed' else 'FAIL'}) "
          f"hit@5={cr.get('chunk_hit@5')} cites={r['agent_citation_count']} fails={r['citation_failures']} lim={[l['kind'] for l in r['limits']]} "
          f"tools={r['tool_events']} q={q}")
