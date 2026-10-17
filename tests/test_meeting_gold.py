import copy
import json
from pathlib import Path

import pytest

from evals.meeting_gold.core import (
    Audit,
    SourceFormatError,
    canonical_bytes,
    stable_case_id,
    stable_sentence_id,
)
from evals.meeting_gold.sources import join_explainable, load_json_files, parse_qmsum


PINNED_QMSUM = Path("/tmp/qmsum-gold-audit-pinned/data/ALL")
PINNED_EXPLAINMEETSUM = Path(
    "/tmp/explainmeetsum-gold-audit-pinned/data/ExplainMeetSum"
)

# Reduced, source-shaped fixtures using verbatim fields from the pinned
# QMSum and ExplainMeetSum sources.
REAL_FIXTURE_ATTRIBUTION = (
    "QMSum 83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e and "
    "ExplainMeetSum d8459311a6578cd438a785bf19dd2fa757ade1c5; "
    "test/Bmr023.json"
)
REAL_FIXTURES = {
    "Academic(icsi)": {
        "split": "train", "meeting_id": "Bro003",
        "turn": {"speaker": "Grad C", "content": "Tell him about the cube ."},
        "general": ("Summarize the meeting", "The meeting included an introduction to the project, the mechanics of training the data, standardizing the data for the models, the time involved, and planning efficient use of computational resources . The team members shared and discussed the existing model as well as the tests they intended to run. There was a problem standardizing labels as well as converting HTK labels to the format that the team wanted. Adding more dimensions to the current model was also causing concern because of the upper bound on computational resources."),
        "specific": ("Did the Professor agree with Grad G on cross-lingual data?", "Grad G thought that the multi-lingual aspect of the model was not very important. The professor disagreed. He explained that the point of the project was to have something robust that could apply to many languages."),
    },
    "Product(ami)": {
        "split": "train", "meeting_id": "ES2012b",
        "turn": {"speaker": "Project Manager", "content": "welcome to the second meeting of this uh design group ."},
        "general": ("Summarize the whole meeting.", "This is the second meeting of the design group. Project Manager went through the notes of the last meeting and informed the team of the management's requirements. They expected the remote control to cover television only and asked the team to incorporate the company logo and colours. As the team is looking at extreme simplicity, the buttons should be reduced to the absolute minimum. Voice recognition can also be added to reach this point. In addition, the remote control can be powered by a rechargeable or permanent battery."),
        "specific": ("Why didn't the team believe that the remote control could fully depend on speech recognition and have no buttons?", "Age group data for remote control use was not available; many people may not want to learn to use the new remote control; some buttons are still needed, such as channel control, volume settings and on/off."),
    },
    "Committee": {
        "split": "train", "meeting_id": "covid_7",
        "turn": {"speaker": "The Chair (Hon. Anthony Rota (NipissingTimiskaming, Lib.))", "content": "I call this meeting to order."},
        "general": ("Summarize the whole meeting.", "The meeting was with the Special Committee on the COVID-19 Pandemic. First of all, petitioners presented several petitions including to call the government to implement a wage supplement. Later the committee expressed the thanks to different departments and people for the contribution to the society under the Covid-19 situation. When it came to questions, the committee discussed the financial support from the government to various industries as well as groups to ensure equal rights of different groups of people, especially the most vulnerable ones. Moreover, the meeting discussed the new law in Hong Kong and issues related to community services in Canada. In the end, the committee discussed the recent and upcoming solutions to battle the Covid-19 concerning various aspects."),
        "specific": ("What were the reasons to thank the members of the Canadian Armed Forces?", "Ms. Anju Dhillon would like to thank the members of the Canadian Armed Forces for their invaluable assistance in the long-term care facilities. Since many seniors, the most vulnerable ones desperately needed them and they had offered great assistance."),
    },
}


def _evidence(
    *,
    label="CES",
    turn_index=0,
    sent_index=0,
    speaker="Alice",
    content="Hello .",
):
    return {
        "type": label,
        "turn_index": turn_index,
        "sent_index": sent_index,
        "speaker": speaker,
        "content": content,
    }


