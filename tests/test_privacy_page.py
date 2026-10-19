"""Public, login-free privacy policy page served by the API itself."""

import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.api.privacy import PRIVACY_EFFECTIVE_DATE, render_privacy_page
from app.config import DEFAULT_PRIVACY_CONTACT_EMAIL, Settings
from app.page_qa.service import MAX_EXCERPT_CHARS

ORIGIN = "https://kb.example.test"
SPA_MARKER = "<html>spa-shell</html>"


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service use: {name}")


def make_app(*, settings=None, static_dir=None, services=True):
    web_services = (
        WebApiServices(
            web_auth=Unused(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            settings=settings,
        )
        if services
        else None
    )
    return create_app(
        services=web_services,
        expected_origin=ORIGIN if services else None,
        static_dir=static_dir,
    )


def client_for(app):
    return TestClient(app, base_url=ORIGIN)


def article(page: str, language: str) -> str:
    match = re.search(
        rf'<article id="{language}"[^>]*>(.*?)</article>', page, flags=re.DOTALL
    )
    assert match, f"missing {language} article"
    return match.group(1)


def headings(section: str) -> list[str]:
    return re.findall(r"<h2>(.*?)</h2>", section)


@pytest.fixture
def spa_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text(SPA_MARKER)
    return tmp_path


@pytest.fixture(params=["services", "no_services"])
def app(request):
    if request.param == "services":
        return make_app(settings=SimpleNamespace())
    return make_app(services=False)


def test_page_and_stylesheet_are_public(app):
    client = client_for(app)

    page = client.get("/privacy")
    sheet = client.get("/privacy/privacy.css")

    assert page.status_code == 200
    assert page.headers["content-type"] == "text/html; charset=utf-8"
    assert sheet.status_code == 200
    assert sheet.headers["content-type"].startswith("text/css")
    assert sheet.headers["cache-control"] == "public, max-age=3600"
    assert page.text.startswith("<!doctype html>")


def test_head_and_trailing_slash_are_served(app):
    client = client_for(app)

    assert client.head("/privacy").status_code == 200
    slash = client.get("/privacy/")
    assert slash.status_code == 200
    assert slash.text == client.get("/privacy").text


@pytest.mark.parametrize("with_services", [True, False])
def test_routes_win_over_the_spa_catch_all(spa_dir: Path, with_services: bool):
    app = make_app(
        settings=SimpleNamespace() if with_services else None,
        static_dir=spa_dir,
        services=with_services,
    )
    client = client_for(app)

    page = client.get("/privacy")
    sheet = client.get("/privacy/privacy.css")

    assert SPA_MARKER not in page.text
    assert "隐私政策" in page.text
    assert sheet.headers["content-type"].startswith("text/css")
    # The catch-all still answers everything else.
    assert client.get("/library/some-route").text == SPA_MARKER
    assert client_for(app).get("/privacy/").text == page.text


def test_routes_are_excluded_from_openapi(app):
    paths = app.openapi()["paths"]

    assert not any(path.startswith("/privacy") for path in paths)


def test_security_headers_apply_and_the_page_needs_no_inline_code(app):
    client = client_for(app)

    page = client.get("/privacy")
    csp = page.headers["content-security-policy"]
    html = page.text

    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert client.get("/privacy/privacy.css").headers["content-security-policy"] == csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in html.lower()
    assert "<style" not in html.lower()
    assert not re.search(r"\sstyle\s*=", html, flags=re.IGNORECASE)
    assert not re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE)
    assert "javascript:" not in html.lower()
    assert re.findall(r"<link\b[^>]*>", html) == [
        '<link rel="stylesheet" href="/privacy/privacy.css">'
    ]
    assert "@import" not in client.get("/privacy/privacy.css").text


def test_contact_email_and_retention_days_come_from_settings():
    page = client_for(
        make_app(
            settings=SimpleNamespace(
                privacy_contact_email="privacy@example.test",
                trash_retention_days=7,
            )
        )
    ).get("/privacy").text

    assert 'href="mailto:privacy@example.test"' in page
    assert DEFAULT_PRIVACY_CONTACT_EMAIL not in page
    assert "7 天后条目会被永久清除" in page
    assert "after 7 days it is permanently purged" in page
    assert "30 天" not in page
    assert "30 days" not in page


def test_defaults_apply_without_settings_and_every_placeholder_is_filled(app):
    page = client_for(app).get("/privacy").text

    assert f'href="mailto:{DEFAULT_PRIVACY_CONTACT_EMAIL}"' in page
    assert "30 天后条目会被永久清除" in page
    assert "{{" not in page and "}}" not in page
    assert page.count(PRIVACY_EFFECTIVE_DATE) >= 3


def test_real_settings_drive_the_page(monkeypatch):
    monkeypatch.setenv("PRIVACY_CONTACT_EMAIL", "ops@example.test")
    monkeypatch.setenv("TRASH_RETENTION_DAYS", "14")
    settings = Settings()

    page = client_for(make_app(settings=settings)).get("/privacy").text

    assert settings.privacy_contact_email == "ops@example.test"
    assert "mailto:ops@example.test" in page
    assert "14 天后条目会被永久清除" in page


def test_contact_email_defaults_and_is_validated(monkeypatch):
    monkeypatch.delenv("PRIVACY_CONTACT_EMAIL", raising=False)
    assert Settings().privacy_contact_email == DEFAULT_PRIVACY_CONTACT_EMAIL

    for bad in ("", "   ", "not-an-email", "a@b", 'a"@x.test', "a b@x.test"):
        monkeypatch.setenv("PRIVACY_CONTACT_EMAIL", bad)
        with pytest.raises(ValueError, match="PRIVACY_CONTACT_EMAIL"):
            Settings()

    monkeypatch.delenv("PRIVACY_CONTACT_EMAIL")
    with pytest.raises(ValueError, match="PRIVACY_CONTACT_EMAIL"):
        replace(Settings(), privacy_contact_email="<x>@y.test")


