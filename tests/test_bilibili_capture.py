"""Bilibili in capture.v1 and instant page Q&A: ids, hosts, parts and source links."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.browser_capture import (
    BrowserCaptureRequest,
    canonicalize_page_url,
    canonicalize_reference,
    cue_content_hash,
    normalized_cues,
    timestamp_url,
)
from app.connectors.bilibili import BilibiliConnector
from app.page_qa import PAGE_QA_STATUS_GROUNDED, PAGE_QA_STATUS_NO_CAPTIONS
from app.page_qa.service import (
    PageQaCompleted,
    PageQaMessageInput,
    PageQaService,
)
from tests.page_qa_support import (
    CONVERSATION_ID,
    TENANT,
    ScriptedModel,
    make_settings,
    message,
    sqlite_factory,
    youtube_page,
)
from tests.test_page_qa_api import BEARER, STREAM_PATH, StubService, build_client

BVID = "BV1xx411c7mD"
CANONICAL = f"https://www.bilibili.com/video/{BVID}"
COVER = "https://i0.hdslb.com/bfs/archive/0123abcd.jpg"


def capture_payload(*, status="available", **overrides):
    caption = (
        {
            "status": "available",
            "source": "auto_caption",
            "language": "zh-CN",
            "cues": [{"start_sec": 1, "end_sec": 2.5, "text": "你好"}],
        }
        if status == "available"
        else {"status": "unavailable", "source": None, "language": None, "cues": []}
    )
    request = {
        "protocol_version": "capture.v1",
        "client_version": "0.2.0",
        "platform": "bilibili",
        "platform_id": BVID,
        "canonical_url": CANONICAL,
        "page_url": CANONICAL,
        "metadata": {
            "title": "一个视频",
            "author": "UP主",
            "cover_url": COVER,
            "tags": [],
            "chapters": [],
        },
        "caption": caption,
        "content_hash": "0" * 64,
    }
    request.update(overrides)
    provisional = BrowserCaptureRequest.model_validate(request)
    request["content_hash"] = cue_content_hash(normalized_cues(provisional.caption))
    return request


# --- capture.v1 contract ------------------------------------------------------------------


def test_bilibili_capture_is_accepted_with_the_connectors_normalized_ids():
    request = BrowserCaptureRequest.model_validate(capture_payload())

    assert request.platform == "bilibili"
    assert canonicalize_reference("bilibili", BVID, request.canonical_url) == CANONICAL
    # The connector derives the very same identity from the same URL.
    assert BilibiliConnector().match(CANONICAL) == BVID
    assert BilibiliConnector().canonical_url(BVID) == CANONICAL
    assert BrowserCaptureRequest.model_validate(
        capture_payload(
            platform_id="av170001",
            canonical_url="https://www.bilibili.com/video/av170001",
            page_url="https://www.bilibili.com/video/av170001",
        )
    ).platform_id == "av170001"


def test_bilibili_capture_without_subtitles_is_valid_and_hashes_empty_input():
    request = BrowserCaptureRequest.model_validate(capture_payload(status="unavailable"))

    assert request.caption.status == "unavailable"
    assert normalized_cues(request.caption) == []


def test_a_later_part_keeps_p_in_the_page_url_but_never_in_the_canonical_reference():
    page = f"{CANONICAL}?p=3"
    request = BrowserCaptureRequest.model_validate(capture_payload(page_url=page))

    assert request.page_url == page
    assert canonicalize_page_url("bilibili", page, platform_id=BVID) == page
    assert canonicalize_page_url("bilibili", f"{CANONICAL}?p=1", platform_id=BVID) == CANONICAL
    assert canonicalize_page_url(
        "bilibili", f"{CANONICAL}?spm_id_from=secret&p=2#frag", platform_id=BVID
    ) == f"{CANONICAL}?p=2"
    assert canonicalize_reference("bilibili", BVID, f"{CANONICAL}?p=3&x=secret") == CANONICAL


def test_reference_normalizes_host_and_id_case_without_accepting_a_different_video():
    assert (
        canonicalize_reference(
            "bilibili", BVID, "https://bilibili.com/video/bv1xx411c7mD/"
        )
        == CANONICAL
    )
    with pytest.raises(ValueError):
        canonicalize_reference("bilibili", "BV1xx411c7mE", CANONICAL)
    with pytest.raises(ValueError):
        canonicalize_reference("bilibili", "bv1xx411c7mD", CANONICAL)  # id must be normalized


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(platform_id="BV1xx411c7m"),  # too short
        lambda v: v.update(platform_id="dQw4w9WgXcQ"),  # a YouTube id
        lambda v: v.update(canonical_url="https://m.bilibili.com/video/" + BVID),
        lambda v: v.update(canonical_url="https://www.bilibili.com.evil.example/video/" + BVID),
        lambda v: v.update(canonical_url="http://www.bilibili.com/video/" + BVID),
        lambda v: v.update(canonical_url="https://www.bilibili.com/bangumi/play/ep1"),
        lambda v: v.update(canonical_url="https://www.bilibili.com/video/BV1xx411c7mE"),
        lambda v: v.update(canonical_url="https://user:pw@www.bilibili.com/video/" + BVID),
        lambda v: v.update(page_url="https://www.bilibili.com/video/BV1xx411c7mE"),
        lambda v: v.update(page_url=f"{CANONICAL}?p=0"),
        lambda v: v.update(page_url=f"{CANONICAL}?p=abc"),
        lambda v: v.update(page_url=f"{CANONICAL}?p=2&p=3"),
        lambda v: v.update(page_url=f"{CANONICAL}?p=100000"),
        lambda v: v.update(page_url="https://space.bilibili.com/1"),
        lambda v: v["metadata"].update(cover_url="http://i0.hdslb.com/bfs/archive/a.jpg"),
        lambda v: v["metadata"].update(cover_url=COVER + "?sign=secret"),
        lambda v: v["metadata"].update(cover_url="https://hdslb.com.evil.example/a.jpg"),
        lambda v: v["metadata"].update(cover_url="https://i.ytimg.com/vi/x/a.jpg"),
        lambda v: v["metadata"].update(cover_url="https://user:pw@i0.hdslb.com/a.jpg"),
    ],
)
def test_bilibili_capture_rejects_foreign_hosts_ids_parts_and_signed_covers(mutation):
    value = copy.deepcopy(capture_payload())
    mutation(value)
    with pytest.raises((ValidationError, ValueError)):
        BrowserCaptureRequest.model_validate(value)


def test_other_platforms_cannot_borrow_the_bilibili_cover_hosts():
    youtube = {
        "protocol_version": "capture.v1",
        "client_version": "0.2.0",
        "platform": "youtube",
        "platform_id": "dQw4w9WgXcQ",
        "canonical_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "page_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "metadata": {"title": "t", "cover_url": COVER},
        "caption": {"status": "unavailable"},
        "content_hash": "0" * 64,
    }
    with pytest.raises(ValidationError):
        BrowserCaptureRequest.model_validate(youtube)


# --- source links --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (CANONICAL, f"{CANONICAL}?t=42"),
        (f"{CANONICAL}?spm_id_from=private&auth_key=secret", f"{CANONICAL}?t=42"),
        (f"{CANONICAL}?p=1", f"{CANONICAL}?t=42"),
        (f"{CANONICAL}?p=2", f"{CANONICAL}?p=2&t=42"),
        (f"{CANONICAL}?spm=x&p=7#frag", f"{CANONICAL}?p=7&t=42"),
        (f"{CANONICAL}?p=abc", f"{CANONICAL}?t=42"),
    ],
)
def test_timestamp_link_keeps_only_a_later_part_and_the_time(url, expected):
    assert timestamp_url("bilibili", url, 42.9) == expected


# --- instant page Q&A ----------------------------------------------------------------------


def bilibili_page(paragraphs, *, page_url=CANONICAL, captions=True, **kwargs):
    page = youtube_page(paragraphs, captions=captions, **kwargs)
    page.update(
        platform="bilibili",
        platform_id=BVID,
        canonical_url=CANONICAL,
        page_url=page_url,
    )
    page["metadata"]["cover_url"] = COVER
    return page


async def collect(service, payload):
    parsed = PageQaMessageInput.model_validate(payload)
    return [
        event
        async for event in service.stream(
            tenant=TENANT,
            conversation_id=CONVERSATION_ID,
            payload=parsed,
            request_id="r" * 32,
        )
    ]


def test_page_qa_request_accepts_bilibili_and_rejects_a_mismatched_video():
    PageQaMessageInput.model_validate(message(bilibili_page(["第一段"], language="zh")))
    PageQaMessageInput.model_validate(
        message(bilibili_page(["第一段"], page_url=f"{CANONICAL}?p=2", language="zh"))
    )
    with pytest.raises(ValidationError):
        PageQaMessageInput.model_validate(
            message(bilibili_page(["x"], page_url="https://www.bilibili.com/video/BV1xx411c7mE"))
        )
    with pytest.raises(ValidationError):
        PageQaMessageInput.model_validate(message(bilibili_page(["x"], page_url=f"{CANONICAL}?p=0")))


def test_caption_less_bilibili_needs_usable_metadata_like_youtube():
    ok = bilibili_page([], captions=False, description="视频简介")
    PageQaMessageInput.model_validate(message(ok))

    bare = bilibili_page([], captions=False, title="", description=None)
    bare["metadata"]["title"] = None
    with pytest.raises(ValidationError):
        PageQaMessageInput.model_validate(message(bare))


@pytest.mark.asyncio
async def test_citations_open_the_asked_part_at_the_cited_time():
    model = ScriptedModel([["视频先讲了学习率 [P2]。"]])
    service = PageQaService(make_settings(), sqlite_factory(), model=model.build())
    page = bilibili_page(
        ["开场白。", "这里讲学习率。", "最后总结。"],
        page_url=f"{CANONICAL}?p=2&spm_id_from=secret",
        language="zh",
    )

    events = await collect(service, message(page, question="讲了什么?"))

    completed = events[-1]
    assert isinstance(completed, PageQaCompleted)
    assert completed.status == PAGE_QA_STATUS_GROUNDED
    [citation] = completed.citations
    assert citation.start_sec == 30
    assert citation.url == f"{CANONICAL}?p=2&t=30"
    assert "secret" not in completed.text


@pytest.mark.asyncio
async def test_citations_of_part_one_use_the_canonical_link():
    model = ScriptedModel([["开场讲了问候 [P1]。"]])
    service = PageQaService(make_settings(), sqlite_factory(), model=model.build())

    events = await collect(service, message(bilibili_page(["开场白。", "正文。"], language="zh")))

    [citation] = events[-1].citations
    assert citation.url == f"{CANONICAL}?t=0"


@pytest.mark.asyncio
async def test_caption_less_bilibili_answers_from_metadata_with_the_label():
    model = ScriptedModel([["视频介绍了梯度下降。"]])
    service = PageQaService(make_settings(), sqlite_factory(), model=model.build())
    page = bilibili_page([], captions=False, description="本期介绍梯度下降", title="梯度下降")

    events = await collect(service, message(page, question="这个视频讲什么?"))

    completed = events[-1]
    assert isinstance(completed, PageQaCompleted)
    assert completed.status == PAGE_QA_STATUS_NO_CAPTIONS
    assert completed.citations == ()
    assert "本期介绍梯度下降" in model.answer_calls[0]["prompt"]
    assert "平台: bilibili" in model.answer_calls[0]["prompt"]


def test_page_qa_route_accepts_a_bilibili_body():
    service = StubService()
    client = build_client(service=service)
    body = message(bilibili_page(["第一段", "第二段"], page_url=f"{CANONICAL}?p=2", language="zh"))

    response = client.post(STREAM_PATH, json=body, headers=BEARER)

    assert response.status_code == 200
    [call] = service.calls
    assert call[2].page.platform == "bilibili"
    assert call[2].page.page_url == f"{CANONICAL}?p=2"


def test_page_qa_route_rejects_a_bilibili_body_with_foreign_ids_or_hosts():
    service = StubService()
    client = build_client(service=service)
    wrong_id = message(bilibili_page(["x"]))
    wrong_id["page"]["platform_id"] = "dQw4w9WgXcQ"
    wrong_host = message(bilibili_page(["x"]))
    wrong_host["page"]["canonical_url"] = "https://www.bilibili.com.evil.example/video/" + BVID

    for body in (wrong_id, wrong_host):
        response = client.post(STREAM_PATH, json=body, headers=BEARER)
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
    assert service.calls == []