def _meeting_records(
    *,
    meeting_id="m",
    q_general=None,
    e_general=None,
    q_turns=None,
    e_turns=None,
    q_domain=None,
):
    q_turns = q_turns or [
        {"speaker": "Alice", "content": "Hello ."},
        {"speaker": "Bob", "content": "World ."},
    ]
    if e_turns is None:
        e_turns = []
        for turn_index, turn in enumerate(q_turns):
            e_turns.append(
                {
                    **turn,
                    "sentence_level_content": [
                        {
                            "turn_index": turn_index,
                            "sent_index": 0,
                            "dialogue_sentence": turn["content"],
                            "dialogue-act_id": f"{meeting_id}.{turn['speaker']}.act{turn_index}",
                        }
                    ],
                }
            )
    q_general = q_general or [{"query": "What?", "answer": "Hello . World ."}]
    if e_general is None:
        e_general = [
            {
                "query": "What?",
                "answer": "Hello . World .",
                "explainable_answer": [
                    {
                        "answer_sentence": "Hello .",
                        "answer_sentence_index": [0],
                        "evidence": [_evidence()],
                    },
                    {
                        "answer_sentence": "World .",
                        "answer_sentence_index": [1],
                        "evidence": [
                            _evidence(
                                label="PES",
                                turn_index=1,
                                speaker="Bob",
                                content="World .",
                            )
                        ],
                    },
                ],
            }
        ]
    q_record = {
        "topic_list": [],
        "meeting_transcripts": q_turns,
        "general_query_list": q_general,
        "specific_query_list": [],
    }
    if q_domain is not None:
        q_record["domain"] = q_domain
    e_record = {
        "domain": "Committee",
        "topic_list": [],
        "meeting_transcripts": e_turns,
        "explainable_ami": {},
        "explainable_icsi": {},
        "explainable_qmsum": {
            "general_query_list": e_general,
            "specific_query_list": [],
        },
    }
    return q_record, e_record


def _source(
    tmp_path,
    *,
    meeting_id="m",
    q_general=None,
    e_general=None,
    q_turns=None,
    e_turns=None,
    q_domain=None,
):
    q_root = tmp_path / "qmsum"
    e_root = tmp_path / "explainmeetsum"
    q_root.mkdir(parents=True)
    e_root.mkdir(parents=True)
    q_record, e_record = _meeting_records(
        meeting_id=meeting_id,
        q_general=q_general,
        e_general=e_general,
        q_turns=q_turns,
        e_turns=e_turns,
        q_domain=q_domain,
    )
    (q_root / f"{meeting_id}.json").write_text(
        json.dumps(q_record), encoding="utf-8"
    )
    (e_root / f"{meeting_id}.json").write_text(
        json.dumps(e_record), encoding="utf-8"
    )
    return q_root, e_root


def _attributed_real_source(tmp_path):
    return _attributed_fixture(tmp_path, REAL_FIXTURES["Academic(icsi)"])


def _attributed_fixture(tmp_path, fixture):
    q_root = tmp_path / "qmsum"
    e_root = tmp_path / "explainmeetsum"
    q_root.mkdir(parents=True)
    e_root.mkdir(parents=True)
    queries = {
        kind: {"query": fixture[kind][0], "answer": fixture[kind][1]}
        for kind in ("general", "specific")
    }
    q_record = {
        "topic_list": [],
        "meeting_transcripts": [fixture["turn"]],
        "general_query_list": [queries["general"]],
        "specific_query_list": [queries["specific"]],
    }
    e_record = {
        "domain": "Academic(icsi)",
        "topic_list": [],
        "meeting_transcripts": [
            {
                **fixture["turn"],
                "sentence_level_content": [
                    {
                        "turn_index": 0,
                        "sent_index": 0,
                        "dialogue_sentence": fixture["turn"]["content"],
                        "dialogue-act_id": None,
                    }
                ],
            }
        ],
        "explainable_ami": {},
        "explainable_icsi": {},
        "explainable_qmsum": {
            "general_query_list": [queries["general"]],
            "specific_query_list": [queries["specific"]],
        },
    }
    (q_root / f"{fixture['meeting_id']}.json").write_text(
        json.dumps(q_record), encoding="utf-8"
    )
    (e_root / f"{fixture['meeting_id']}.json").write_text(
        json.dumps(e_record), encoding="utf-8"
    )
    return q_root, e_root


