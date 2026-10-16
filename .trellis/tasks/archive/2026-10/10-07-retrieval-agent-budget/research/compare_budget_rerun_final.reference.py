import collections, glob, json, statistics
from pathlib import Path

S = Path('/private/tmp/claude-501/-Volumes-PeeB-projects-notebook-agent/65a84b49-1cb0-4e56-9c7a-28f3d0214a28/scratchpad')
RUNS = {
    'baseline (10-06 post-fix)': S / 'prodpath-postfix',
    'thinking-off (round 1)': S / 'budget-rerun',
    'thinking-on (final)': S / 'budget-rerun-final',
}


def load(d):
    return {json.loads(Path(f).read_text())['case_id']: json.loads(Path(f).read_text())
            for f in glob.glob(str(d / 'qmsum_*.json'))}


def category(r):
    if r.get('answer_status') == 'completed':
        return 'completed'
    if r.get('agent_error_code') == 'search_required':
        return 'search_required (no search call)'
    rules = [g['rule'] for g in r.get('guard_rejections') or []]
    sf = r.get('stream_failures') or []
    if any('empty' in x for x in rules):
        return 'stream guard whitespace'
    if rules:
        return 'stream guard: ' + rules[0]
    if sf and sf[0].get('cause') and '数量超过上限' in sf[0]['cause']:
        return 'stream plan >8'
    if set(r.get('citation_failures') or []) & {'too_many_segments', 'duplicate_citation'}:
        return 'composer too_many/duplicate'
    if r.get('agent_error_code'):
        return 'other: ' + str(r.get('agent_error_code')) + ' ' + json.dumps(sf)[:120]
    return 'other'


data = {k: load(v) for k, v in RUNS.items()}
summaries = {}
for name, rows in data.items():
    ing = json.loads((RUNS[name] / 'ingest.json').read_text())
    words = sorted(s['words'] for i in ing.values() for s in i['segments'])
    el = [r for r in rows.values() if r.get('ces_eligible') and (r.get('chunk_report') or {}).get('retrieval')]
    ce = [r for r in rows.values() if r.get('ces_eligible') and (r.get('chunk_report') or {}).get('citation')]
    m = lambda key: sum(r['chunk_report']['retrieval'][key] for r in el) / len(el) if el else float('nan')
    mc = lambda key: sum(r['chunk_report']['citation'][key] for r in ce) / len(ce) if ce else float('nan')
    skipped = sum(v for r in rows.values() for k, v in r['tool_events'].items() if k.endswith(':skipped'))
    executed = sum(v for r in rows.values() for k, v in r['tool_events'].items() if k.endswith(':succeeded'))
    provider_calls = [len(r.get('provider_responses') or []) for r in rows.values()]
    usages = [u for r in rows.values() for u in (r.get('stage_usage') or [])]
    out_tokens = sorted(u.get('output_tokens') for u in usages if isinstance(u.get('output_tokens'), int))
    in_tokens = [u.get('input_tokens') for u in usages if isinstance(u.get('input_tokens'), int)]
    req_counts = [u.get('request_count') for u in usages if isinstance(u.get('request_count'), int)]
    cats = dict(collections.Counter(category(r) for r in rows.values()))
    limit_kinds = dict(collections.Counter(l['kind'] for r in rows.values() for l in r['limits']))
    summaries[name] = dict(
        answered=sum(r['answer_status'] == 'completed' for r in rows.values()), total=len(rows), cats=cats,
        pool_hit=m('pool_hit'), pool_recall=m('pool_recall'), n_el=len(el),
        cited_any_gold=mc('cited_any_gold'), gold_recall=mc('cited_gold_recall'),
        chunk_precision=mc('cited_chunk_precision'), group_any=mc('cited_group_any'), n_ce=len(ce),
        limit_cases=sum(bool(r['limits']) for r in rows.values()), limit_kinds=limit_kinds,
        executed=executed, skipped=skipped,
        provider_median=statistics.median(provider_calls) if provider_calls else None,
        latency_median=statistics.median(r['elapsed_seconds'] for r in rows.values()),
        out_tok_median=statistics.median(out_tokens) if out_tokens else None,
        out_tok_p90=out_tokens[int(0.9 * (len(out_tokens) - 1))] if out_tokens else None,
        out_tok_max=max(out_tokens) if out_tokens else None,
        in_tok_median=statistics.median(in_tokens) if in_tokens else None,
        req_median=statistics.median(req_counts) if req_counts else None,
    )

print(f"{'metric':38} | {'baseline':>22} | {'thinking-off':>22} | {'thinking-on (final)':>22}")
print('-' * 112)
names = list(RUNS)
def row(label, key, fmt='{}'):
    vals = [summaries[n].get(key) for n in names]
    print(f"{label:38} | " + " | ".join(f"{fmt.format(v) if v is not None else '-':>22}" for v in vals))

row('answered', 'answered', '{}/30')
for n in names:
    print(f"    [{n}] categories = {summaries[n]['cats']}")
row('pool gold hit@eligible', 'pool_hit', '{:.3f}')
row('pool gold recall@eligible', 'pool_recall', '{:.3f}')
row('  (n eligible)', 'n_el', '{}')
row('cited_any_gold', 'cited_any_gold', '{:.3f}')
row('cited gold_recall', 'gold_recall', '{:.3f}')
row('cited chunk_precision', 'chunk_precision', '{:.3f}')
row('cited group_any', 'group_any', '{:.3f}')
row('  (n answered+eligible)', 'n_ce', '{}')
row('limit-hit cases', 'limit_cases', '{}')
for n in names:
    print(f"    [{n}] limit kinds = {summaries[n]['limit_kinds']}")
row('tool calls executed', 'executed', '{}')
row('tool calls skipped', 'skipped', '{}')
row('provider calls / case (median)', 'provider_median', '{:.0f}')
row('latency median (s)', 'latency_median', '{:.1f}')
row('retrieval-stage output_tokens median', 'out_tok_median', '{:.0f}')
row('retrieval-stage output_tokens p90', 'out_tok_p90', '{:.0f}')
row('retrieval-stage output_tokens max', 'out_tok_max', '{:.0f}')
row('retrieval-stage input_tokens median', 'in_tok_median', '{:.0f}')
row('retrieval-stage request_count median', 'req_median', '{:.0f}')

print('\n== per case (baseline -> thinking-off -> thinking-on)')
for cid in sorted(data['thinking-on (final)']):
    a = data['baseline (10-06 post-fix)'].get(cid, {})
    b = data['thinking-off (round 1)'].get(cid, {})
    c = data['thinking-on (final)'][cid]
    ha = ((a.get('chunk_report') or {}).get('retrieval') or {}).get('chunk_hit@5')
    hb = ((b.get('chunk_report') or {}).get('retrieval') or {}).get('chunk_hit@5')
    hc = ((c.get('chunk_report') or {}).get('retrieval') or {}).get('chunk_hit@5')
    print(f"  {cid[6:]:<26} {category(a):<20} -> {category(b):<32} -> {category(c):<12} hit@5 {ha} -> {hb} -> {hc}")
