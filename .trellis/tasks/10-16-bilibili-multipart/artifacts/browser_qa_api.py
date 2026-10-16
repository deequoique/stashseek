"""Disposable in-memory UI fixture. No DB, broker, mail, or upstream requests.

Run from the repository: .venv/bin/python <this-file> --port 8021
This is a fixture transport, NOT the authenticated production API.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import RLock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError

from app.api.bilibili_schemas import BilibiliPartsBatchRequest, BilibiliPartsBatchResponse, BilibiliPartsPreviewRequest, BilibiliPartsPreviewResponse, BilibiliFavoritesPreviewRequest, BilibiliFavoritesPreviewResponse
from app.api.app import CapabilitiesResponse
from app.bilibili.public import BilibiliPart, BilibiliPublicError, parse_video_input, parse_favorites_input
from app.bilibili.refs import PartRefError, PartRefSigner
from demo.stashseek_demo_api import DemoApiHandler, DemoState, ITEMS


SIGNER = PartRefSigner("disposable-browser-qa-not-a-real-auth-secret")
LOCK = RLock()
ATTEMPTS: dict[str, dict] = {}
BATCH_CALLS = 0
FAVORITES_CALLS = 0
PARTS_CALLS = 0


class Handler(DemoApiHandler):
    demo_state = DemoState()
    demo_state.items = [{**deepcopy(ITEMS[0]), "public_id": "qa-verified-part", "platform": "bilibili",
        "url": "/api/v1/library/items/qa-verified-part/source?t=0", "title": "【测试数据】分 P 来源验证 · P2 课程",
        "cover_url": None, "why_saved": "【仅 UI 测试，不会真正导入】", "chapters": [{"title": "测试章节", "start_sec": 185}],
        "available_actions": ["open_source"]}]

    def log_message(self, *_args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/capabilities":
            data = CapabilitiesResponse(
                supported_platforms=["bilibili"], browser_companion=False,
                web_login_channels=["email"], external_login_providers=[],
                save_enabled=True, bilibili_parts_enabled=True, bilibili_favorites_enabled=True, max_save_batch_size=10,
                transcript_pagination=True, archive=False, summary_generation=False, chat=False,
            )
            return self._json(200, data.model_dump(mode="json"))
        if parsed.path == "/api/v1/qa/stats":
            return self._json(200, {"fixture_only": True, "batch_calls": BATCH_CALLS, "unique_operations": len(ATTEMPTS), "favorites_calls": FAVORITES_CALLS, "parts_calls": PARTS_CALLS})
        if parsed.path.endswith("/qa-verified-part/transcript"):
            return self._json(200, {"blocks": [{"ordinal": 0, "start_sec": 12, "end_sec": 16,
                "text": "【测试数据】这一段来自已验证分 P，时间是本 P 的 12 秒。",
                "source_url": "/api/v1/library/items/qa-verified-part/source?t=12"}], "next_cursor": None})
        if parsed.path.endswith("/qa-verified-part/source"):
            seconds = max(0, min(2147483647, int(parse_qs(parsed.query).get("t", ["0"])[0])))
            # Never navigate to the real provider during disposable fixture QA.
            return self._json(200, {"fixture_only": True, "message": "【测试数据】这里只显示预期来源，不访问 B 站。",
                "expected_provider_location": f"https://www.bilibili.com/video/BV1xx411c7mD?p=6&t={seconds}"})
        return super().do_GET()

    def do_POST(self):
        global BATCH_CALLS, FAVORITES_CALLS, PARTS_CALLS
        path = urlparse(self.path).path
        if path not in {"/api/v1/bilibili/parts:preview", "/api/v1/bilibili/parts:batch", "/api/v1/bilibili/favorites:preview"}:
            return self._error(404, "qa_only", "测试环境只提供分 P 操作，不会保存普通视频。")
        body = self._body()
        if body is None:
            return
        try:
            if path == "/api/v1/bilibili/favorites:preview":
                FAVORITES_CALLS += 1
                request = BilibiliFavoritesPreviewRequest.model_validate(body)
                collection = parse_favorites_input(request.url)
                if collection == "403":
                    return self._error(409, "bilibili_favorites_private", "【测试数据】私密收藏夹。")
                if collection == "429":
                    return self._error(429, "bilibili_rate_limited", "【测试数据】临时限制。")
                count = 22 if collection == "22" else 8
                items = []
                for index in range((request.page - 1) * 20 + 1, min(request.page * 20, count) + 1):
                    # Repeated first parent on page two exercises stable-key dedupe.
                    bvid = "BV1xx411c001" if index == 21 else f"BV1xx411c{index:03d}"
                    items.append({"entry_key": f"qa-parent-{index}", "bvid": bvid if index != 4 else None,
                        "canonical_url": f"https://www.bilibili.com/video/{bvid}" if index != 4 else None,
                        "title": f"【测试数据】收藏夹视频 {index}", "available": index != 4,
                        "unavailable_reason": "not_video" if index == 4 else None})
                response = BilibiliFavoritesPreviewResponse(collection_id=collection,
                    title=f"【测试数据】study · {count} 个条目（无真实导入）", reported_total=count,
                    page=request.page, page_size=20, has_more=request.page * 20 < count,
                    capped=False, items=items)
                return self._json(200, response.model_dump(mode="json"))
            if path.endswith(":preview"):
                PARTS_CALLS += 1
                request = BilibiliPartsPreviewRequest.model_validate(body)
                bvid, requested = parse_video_input(request.url)
                page = request.page or ((requested - 1) // 20 + 1 if requested else 1)
                parts = []
                for index in range((page - 1) * 20 + 1, min(page * 20, 29) + 1):
                    part = BilibiliPart(bvid, str(1000 + index), index)
                    ref, expiry = SIGNER.issue_with_expiry(7, part)
                    parts.append({"part_key": f"{bvid}:{part.cid}", "part_ref": ref if index != 4 else None,
                        "expires_at": expiry if index != 4 else None,
                        "page": index, "title": f"【测试数据】课程 {index}", "duration_sec": index * 10,
                        "selectable": index != 4, "unavailable_reason": "source_unavailable" if index == 4 else None,
                        "has_unverified_legacy_item": index == 3})
                response = BilibiliPartsPreviewResponse(bvid=bvid, title="【测试数据】29 分 P 课程（无真实导入）",
                    page=page, page_size=20, total_parts=29, requested_page=requested, has_more=page == 1, parts=parts)
                return self._json(200, response.model_dump(mode="json"))
            request = BilibiliPartsBatchRequest.model_validate(body)
            key = self.headers.get("Idempotency-Key")
            if not key:
                return self._error(422, "validation_error", "缺少请求标识。")
            with LOCK:
                BATCH_CALLS += 1
                if key in ATTEMPTS:
                    saved = ATTEMPTS[key]
                    if saved["input"] != body:
                        return self._error(409, "qa_payload_changed", "同一次测试请求的内容发生变化。")
                    return self._json(200, saved["response"])
                results = []
                for index, selection in enumerate(request.selections):
                    try:
                        part = SIGNER.verify(7, selection.part_ref)
                        status = "legacy_identity_unknown" if part.page == 3 and not selection.allow_legacy_overlap else "quota_exceeded" if part.page == 2 else "already_exists" if part.page == 1 else "queued"
                    except PartRefError as error:
                        status = error.error_code
                    results.append({"result_id": f"qa-result-{index}", "input_index": index, "status": status})
                response = BilibiliPartsBatchResponse(results=results).model_dump(mode="json")
                ATTEMPTS[key] = {"input": body, "response": response}
                # A first uncertain response has a replayable RAM result. Put
                # 'unknown' in the note to exercise the same-key retry UI.
                if request.why_saved and "unknown" in request.why_saved:
                    return self._error(503, "qa_uncertain", "【测试数据】故意中断首次结果；可使用相同请求重新确认。")
                return self._json(200, response)
        except (ValidationError, BilibiliPublicError):
            return self._error(422, "validation_error", "测试请求参数无效。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8021)
    args = parser.parse_args()
    print(f"Disposable UI fixture only: http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