def _load_and_join(q_root, e_root, split="val"):
    audit = Audit()
    corpus, cases = parse_qmsum(q_root, split, audit)
    join_explainable(cases, e_root, split, corpus, audit)
    return corpus, cases, audit


def test_attributed_real_fixture_starts_without_qmsum_sentence_fallback(tmp_path):
    q_root, e_root = _attributed_real_source(tmp_path)
    audit = Audit()
    corpus, cases = parse_qmsum(q_root, "train", audit)

    assert REAL_FIXTURE_ATTRIBUTION.startswith("QMSum ")
    assert corpus["Bro003"] == []
    assert corpus["Bro003"].qmsum_turns[0].text == REAL_FIXTURES["Academic(icsi)"]["turn"]["content"]

    join_explainable(cases, e_root, "train", corpus, audit)

    assert len(corpus["Bro003"]) == 1
    assert corpus["Bro003"][0].id == stable_sentence_id("Bro003", 0, 0)
    assert {case.query_kind for case in cases} == {"general", "specific"}


@pytest.mark.parametrize("domain", ["Product(ami)", "Committee"])
def test_attributed_real_fixture_covers_remaining_domains_and_query_kinds(tmp_path, domain):
    fixture = REAL_FIXTURES[domain]
    q_root, e_root = _attributed_fixture(tmp_path, fixture)

    audit = Audit()
    corpus, cases = parse_qmsum(q_root, fixture["split"], audit)
    assert corpus[fixture["meeting_id"]] == []

    join_explainable(cases, e_root, fixture["split"], corpus, audit)

    assert {case.query_kind for case in cases} == {"general", "specific"}
    assert all(case.answer for case in cases)
    assert all(case.provenance["explainmeetsum_file"] == f"{fixture['meeting_id']}.json" for case in cases)


def test_pinned_test_split_uses_real_source_shapes_and_resolves_pointers():
    if not (PINNED_QMSUM / "test").is_dir() or not (
        PINNED_EXPLAINMEETSUM / "test"
    ).is_dir():
        pytest.skip("pinned audit sources are unavailable")

    corpus, cases, audit = _load_and_join(
        PINNED_QMSUM / "test", PINNED_EXPLAINMEETSUM / "test", split="test"
    )

    assert len(corpus) == 35
    assert len(cases) == 281
    assert any(sentence.sentence_index > 0 for sentence in corpus["Bed003"])
    assert any(case.eligibility["ces_pes"] == "citation_scorable" for case in cases)
    assert all(set(case.eligibility) == {"ces", "ces_pes"} for case in cases)
    assert all(
        len(case.evidence_groups) == len(case.evidence_types)
        for case in cases
        if case.evidence_groups
    )
    assert not {
        record["code"]
        for record in audit.records
    } & {
        "pointer_out_of_bounds",
        "pointer_text_mismatch",
        "answer_indices_not_contiguous",
        "answer_group_text_not_consumed",
        "transcript_mismatch",
    }


def test_exact_tuple_join_uses_source_ordinals_not_annotation_order(tmp_path):
    q_general = [
        {"query": "first", "answer": "Hello ."},
        {"query": "second", "answer": "World ."},
    ]
    e_general = [
        {
            "query": "second",
            "answer": "World .",
            "explainable_answer": [
                {
                    "answer_sentence": "World .",
                    "answer_sentence_index": [0],
                    "evidence": [
                        _evidence(
                            label="CES",
                            turn_index=1,
                            speaker="Bob",
                            content="World .",
                        )
                    ],
                }
            ],
        },
        {
            "query": "first",
            "answer": "Hello .",
            "explainable_answer": [
                {
                    "answer_sentence": "Hello .",
                    "answer_sentence_index": [0],
                    "evidence": [_evidence()],
                }
            ],
        },
    ]
    q_root, e_root = _source(
        tmp_path, q_general=q_general, e_general=e_general
    )

    corpus, cases, audit = _load_and_join(q_root, e_root)

    assert not audit.records
    by_id = {case.case_id: case for case in cases}
    first = by_id[stable_case_id("m", "general", 0)]
    second = by_id[stable_case_id("m", "general", 1)]
    assert first.evidence_groups == [[stable_sentence_id("m", 0, 0)]]
    assert second.evidence_groups == [[stable_sentence_id("m", 1, 0)]]
    assert first.provenance["explainmeetsum_query_index"] == 1
    assert second.provenance["explainmeetsum_query_index"] == 0
    assert corpus["m"][0].dialogue_act_id == "m.Alice.act0"
    assert corpus["m"].qmsum_turns[1].text == "World ."
    assert corpus["m"].explainmeetsum_turns[1].text == "World ."


