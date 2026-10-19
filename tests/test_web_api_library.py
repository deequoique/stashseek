from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.channels.types import UserScope
from app.ingest.submission import SaveItemResult
from app.web.library import LibraryConflict, LibraryNotFound
from app.web.transcript import TranscriptError


def library_item(**overrides):
    values = {
        "public_id": "item-public",
        "platform": "youtube",
        "kind": "video",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "A title",
        "author": "Author",
        "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        "duration_sec": 42,
        "lang": "en",
        "description": "Description",
        "tags": ("one",),
        "chapters": ({"start": 0, "end": 42, "title": "All"},),
        "cover_url": "https://example.test/cover.jpg",
        "saved_at": datetime(2026, 1, 2, tzinfo=UTC),
        "why_saved": "Later",
        "text_source": "official_cc",
        "lifecycle": "ready",
        "error_code": None,
        "available_actions": ("edit_why_saved", "archive", "open_source"),
        "latest_dispatch_public_id": "dispatch-public",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Library:
    def __init__(self):
        self.calls = []
        self.error = None

    def _result(self):
        if self.error:
            raise self.error
        return library_item()

    def list_items(self, scope, **kwargs):
        self.calls.append(("list", scope, kwargs))
        return SimpleNamespace(
            items=(self._result(),),
            total=1,
            page=kwargs["page"],
            page_size=kwargs["page_size"],
            is_true_first_empty=False,
        )

    def get_item(self, scope, public_id):
        self.calls.append(("detail", scope, public_id))
        return self._result()

    def update_why_saved(self, scope, public_id, why_saved):
        self.calls.append(("patch", scope, public_id, why_saved))
        return self._result()

    def archive(self, scope, public_id):
        self.calls.append(("archive", scope, public_id))
        return self._result()

    def restore(self, scope, public_id):
        self.calls.append(("restore", scope, public_id))
        return self._result()

    def retry(self, scope, public_id, *, request_key, publish_budget_seconds):
        self.calls.append(("retry", scope, public_id, request_key, publish_budget_seconds))
        return self._result()

    def get_dispatch(self, scope, public_id):
        self.calls.append(("dispatch", scope, public_id))
        return SimpleNamespace(
            public_id="dispatch-public",
            item_public_id="item-public",
            attempt=2,
            state="failed",
            error_code="queue_unavailable",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


class Submission:
    def __init__(self):
        self.calls = []

    def submit_urls(self, scope, urls, **kwargs):
        self.calls.append((scope, urls, kwargs))
        return SimpleNamespace(
            results=(
                SaveItemResult(
                    "A1",
                    0,
                    "queued",
                    item_id=41,
                    item_public_id="item-public",
                    state="pending",
                ),
                SaveItemResult(
                    "A2",
                    1,
                    "invalid_url",
                    safe_error_code="invalid_url",
                ),
                SaveItemResult(
                    "A3",
                    2,
                    "already_exists",
                    item_id=42,
                    item_public_id="needs-action-public",
                    state="needs_asr",
                ),
                SaveItemResult(
                    "A4",
                    3,
                    "already_exists",
                    item_id=43,
                    item_public_id="archived-public",
                    state="ready",
                    archived=True,
                ),
                SaveItemResult(
                    "A5",
                    4,
                    "quota_exceeded",
                    safe_error_code="quota_exceeded",
                ),
                SaveItemResult(
                    "A6",
                    5,
                    "restored",
                    item_id=44,
                    item_public_id="restored-public",
                    state="ready",
                ),
                SaveItemResult(
                    "A7",
                    6,
                    "purge_in_progress",
                    item_id=45,
                    state="failed",
                    safe_error_code="purge_in_progress",
                ),
            )
        )


class Transcript:
    def __init__(self):
        self.calls = []
        self.error = None

    def get(self, scope, public_id, **kwargs):
        self.calls.append((scope, public_id, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(
            blocks=(
                SimpleNamespace(
                    ordinal=0,
                    start_sec=0,
                    end_sec=2,
                    text="Original JSON3 text",
                    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0",
                ),
            ),
            next_cursor="opaque-next",
        )


def client_for(
    library=None,
    submission=None,
    transcript=None,
    *,
    user_id=7,
    save_enabled=True,
):
    from app.api.library_routes import build_library_router

    library = library or Library()
    submission = submission or Submission()
    transcript = transcript or Transcript()
    app = FastAPI()
    app.include_router(
        build_library_router(
            library,
            submission,
            transcript,
            publish_budget_seconds=0.25,
            save_enabled=save_enabled,
            scope_dependency=lambda: UserScope(user_id),
        )
    )
    return TestClient(app), library, submission, transcript


def test_list_filters_sort_page_and_detail_use_injected_scope_without_private_ids():
    client, library, _, _ = client_for()

    with client:
        listed = client.get(
            "/api/v1/library/items",
            params={
                "search": "title",
                "lifecycle": "ready",
                "sort": "title_asc",
                "page": 2,
                "page_size": 5,
            },
        )
        detail = client.get("/api/v1/library/items/item-public")
        openapi = client.get("/openapi.json")

    assert listed.status_code == detail.status_code == 200
    assert library.calls[0] == (
        "list",
        UserScope(7),
        {
            "search": "title",
            "collection": None,
            "lifecycle": "ready",
            "include_archived": False,
            "sort": "title_asc",
            "page": 2,
            "page_size": 5,
        },
    )
    assert listed.json()["items"][0]["public_id"] == "item-public"
    assert listed.json()["items"][0]["summary"] is None
    # A service double that predates the counts reports zero, not an error.
    assert (listed.json()["unsearchable_total"], listed.json()["processing_total"]) == (0, 0)
    assert detail.json()["lifecycle"] == "ready"
    chapter_schema = openapi.json()["components"]["schemas"]["LibraryItemResponse"]["properties"]["chapters"]["items"]
    assert chapter_schema["$ref"].endswith("/ChapterResponse")
    serialized = str(listed.json()) + str(detail.json())
    for forbidden in ("user_id", "app_user_id", "channel_identity_id", "item_id"):
        assert forbidden not in serialized
        assert forbidden not in str(openapi.json())


def test_list_reports_the_availability_counts_of_the_whole_library():
    client, library, _, _ = client_for()
    original = library.list_items

    def list_items(scope, **kwargs):
        page = original(scope, **kwargs)
        page.unsearchable_total = 4
        page.processing_total = 2
        return page

    library.list_items = list_items

    with client:
        listed = client.get("/api/v1/library/items", params={"page": 1, "page_size": 5})

    assert listed.status_code == 200
    assert (listed.json()["unsearchable_total"], listed.json()["processing_total"]) == (4, 2)
    assert listed.json()["total"] == 1


def test_batch_is_bounded_partial_and_namespaces_hashed_idempotency_key():
    client, _, submission, _ = client_for()
    raw_key = "browser-secret-retry-key"

    with client:
        response = client.post(
            "/api/v1/library/items:batch",
            headers={"Idempotency-Key": raw_key, "X-CSRF-Token": "csrf"},
            json={"urls": ["https://youtu.be/dQw4w9WgXcQ", "bad"], "why_saved": "Later"},
        )
        too_many = client.post(
            "/api/v1/library/items:batch",
            headers={"Idempotency-Key": raw_key, "X-CSRF-Token": "csrf"},
            json={"urls": [f"https://example.test/{value}" for value in range(11)]},
        )

    assert response.status_code == 200
    assert [value["status"] for value in response.json()["results"]] == [
        "queued",
        "invalid_url",
        "already_exists",
        "already_exists",
        "quota_exceeded",
        "already_exists",
        "create_failed",
    ]
    assert response.json()["results"][0]["item_public_id"] == "item-public"
    assert response.json()["results"][2]["item_public_id"] == "needs-action-public"
    assert response.json()["results"][2]["lifecycle"] == "needs_action"
    assert response.json()["results"][3]["lifecycle"] == "archived"
    assert response.json()["results"][5]["item_public_id"] == "restored-public"
    assert response.json()["results"][5]["lifecycle"] == "ready"
    assert response.json()["results"][6] == {
        "result_id": "A7",
        "input_index": 6,
        "status": "create_failed",
        "item_public_id": None,
        "lifecycle": "failed",
        "safe_error_code": "create_failed",
    }
    assert response.json()["results"][4] == {
        "result_id": "A5",
        "input_index": 4,
        "status": "quota_exceeded",
        "item_public_id": None,
        "lifecycle": None,
        "safe_error_code": "quota_exceeded",
    }
    assert "needs_asr" not in response.text
    scope, urls, kwargs = submission.calls[0]
    assert scope == UserScope(7)
    assert urls == ["https://youtu.be/dQw4w9WgXcQ", "bad"]
    assert kwargs["why_saved"] == "Later"
    assert kwargs["publish_budget_seconds"] == 0.25
    assert kwargs["request_key"].startswith("web:7:")
    assert raw_key not in kwargs["request_key"]
    assert too_many.status_code == 422
    assert too_many.json() == {
        "code": "validation_error",
        "message": "请求参数无效",
    }
    assert "example.test" not in too_many.text
    assert len(submission.calls) == 1


def test_batch_rejects_an_oversized_url_without_echoing_it():
    client, _, submission, _ = client_for()
    private_tail = "PRIVATE" * 400

    with client:
        response = client.post(
            "/api/v1/library/items:batch",
            headers={
                "Idempotency-Key": "bounded-url",
                "X-CSRF-Token": "x" * 32,
            },
            json={"urls": [f"https://youtu.be/{private_tail}"]},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "PRIVATE" not in response.text
    assert submission.calls == []


def test_why_saved_accepts_500_characters_and_rejects_501_at_the_api_boundary():
    client, library, submission, _ = client_for()

    with client:
        accepted_batch = client.post(
            "/api/v1/library/items:batch",
            headers={"Idempotency-Key": "why-500", "X-CSRF-Token": "csrf"},
            json={"urls": ["https://youtu.be/dQw4w9WgXcQ"], "why_saved": "x" * 500},
        )
        rejected_batch = client.post(
            "/api/v1/library/items:batch",
            headers={"Idempotency-Key": "why-501", "X-CSRF-Token": "csrf"},
            json={"urls": ["https://youtu.be/dQw4w9WgXcQ"], "why_saved": "x" * 501},
        )
        accepted_patch = client.patch(
            "/api/v1/library/items/item-public",
            headers={"X-CSRF-Token": "csrf"},
            json={"why_saved": "y" * 500},
        )
        rejected_patch = client.patch(
            "/api/v1/library/items/item-public",
            headers={"X-CSRF-Token": "csrf"},
            json={"why_saved": "y" * 501},
        )

    assert accepted_batch.status_code == 200
    assert accepted_patch.status_code == 200
    assert rejected_batch.status_code == rejected_patch.status_code == 422
    assert rejected_batch.json() == rejected_patch.json() == {
        "code": "validation_error",
        "message": "请求参数无效",
    }
    assert len(submission.calls) == 1
    assert [call for call in library.calls if call[0] == "patch"] == [
        ("patch", UserScope(7), "item-public", "y" * 500),
    ]


def test_patch_archive_restore_retry_dispatch_and_transcript_contracts():
    client, library, _, transcript = client_for()

    with client:
        patch = client.patch(
            "/api/v1/library/items/item-public",
            headers={"X-CSRF-Token": "csrf"},
            json={"why_saved": "Now"},
        )
        archive = client.post(
            "/api/v1/library/items/item-public:archive",
            headers={"X-CSRF-Token": "csrf"},
        )
        restore = client.post(
            "/api/v1/library/items/item-public:restore",
            headers={"X-CSRF-Token": "csrf"},
        )
        retry = client.post(
            "/api/v1/library/items/item-public:retry",
            headers={"Idempotency-Key": "retry-key", "X-CSRF-Token": "csrf"},
        )
        dispatch = client.get("/api/v1/ingest-dispatches/dispatch-public")
        page = client.get(
            "/api/v1/library/items/item-public/transcript",
            params={"limit": 12, "cursor": "opaque-current"},
        )

    assert [patch.status_code, archive.status_code, restore.status_code, retry.status_code] == [200] * 4
    assert library.calls[0] == ("patch", UserScope(7), "item-public", "Now")
    assert library.calls[1][0] == "archive"
    assert library.calls[2][0] == "restore"
    assert library.calls[3][:3] == ("retry", UserScope(7), "item-public")
    assert library.calls[3][3].startswith("web:7:")
    assert library.calls[3][4] == 0.25
    assert dispatch.json()["item_public_id"] == "item-public"
    assert page.json()["next_cursor"] == "opaque-next"
    assert transcript.calls == [
        (UserScope(7), "item-public", {"limit": 12, "cursor": "opaque-current"})
    ]


def test_read_only_mode_blocks_batch_and_retry_before_service_calls():
    client, library, submission, _ = client_for(save_enabled=False)

    with client:
        batch = client.post(
            "/api/v1/library/items:batch",
            headers={"Idempotency-Key": "read-only", "X-CSRF-Token": "csrf"},
            json={"urls": ["https://youtu.be/dQw4w9WgXcQ"]},
        )
        retry = client.post(
            "/api/v1/library/items/item-public:retry",
            headers={"Idempotency-Key": "read-only", "X-CSRF-Token": "csrf"},
        )

    assert batch.status_code == retry.status_code == 503
    assert batch.json() == retry.json() == {"detail": "save_disabled"}
    assert submission.calls == []
    assert library.calls == []


def test_service_failures_map_to_safe_404_409_and_422_statuses():
    library = Library()
    transcript = Transcript()
    client, _, _, _ = client_for(library=library, transcript=transcript)

    with client:
        library.error = LibraryNotFound()
        missing = client.get("/api/v1/library/items/missing")
        library.error = LibraryConflict("retry_unavailable")
        conflict = client.post(
            "/api/v1/library/items/item-public:retry",
            headers={"Idempotency-Key": "retry-key", "X-CSRF-Token": "csrf"},
        )
        library.error = LibraryConflict("quota_exceeded")
        quota = client.post(
            "/api/v1/library/items/item-public:retry",
            headers={"Idempotency-Key": "quota-key", "X-CSRF-Token": "csrf"},
        )
        library.error = None
        transcript.error = TranscriptError("transcript_invalid")
        invalid_cursor = client.get(
            "/api/v1/library/items/item-public/transcript",
            params={"cursor": "corrupt"},
        )

    assert missing.status_code == 404
    assert conflict.status_code == 409
    assert quota.status_code == 429
    assert quota.headers["Retry-After"] == "60"
    assert quota.json() == {"detail": "quota_exceeded"}
    assert invalid_cursor.status_code == 422
    serialized = missing.text + conflict.text + quota.text + invalid_cursor.text
    assert "private" not in serialized
