"""Read-only source-shape audit; classifications never normalize build inputs."""
from __future__ import annotations

import argparse
import difflib
import json
from collections import Counter, defaultdict
from pathlib import Path


def audit_sources(qmsum: Path, explainmeetsum: Path) -> dict:
    counts = Counter()
    shapes = defaultdict(Counter)
    differences = []
    segmentation = []
    for split in ("train", "val", "test"):
        for path in sorted((qmsum / "data/ALL" / split).glob("*.json")):
            q = json.loads(path.read_text())
            ep = explainmeetsum / "data/ExplainMeetSum" / split / path.name
            e = json.loads(ep.read_text()) if ep.exists() else None
            counts["qmsum_meetings"] += 1
            if e:
                counts["explainmeetsum_meetings"] += 1
                shapes["domain"][e["domain"]] += 1
                shapes["ems_root"][str(sorted(e))] += 1
                qt = [(t["speaker"], t["content"]) for t in q["meeting_transcripts"]]
                et = [(t["speaker"], t["content"]) for t in e["meeting_transcripts"]]
                counts["raw_transcript_equal" if qt == et else "raw_transcript_different"] += 1
                for turn in e["meeting_transcripts"]:
                    shapes["ems_turn"][str(sorted(turn))] += 1
                    for sent in turn["sentence_level_content"]:
                        shapes["ems_sentence"][str(sorted(sent))] += 1
            for kind in ("general", "specific"):
                queries = q[kind + "_query_list"]
                annotations = e["explainable_qmsum"][kind + "_query_list"] if e else []
                counts["qmsum_queries"] += len(queries)
                counts["explainmeetsum_queries"] += len(annotations)
                qc = Counter((x["query"], x["answer"]) for x in queries)
                ec = Counter((x["query"], x["answer"]) for x in annotations)
                for i, item in enumerate(queries):
                    identity = f"qmsum:{path.stem}:{kind}:{i}"
                    shapes["qmsum_query"][str(sorted(item))] += 1
                    key = (item["query"], item["answer"])
                    if qc[key] == ec[key] == 1:
                        counts["exact_unique_matches"] += 1
                        continue
                    candidates = [x for x in annotations if x["query"] == item["query"]]
                    reason = "missing_meeting" if e is None else "missing_query"
                    ops = []
                    if qc[key] > 1 or ec[key] > 1:
                        reason = "ambiguous_exact_tuple"
                    elif len(candidates) == 1:
                        a, b = item["answer"], candidates[0]["answer"]
                        reason = "answer_whitespace_only" if "".join(a.split()) == "".join(b.split()) else "answer_text_changed"
                        ops = [{"op": op, "qmsum": a[s:t], "explainmeetsum": b[u:v], "qmsum_offset": s, "ems_offset": u}
                               for op, s, t, u, v in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != "equal"]
                    counts[reason] += 1
                    differences.append({"case_id": identity, "split": split, "reason": reason, "operations": ops})
                for i, item in enumerate(annotations):
                    shapes["ems_query"][str(sorted(item))] += 1
                    groups = item["explainable_answer"]
                    offset = 0
                    error = None
                    indices = []
                    for gi, group in enumerate(groups):
                        shapes["ems_group"][str(sorted(group))] += 1
                        counts["groups"] += 1
                        indices.extend(group["answer_sentence_index"])
                        counts["merged_groups"] += len(group["answer_sentence_index"]) > 1
                        counts["empty_groups"] += not group["evidence"]
                        counts["evidence_records"] += len(group["evidence"])
                        for entry in group["evidence"]:
                            shapes["evidence"][str(sorted(entry))] += 1
                            counts["label_" + entry["type"]] += 1
                        text = group["answer_sentence"]
                        pos = item["answer"].find(text, offset)
                        if not text or pos < 0 or item["answer"][offset:pos].strip():
                            error = error or "answer_group_text_not_consumed"
                        else:
                            offset = pos + len(text)
                    if item["answer"][offset:].strip():
                        error = error or "answer_suffix_not_consumed"
                    if indices != list(range(len(indices))):
                        error = error or "answer_indices_not_contiguous"
                    if error:
                        counts[error] += 1
                        segmentation.append({"annotation_id": f"ems:{path.stem}:{kind}:{i}", "split": split, "reason": error})
    return {"counts": dict(sorted(counts.items())), "shapes": {k: dict(v) for k, v in sorted(shapes.items())},
            "answer_differences": differences, "answer_mapping_failures": segmentation}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--qmsum", type=Path, required=True)
    parser.add_argument("--explainmeetsum", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_sources(args.qmsum, args.explainmeetsum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(report["counts"], sort_keys=True))