def test_policy_specific_eligibility_and_empty_groups_are_preserved(tmp_path):
    q_root, e_root = _source(tmp_path)

    corpus, cases, audit = _load_and_join(q_root, e_root)

    assert not audit.records
    case = cases[0]
    assert case.evidence_groups == [
        [stable_sentence_id("m", 0, 0)],
        [stable_sentence_id("m", 1, 0)],
    ]
    assert case.evidence_types == [["CES"], ["PES"]]
    assert case.eligibility == {
        "ces": "partial_annotation",
        "ces_pes": "citation_scorable",
    }
    assert case.lifecycle_state == "partial_annotation"
    assert case.answer_sentence_texts == ["Hello .", "World ."]
    assert case.evidence_records[1][0]["type"] == "PES"

    empty_group = {
        "query": "What?",
        "answer": "Hello .",
        "explainable_answer": [
            {
                "answer_sentence": "Hello .",
                "answer_sentence_index": [0],
                "evidence": [],
            }
        ],
    }
    q_root, e_root = _source(
        tmp_path / "empty", q_general=[{"query": "What?", "answer": "Hello ."}], e_general=[empty_group]
    )
    _, empty_cases, empty_audit = _load_and_join(q_root, e_root)
    assert not empty_audit.records
    assert empty_cases[0].evidence_groups == [[]]
    assert empty_cases[0].evidence_types == [[]]
    assert empty_cases[0].eligibility == {
        "ces": "partial_annotation",
        "ces_pes": "partial_annotation",
    }


def test_absent_explainable_answer_is_partial_not_answer_only(tmp_path):
    q_root, e_root = _source(
        tmp_path,
        e_general=[{"query": "What?", "answer": "Hello . World ."}],
    )

    _, cases, audit = _load_and_join(q_root, e_root)

    assert cases[0].lifecycle_state == "partial_annotation"
    assert cases[0].eligibility == {
        "ces": "partial_annotation",
        "ces_pes": "partial_annotation",
    }
    assert any(record["code"] == "missing_explainable_answer" for record in audit.records)


def test_invalid_pointer_is_quarantined_and_kept_out_of_scoring_views(tmp_path):
    e_general = copy.deepcopy(_meeting_records()[1]["explainable_qmsum"]["general_query_list"])
    e_general[0]["explainable_answer"][0]["evidence"][0]["sent_index"] = 9
    q_root, e_root = _source(tmp_path, e_general=e_general)

    _, cases, audit = _load_and_join(q_root, e_root)

    assert cases[0].lifecycle_state == "quarantined"
    assert cases[0].eligibility == {"ces": "quarantined", "ces_pes": "quarantined"}
    assert any(record["code"] == "pointer_out_of_bounds" for record in audit.records)


def test_invalid_ces_pointer_does_not_shift_valid_pes_type(tmp_path):
    e_general = copy.deepcopy(
        _meeting_records()[1]["explainable_qmsum"]["general_query_list"]
    )
    e_general[0]["explainable_answer"][0]["evidence"] = [
        _evidence(label="CES", sent_index=9),
        _evidence(
            label="PES",
            turn_index=1,
            speaker="Bob",
            content="World .",
        ),
    ]
    q_root, e_root = _source(tmp_path, e_general=e_general)

    _, cases, audit = _load_and_join(q_root, e_root)

    case = cases[0]
    assert case.lifecycle_state == "quarantined"
    assert case.evidence_groups[0] == [stable_sentence_id("m", 1, 0)]
    assert case.evidence_types[0] == ["PES"]
    assert [record["type"] for record in case.evidence_records[0]] == [
        "CES",
        "PES",
    ]
    assert any(record["code"] == "pointer_out_of_bounds" for record in audit.records)


