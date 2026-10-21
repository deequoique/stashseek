"""Compose only explicitly enabled and completely configured login providers."""

from __future__ import annotations

from app.external_providers.base import ExternalProvider
from app.external_providers.http import ProviderHTTP
from app.tls import configure_trusted_ca


def build_external_providers(settings) -> dict[str, ExternalProvider]:
    enabled = [
        name for name in ("microsoft", "wechat_website", "qq")
        if getattr(settings, f"web_{name}_enabled", False)
    ]
    if not enabled:
        return {}
    if not getattr(settings, "web_auth_enabled", False):
        raise ValueError("External login requires Web email authentication")
    trusted = configure_trusted_ca(getattr(settings, "tls_ca_bundle", None))
    result: dict[str, ExternalProvider] = {}
    if "microsoft" in enabled:
        from app.external_providers.microsoft import MicrosoftProvider
        result["microsoft"] = MicrosoftProvider(
            settings.web_microsoft_client_id or "", settings.web_microsoft_client_secret or "",
            tenant=settings.web_microsoft_tenant,
            http=ProviderHTTP(frozenset({"login.microsoftonline.com"}), ssl_context=trusted.ssl_context),
        )
    if "wechat_website" in enabled:
        from app.external_providers.wechat import WeChatWebsiteProvider
        result["wechat_website"] = WeChatWebsiteProvider(
            settings.web_wechat_website_app_id or "", settings.web_wechat_website_app_secret or "",
            http=ProviderHTTP(frozenset({"api.weixin.qq.com"}), ssl_context=trusted.ssl_context),
        )
    if "qq" in enabled:
        from app.external_providers.qq import QQProvider
        result["qq"] = QQProvider(
            settings.web_qq_app_id or "", settings.web_qq_app_key or "",
            http=ProviderHTTP(frozenset({"graph.qq.com"}), ssl_context=trusted.ssl_context),
        )
    return result
