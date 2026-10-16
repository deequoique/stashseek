# Provider Registration Research

Checked on 2026-10-15. The user has no application registrations for Microsoft, WeChat, or QQ yet. No accounts were registered, no secrets were requested, and no provider configuration was changed.

## Microsoft

Primary sources read:

- [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
- [OpenID Connect](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
- [Authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)

The registration supports personal Microsoft accounts and/or organizational accounts depending on the supported-account-types setting. To support both, the application registration must allow organizational directories plus personal Microsoft accounts; an organization may still impose its own consent/access policy.

The standard server web flow is authorization code with OIDC and PKCE. Registration must specify the intended Web redirect URI, client/application ID, and a server-side credential. Use a maintained identity library for validation rather than implementing JWT cryptography manually. Secret/certificate values are private deployment configuration, not frontend build variables.

The exact callback paths and corresponding environment variable names are deferred to the approved technical design. No callback URL has been registered or promised to work locally.

## QQ

Primary sources read:

- [Website application overview](https://wiki.connect.qq.com/网站应用接入流程)
- [Preparation](https://wiki.connect.qq.com/准备工作_oauth2-0)
- [Create an application](https://wiki.connect.qq.com/__trashed-2)
- [Authorization code exchange](https://wiki.connect.qq.com/使用authorization_code获取access_token)

QQ Connect requires an application registration with AppID/AppKey, website/domain/callback information, and review. The current creation guide includes filing-information instructions and a separate path for international-domain review; eligibility must be checked for the intended deployment rather than assumed.

The website flow uses server-side authorization-code exchange. The documented authorization request requires state and says to bind/check it against the user; the callback must belong to the registered domain, and token exchange must use the same redirect URI. QQ recommends limiting requested permissions. Its documented API uses credential-bearing requests, so HTTP/proxy error logs must not expose URLs or tokens.

The official overview describes optional post-login account linking. StashSeek's first-login linking experience remains a product decision, because creating a separate populated library raises tenant-merge requirements.

## WeChat

The intended flow is WeChat website login for this browser app, separate from the existing Bot channel. The official protocol and public review requirements were subsequently fetched through an approved read-only HTTP path after the web reader failed. See `wechat-official-verification.md` for the current primary-source evidence.

Official documentation locations attempted:

- [WeChat website login guide](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html)
- [WeChat Open Platform](https://open.weixin.qq.com/)

The verified flow uses the website `qrconnect` authorization endpoint and `snsapi_login`, AppID/AppSecret server code exchange, and app-scoped OpenID/userinfo. State is mandatory in StashSeek's flow even though optional in the provider table. Rejected authorization does not redirect; codes expire after ten minutes and exchange successfully only once. Separate API signing/encryption is not established as mandatory for this documented OAuth flow. Do not assume PKCE/OIDC support from generic adapters.

An approved website application, login permission, AppID/AppSecret and reviewed callback domain are required for real use. Personal eligibility, fees/timing and console-specific security policy remain unverified and must be confirmed during actual registration; no authenticated platform console was accessed. See the detailed evidence for public review-rule limitations rather than substituting third-party descriptions.

## Additional Implementation Sources

- [MSAL Python code-flow guidance](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)
- [Microsoft ID-token claims](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference)
- [MSAL Python public API](https://msal-python.readthedocs.io/en/stable/)
- [JOSE JWT verification and claim validation](https://jose.authlib.org/en/guide/jwt/)
- [QQ OpenID response](https://wiki.connect.qq.com/获取用户openid_oauth2-0)

These support maintained code-flow/JOSE integration, explicit claim validation, stable subject identity instead of mutable email/name, and QQ application-scoped OpenID checks. Versions will be pinned in implementation; no dependency was installed during planning.

## Email

Use the existing mailbox-independent verification-code mechanism. There is no requirement to read email or integrate individual Gmail/Outlook/QQ Mail/163 OAuth APIs. A valid mailbox address is distinct from Microsoft/QQ provider identity ownership. A real email delivery configuration and successful delivery smoke are required before calling this ready for production.

## Rollout Evidence

Fake-provider tests can verify application logic before registrations exist. They cannot establish that Microsoft, QQ, or WeChat has approved an application, accepted the callback domain, or enabled real users. Record each provider's test/live status separately; leave unconfigured providers unavailable in the login UI.