@pytest.mark.parametrize(
    ("mutation", "audit_code"),
    [
        (lambda group: group.update(answer_sentence="Other ."), "answer_group_text_not_consumed"),
        (lambda group: group.update(answer_sentence_index=[0, 2]), "invalid_answer_indices"),
        (lambda group: group["evidence"][0].update(content="Wrong ."), "pointer_text_mismatch"),
        (lambda group: group["evidence"][0].update(type="OTHER"), "unknown_evidence_type"),
    ],
)
def test_annotation_validation_quarantines_hard_failures(tmp_path, mutation, audit_code):
    e_general = copy.deepcopy(_meeting_records()[1]["explainable_qmsum"]["general_query_list"])
    mutation(e_general[0]["explainable_answer"][0])
    q_root, e_root = _source(tmp_path, e_general=e_general)

    _, cases, audit = _load_and_join(q_root, e_root)

    assert cases[0].lifecycle_state == "quarantined"
    assert any(record["code"] == audit_code for record in audit.records)


def test_invalid_answer_indices_preserve_raw_annotation_groups(tmp_path):
    e_general = copy.deepcopy(
        _meeting_records()[1]["explainable_qmsum"]["general_query_list"]
    )
    e_general[0]["explainable_answer"][0]["answer_sentence_index"] = [0, 2]
    q_root, e_root = _source(tmp_path, e_general=e_general)

    _, cases, _ = _load_and_join(q_root, e_root)

    case = cases[0]
    assert case.lifecycle_state == "quarantined"
    assert case.answer_sentence_index == [[], [1]]
    assert case.provenance["raw_annotation_groups"][0]["answer_sentence_index"] == [0, 2]
    assert case.provenance["raw_annotation_groups"][0]["evidence"][0]["type"] == "CES"


def test_exact_tuple_mismatch_does_not_fall_back_to_query_only(tmp_path):
    q_root, e_root = _source(
        tmp_path,
        q_general=[{"query": "What?", "answer": "Hello ."}],
        e_general=[
            {
                "query": "What?",
                "answer": "Hello . ",
                "explainable_answer": [],
            }
        ],
    )

    _, cases, audit = _load_and_join(q_root, e_root)

    assert cases[0].eligibility == {"ces": "answer_only", "ces_pes": "answer_only"}
    assert any(
        record["code"] == "unmatched_qmsum"
        and record["reason"] == "missing_explainmeetsum_query"
        for record in audit.records
    )
    assert any(
        record["code"] == "unmatched_explainable"
        and record["reason"] == "missing_qmsum_query"
        for record in audit.records
    )


def test_duplicate_exact_tuple_is_ambiguous_not_resolved_by_ordinal(tmp_path):
    q_general = [
        {"query": "same", "answer": "Hello ."},
        {"query": "same", "answer": "Hello ."},
    ]
    e_general = [
        {
            "query": "same",
            "answer": "Hello .",
            "explainable_answer": [
                {
                    "answer_sentence": "Hello .",
                    "answer_sentence_index": [0],
                    "evidence": [_evidence()],
                }
            ],
        }
    ]
    q_root, e_root = _source(
        tmp_path, q_general=q_general, e_general=e_general
    )

    _, cases, audit = _load_and_join(q_root, e_root)

    assert [case.lifecycle_state for case in cases] == ["quarantined", "quarantined"]
    assert sum(record["code"] == "ambiguous_join" for record in audit.records) == 2
    assert any(record["code"] == "unmatched_explainable" for record in audit.records)


def test_transcript_mismatch_quarantines_the_meeting_cases(tmp_path):
    e_turns = copy.deepcopy(_meeting_records()[1]["meeting_transcripts"])
    e_turns[1]["content"] = "Changed ."
    q_root, e_root = _source(tmp_path, e_turns=e_turns)

    corpus, cases, audit = _load_and_join(q_root, e_root)

    assert cases[0].eligibility == {"ces": "quarantined", "ces_pes": "quarantined"}
    assert any(record["code"] == "transcript_mismatch" for record in audit.records)
    assert cases[0].provenance["transcript_mismatch"] is True
    assert corpus["m"] == []
    assert corpus["m"].qmsum_turns[1].text == "World ."
    assert corpus["m"].explainmeetsum_turns[1].text == "Changed ."


