"""Source clicks reuse the cached part map and degrade to the stored page."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.bilibili.public import BilibiliPart, BilibiliPartMap, BilibiliPublicError
from app.bilibili.service import BilibiliService

BV = "BV1xx411c7mD"
CID = "123"


class Gate:
    def __init__(self, *, admit_error: BilibiliPublicError | None = None) -> None:
        self.upstream_calls = 0
        self.admit_error = admit_error

    def admit(self, _user_id: int) -> None:
        if self.admit_error is not None:
            raise self.admit_error

    @contextmanager
    def upstream(self):
        self.upstream_calls += 1
        yield


class Public:
    def __init__(self, *, error: BilibiliPublicError | None = None) -> None:
        self.calls = 0
        self.error = error

    def get_parts(self, bvid: str) -> BilibiliPartMap:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert bvid == BV
        return BilibiliPartMap(bvid, "video", (
            BilibiliPart(bvid, "100", 1, "P1"),
            BilibiliPart(bvid, CID, 2, "P2"),
        ))


class Result:
    def __init__(self, row) -> None:
        self._row = row

    def one_or_none(self):
        return self._row


class Session:
    def __init__(self, row, *, owned: bool = True) -> None:
        self._row = row
        self._owned = owned

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, _statement):
        return Result(self._row)

    def scalar(self, _statement):
        return 1 if self._owned else None


def service(public: Public, *, row=(BV, CID, 2), owned: bool = True, admit_error: BilibiliPublicError | None = None):
    gate = Gate(admit_error=admit_error)
    return BilibiliService(
        lambda: Session(row, owned=owned),
        submission=SimpleNamespace(),
        public=public,
        signer=SimpleNamespace(),
        gate=gate,
    ), gate


def test_repeated_source_clicks_hit_the_provider_once():
    public = Public()
    bilibili, gate = service(public)
    scope = SimpleNamespace(app_user_id=7)

    first = bilibili.open_source(scope, "item-public", timestamp=12)
    second = bilibili.open_source(scope, "item-public", timestamp=30)

    assert first == f"https://www.bilibili.com/video/{BV}?p=2&t=12"
    assert second == f"https://www.bilibili.com/video/{BV}?p=2&t=30"
    assert public.calls == 1
    assert gate.upstream_calls == 1


def test_provider_throttling_falls_back_to_the_stored_page():
    public = Public(error=BilibiliPublicError("bilibili_rate_limited"))
    bilibili, _gate = service(public, row=(BV, CID, 3))

    assert bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public", timestamp=5) == (
        f"https://www.bilibili.com/video/{BV}?p=3&t=5"
    )


@pytest.mark.parametrize("code", ["bilibili_rate_limited", "bilibili_busy", "bilibili_unavailable"])
def test_every_transient_provider_failure_falls_back_to_the_stored_page(code):
    public = Public(error=BilibiliPublicError(code))
    bilibili, _gate = service(public, row=(BV, CID, 3))

    assert bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public", timestamp=5) == (
        f"https://www.bilibili.com/video/{BV}?p=3&t=5"
    )


@pytest.mark.parametrize("code", ["source_unavailable", "bilibili_limit_exceeded", "bilibili_invalid_url"])
def test_non_transient_failure_never_opens_a_possibly_different_part(code):
    public = Public(error=BilibiliPublicError(code))
    bilibili, _gate = service(public, row=(BV, CID, 3))

    with pytest.raises(BilibiliPublicError) as caught:
        bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public")
    assert caught.value.error_code == code


def test_vanished_cid_does_not_fall_back_to_the_stored_page():
    # The cached part map no longer lists CID 123: resolve_part reports
    # source_unavailable, and opening stored page 3 would show another part.
    class Remapped(Public):
        def get_parts(self, bvid: str) -> BilibiliPartMap:
            self.calls += 1
            return BilibiliPartMap(bvid, "video", (BilibiliPart(bvid, "100", 1, "P1"), BilibiliPart(bvid, "999", 2, "P2")))

    bilibili, _gate = service(Remapped(), row=(BV, CID, 3))

    with pytest.raises(BilibiliPublicError) as caught:
        bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public")
    assert caught.value.error_code == "source_unavailable"


def test_tenant_admission_rate_limit_falls_back_without_an_upstream_call():
    public = Public()
    bilibili, gate = service(public, row=(BV, CID, 3), admit_error=BilibiliPublicError("bilibili_rate_limited"))

    assert bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public", timestamp=9) == (
        f"https://www.bilibili.com/video/{BV}?p=3&t=9"
    )
    assert public.calls == 0 and gate.upstream_calls == 0


def test_admission_outage_without_a_stored_page_stays_fail_closed():
    bilibili, _gate = service(Public(), row=(BV, CID, None), admit_error=BilibiliPublicError("bilibili_rate_limited"))

    with pytest.raises(BilibiliPublicError) as caught:
        bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public")
    assert caught.value.error_code == "bilibili_rate_limited"


def test_failure_without_a_stored_page_stays_fail_closed():
    public = Public(error=BilibiliPublicError("bilibili_rate_limited"))
    bilibili, _gate = service(public, row=(BV, CID, None))

    with pytest.raises(BilibiliPublicError):
        bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public")


def test_deleted_item_after_network_io_is_not_opened():
    public = Public()
    bilibili, _gate = service(public, owned=False)

    with pytest.raises(BilibiliPublicError) as caught:
        bilibili.open_source(SimpleNamespace(app_user_id=7), "item-public")
    assert caught.value.error_code == "not_found"
