# WeChat website login: official verification

Verified on 2026-10-15. Planning research only; no application registration,
authorization, product-code change, or task activation was performed.

## Evidence and access method

The web reader could not open the WeChat documentation. Read-only HTTP requests
outside the network sandbox subsequently returned HTTP 200 and readable official
HTML for the sources below. These findings use that official content, not search
snippets or third-party mirrors.

| Source | Official URL |
| --- | --- |
| Website login development guide | <https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html> |
| Authorized interfaces and UnionID | <https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Authorized_Interface_Calling_UnionID.html> |
| Application review requirements | <https://developers.weixin.qq.com/doc/oplatform/Website_App/operation.html> |
| API security configuration | <https://developers.weixin.qq.com/doc/oplatform/Website_App/guide/signature_verify.html> |
| Public website application introduction | <https://open.weixin.qq.com/cgi-bin/frame?t=home/web_tmpl&lang=zh_CN> |

## Verified OAuth endpoints and behavior

The [website login guide](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html)
documents a server-side authorization-code flow with AppID and AppSecret:

| Operation | Method and endpoint | Documented parameters |
| --- | --- | --- |
| Authorization | Browser GET `https://open.weixin.qq.com/connect/qrconnect` | `appid`, URL-encoded `redirect_uri`, `response_type=code`, `scope=snsapi_login`, `state`; optional `lang`. The example has fragment `#wechat_redirect`. |
| Code exchange | Server GET `https://api.weixin.qq.com/sns/oauth2/access_token` | `appid`, `secret`, `code`, `grant_type=authorization_code` |
| User information | Server GET `https://api.weixin.qq.com/sns/userinfo` | `access_token`, `openid`; optional `lang` |
| Token validity check | Server GET `https://api.weixin.qq.com/sns/auth` | `access_token`, `openid` |
| Token refresh | Server GET `https://api.weixin.qq.com/sns/oauth2/refresh_token` | `appid`, `grant_type=refresh_token`, `refresh_token` |

The latter four interfaces and their GET methods are specified in the
[authorized-interface guide](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Authorized_Interface_Calling_UnionID.html).
These are documented contracts, not live credentialed API tests. Secrets and
tokens in these server requests must not appear in browser URLs or ordinary logs.

The authorization guide confirms:

- The callback domain must match the authorized domain submitted for review.
- Successful authorization returns `code` and `state` to the callback.
- A code expires after ten minutes and can successfully obtain a token once.
- `state` is optional in the provider parameter table, but recommended for CSRF
  protection through a random value checked against the session. StashSeek should
  make its own browser-bound, expiring, single-use state mandatory.
- The current guide says rejection does **not** redirect. Do not assume a denial
  callback will arrive; permit the user to return/retry and expire the transaction.
- Current desktop clients can offer a local WeChat confirmation flow before QR
  login. Do not describe website login as necessarily requiring a QR scan.

## Security capabilities: confirmed boundaries

The [API security page](https://developers.weixin.qq.com/doc/oplatform/Website_App/guide/signature_verify.html)
describes configuring an application public/private key and downloading a platform
certificate, plus optional AES256/SM4 API-content encryption. Configuration is under
the website application's development settings, API Security. Its key-generation
instructions are conditional on the developer needing signing or encryption.

**Mandatory status:** this page does not establish that these keys/signatures are
mandatory for the website OAuth flow above. The verified OAuth parameter tables
still specify AppSecret code exchange and do not list those signing fields. Do
not add a universal OAuth-signing prerequisite based solely on this page. Exact
endpoint applicability and any console-specific mandatory policy remain deferred
until an application is registered and its enabled modes can be inspected.

Neither verified OAuth guide documents `code_challenge`, `code_verifier`, PKCE,
`id_token`, OIDC discovery, or JWKS. This is **no verified support evidence**, not
proof that the provider rejects every such extension. Do not make PKCE/OIDC a
shared mandatory adapter assumption for WeChat. Do not treat the separate API
signing configuration as PKCE or as a documented OAuth redirect-signature scheme.

## Identity and first-login implications

The [authorized-interface guide](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Authorized_Interface_Calling_UnionID.html)
returns `openid` in the token and user-information contracts. It defines UnionID
as the identifier shared by the same user across applications under one WeChat
Open Platform account. UnionID is not documented as globally interchangeable
between unrelated Open Platform accounts. Its user-information contract does not
document an email address.

For this project, use an explicitly namespaced website-provider identity, including
the configured AppID and OpenID; treat UnionID as additional provider evidence.
Do not infer an existing Bot/channel identity or library owner from matching text.
The user's chosen first-login email-code verification and explicit binding remain
necessary; WeChat profile fields do not prove control of an existing email account.

## Registration prerequisites and remaining uncertainty

The [login guide](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html)
requires a developer account, an approved website application, AppID/AppSecret,
and approved WeChat-login permission before integration. An application being
created is not itself evidence that `snsapi_login` is enabled.

The [review requirements](https://developers.weixin.qq.com/doc/oplatform/Website_App/operation.html)
require application/website information consistent with the submitted identity,
a reachable substantive official website, and consistent website information-form
and filing details. A login-only page or a site consisting of a few lines/images
is insufficient. The page distinguishes personal and non-personal service
categories and says some categories require developer qualification certification.
It also contains mobile-app-specific provisions, which must not be indiscriminately
applied to a website application.

**Personal eligibility remains unverified.** These public rules acknowledge
personal categories but do not conclusively establish that a personal account can
obtain the website WeChat-login permission required here. Do not promise either
eligibility or ineligibility. Application ownership type, category eligibility,
current certification charges/timing, exact required registration materials,
and the chosen domain's filing requirements must be confirmed in the actual
official application/qualification process. Public registration-route requests
did not expose readable eligibility details, and no authenticated console was used.

The user has no registered WeChat application yet. Real-provider acceptance is
therefore pending application/permission approval and private deployment
configuration. Fake-provider tests alone cannot establish live WeChat readiness.