def test_missing_meeting_and_unexpected_meeting_are_both_audited(tmp_path):
    q_root, e_root = _source(tmp_path)
    (e_root / "m.json").unlink()
    _, extra_record = _meeting_records(meeting_id="other")
    (e_root / "other.json").write_text(json.dumps(extra_record), encoding="utf-8")

    corpus, cases, audit = _load_and_join(q_root, e_root)

    assert cases[0].eligibility == {"ces": "answer_only", "ces_pes": "answer_only"}
    assert corpus["m"] == []
    assert corpus["m"].qmsum_turns[0].text == "Hello ."
    assert any(record["code"] == "missing_explainmeetsum_meeting" for record in audit.records)
    assert any(record["code"] == "unexpected_explainmeetsum_meeting" for record in audit.records)


def test_domain_mismatch_quarantine_survives_missing_explainable_block(tmp_path):
    q_root, e_root = _source(tmp_path, q_domain="Academic(icsi)")
    e_record = json.loads((e_root / "m.json").read_text(encoding="utf-8"))
    del e_record["explainable_qmsum"]
    (e_root / "m.json").write_text(json.dumps(e_record), encoding="utf-8")

    _, cases, audit = _load_and_join(q_root, e_root)

    case = cases[0]
    assert case.lifecycle_state == "quarantined"
    assert case.eligibility == {"ces": "quarantined", "ces_pes": "quarantined"}
    assert "domain_mismatch" in case.provenance["alignment_reasons"]
    assert "missing_explainable_qmsum" in case.provenance["alignment_reasons"]
    assert any(record["code"] == "domain_mismatch" for record in audit.records)
    assert any(record["code"] == "missing_explainable_qmsum" for record in audit.records)


def test_domain_mismatch_quarantine_survives_unmatched_tuple(tmp_path):
    q_root, e_root = _source(
        tmp_path,
        q_domain="Academic(icsi)",
        q_general=[{"query": "What?", "answer": "Different ."}],
    )

    _, cases, audit = _load_and_join(q_root, e_root)

    case = cases[0]
    assert case.lifecycle_state == "quarantined"
    assert case.eligibility == {"ces": "quarantined", "ces_pes": "quarantined"}
    assert "domain_mismatch" in case.provenance["alignment_reasons"]
    assert "missing_explainmeetsum_query" in case.provenance["alignment_reasons"]
    assert any(record["code"] == "unmatched_qmsum" for record in audit.records)


@pytest.mark.parametrize(
    "span",
    [
        [["bogus", "0"]],
        [["0", "999"]],
        [["1", "0"]],
    ],
)
def test_relevant_spans_are_numeric_ordered_and_in_raw_turn_bounds(tmp_path, span):
    q_root, _ = _source(
        tmp_path,
        q_general=[
            {
                "query": "What?",
                "answer": "Hello .",
                "relevant_text_span": span,
            }
        ],
    )

    with pytest.raises(SourceFormatError, match="invalid_relevant_text_span"):
        parse_qmsum(q_root, "val", Audit())


@pytest.mark.parametrize("field", ["query", "answer"])
def test_empty_query_or_answer_is_rejected(tmp_path, field):
    query = "" if field == "query" else "What?"
    answer = "" if field == "answer" else "Hello ."
    q_root, _ = _source(
        tmp_path,
        q_general=[{"query": query, "answer": answer}],
    )

    with pytest.raises(SourceFormatError, match="invalid_query"):
        parse_qmsum(q_root, "val", Audit())


def test_duplicate_json_keys_and_non_finite_values_are_rejected(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "duplicate.json").write_text(
        '{"meeting_transcripts": [], "meeting_transcripts": []}', encoding="utf-8"
    )
    with pytest.raises(SourceFormatError, match="duplicate_json_key"):
        load_json_files(root)

    (root / "duplicate.json").unlink()
    (root / "nan.json").write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(SourceFormatError, match="non_finite_json"):
        load_json_files(root)


def test_canonical_json_is_stable():
    assert canonical_bytes({"b": 1, "a": "x"}) == b'{"a":"x","b":1}\n'
