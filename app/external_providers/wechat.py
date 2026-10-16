"""WeChat website login, distinct from messaging/Bot identities."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.external_providers.base import (
    ExternalPrincipal, ProviderAuthorization, ProviderError, configuration_digest,
)
from app.external_providers.http import ProviderHTTP


class WeChatWebsiteProvider:
    name = "wechat_website"
    NAMESPACE = "https://open.weixin.qq.com"

    def __init__(self, app_id: str, app_secret: str, *, http: ProviderHTTP | None = None) -> None:
        if not app_id or not app_secret:
            raise ValueError("WeChat website configuration is incomplete")
        self.application_id = app_id
        self.configuration_id = configuration_digest(self.name, app_id, app_secret)
        self._secret = app_secret
        self._http = http or ProviderHTTP(frozenset({"api.weixin.qq.com"}))

    def begin(self, *, redirect_uri: str, state: str) -> ProviderAuthorization:
        query = urlencode({
            "appid": self.application_id, "redirect_uri": redirect_uri,
            "response_type": "code", "scope": "snsapi_login", "state": state,
        })
        return ProviderAuthorization(
            f"{self.NAMESPACE}/connect/qrconnect?{query}#wechat_redirect", {}
        )

    def complete(
        self, *, redirect_uri: str, code: str, material: dict[str, Any]
    ) -> ExternalPrincipal:
        try:
            token = self._http.json("https://api.weixin.qq.com/sns/oauth2/access_token", params={
                "appid": self.application_id, "secret": self._secret,
                "code": code, "grant_type": "authorization_code",
            })
            access, subject = token.get("access_token"), token.get("openid")
            if (
                token.get("errcode", 0) != 0
                or not isinstance(access, str) or not 1 <= len(access) <= 8192
                or not isinstance(subject, str) or not 1 <= len(subject) <= 128
                or "snsapi_login" not in str(token.get("scope", "")).split(",")
            ):
                raise ProviderError()
            identity = self._http.json("https://api.weixin.qq.com/sns/userinfo", params={
                "access_token": access, "openid": subject,
            })
            if identity.get("errcode", 0) != 0 or identity.get("openid") != subject:
                raise ProviderError()
            return ExternalPrincipal(self.NAMESPACE, subject)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError() from None