def test_substituted_values_are_html_escaped():
    page = render_privacy_page(
        contact_email='a"><script>alert(1)</script>@x.test',
        retention_days=5,
        service_origin="https://<i>host</i>",
        effective_date="<b>2026</b>",
    )

    assert "<script" not in page.lower()
    assert "<b>2026</b>" not in page
    assert "&lt;b&gt;2026&lt;/b&gt;" in page
    assert 'mailto:a&quot;&gt;&lt;script&gt;' in page
    assert "<i>host</i>" not in page
    assert "https://&lt;i&gt;host&lt;/i&gt;" in page
    assert "{{" not in page


def test_template_names_no_fixed_service_domain():
    template = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "privacy" / "index.html"
    ).read_text(encoding="utf-8")

    assert "deequoique" not in template
    assert template.count("{{service_origin}}") == 2


def test_service_origin_is_rendered_from_the_request(app):
    client = TestClient(app, base_url="https://stashseek.deequoique.tech")

    page = client.get("/privacy").text

    assert "https://stashseek.deequoique.tech（下称“服务”）" in article(page, "zh")
    assert "https://stashseek.deequoique.tech (“the service”)" in article(page, "en")
    # An explicit Host header wins, the scheme follows the request, and no
    # trailing slash is rendered.
    other = client_for(app).get("/privacy", headers={"Host": "old.example.test"}).text
    assert "https://old.example.test（下称“服务”）" in other
    local = TestClient(app, base_url="http://127.0.0.1:8000").get("/privacy").text
    assert "http://127.0.0.1:8000（下称“服务”）" in local
    assert "stashseek.deequoique.tech" not in other


def test_hostile_host_header_is_escaped(app):
    hostile = client_for(app).get(
        "/privacy", headers={"Host": 'evil.test"><script>alert(1)</script>'}
    )

    assert hostile.status_code == 200
    assert "<script" not in hostile.text.lower()
    assert "https://evil.test&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in hostile.text


def test_both_languages_and_the_effective_date_are_present(app):
    page = client_for(app).get("/privacy").text

    assert 'href="#zh"' in page and 'href="#en"' in page
    assert 'id="zh" lang="zh-CN"' in page
    assert 'id="en" lang="en"' in page
    assert f'<time datetime="{PRIVACY_EFFECTIVE_DATE}">' in page
    assert f"本政策自 {PRIVACY_EFFECTIVE_DATE} 起生效" in page
    assert f"effective from {PRIVACY_EFFECTIVE_DATE}" in page


def test_languages_have_the_same_ten_sections(app):
    page = client_for(app).get("/privacy").text

    zh = headings(article(page, "zh"))
    en = headings(article(page, "en"))

    assert len(zh) == len(en) == 10
    assert [h.split(".")[0] for h in zh] == [h.split(".")[0] for h in en]
    assert zh[0] == "1. 我们是谁，如何联系我们"
    assert zh[5] == "6. 保存期限与删除"
    assert "Limited Use" in zh[6]
    assert "Limited Use" in en[6]


PINNED_CLAIMS = {
    "zh": [
        # What the extension reads, and never reads or uploads.
        "只在 YouTube 和哔哩哔哩（Bilibili）的视频页面",
        "不会读取你的 Cookie、密码或其他登录凭据并发送给我们",
        "也不会读取或上传音频、视频文件",
        "带签名的字幕地址只在页面内使用",
        # Page Q&A keeps no captions.
        "不保存字幕",
        f"不超过 {MAX_EXCERPT_CHARS} 个字符的被引用片段",
        "不写入数据库、对象存储或日志",
        # No ads, no sale.
        "我们不投放广告，不出售你的数据",
        "我们不出售用户数据",
        # Limited Use.
        "Chrome 网上应用店用户数据政策（Chrome Web Store User Data Policy）",
        "有限使用（Limited Use）要求",
        # Retention and logs.
        "不含你的提问、回答或字幕文本",
        "不面向 13 岁以下的儿童",
    ],
    "en": [
        "only on YouTube and Bilibili video pages",
        "does not read your cookies, passwords or other sign-in credentials in order to send them to us",
        "does not read or upload audio or video files",
        "Signed caption addresses stay inside the page",
        "Captions are not stored.",
        f"cited excerpts of at most {MAX_EXCERPT_CHARS} characters",
        "not written to the database, object storage or logs",
        "We show no ads, do not sell your data",
        "We do not sell user data",
        "Chrome Web Store User Data Policy, including the Limited Use requirements",
        "no question, answer or caption text",
        "not directed to children under 13",
    ],
}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_code_guaranteed_claims_are_pinned_in_both_languages(app, language):
    section = article(client_for(app).get("/privacy").text, language)

    for claim in PINNED_CLAIMS[language]:
        assert claim in section, claim


def test_page_qa_excerpt_limit_matches_the_code():
    assert MAX_EXCERPT_CHARS == 180


@pytest.mark.parametrize("language", ["zh", "en"])
def test_processors_are_named_by_category_only(app, language):
    section = article(client_for(app).get("/privacy").text, language).lower()

    for name in (
        "deepseek", "zhipu", "bigmodel", "openai", "dashscope", "alibaba",
        "aliyun", "fun-asr", "whisper", "neon", "ovh", "resend", "smtp",
        "minio", "postgres", "redis", "caddy",
    ):
        assert name not in section, name


def test_page_does_not_mention_ntu_pilot_sources(app):
    page = client_for(app).get("/privacy").text.lower()

    for word in ("ntulearn", "kaltura", "ntu.edu"):
        assert word not in page
