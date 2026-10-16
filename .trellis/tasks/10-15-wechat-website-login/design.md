# WeChat Website Adapter Design

Inherit parent browser-flow/email-binding/session/rollout contracts. Use `wechat_website` and callback `/api/v1/auth/external/wechat_website/callback`, distinct from messaging-channel `wechat`.

Use verified authorization `https://open.weixin.qq.com/connect/qrconnect` with `scope=snsapi_login`, GET code exchange `https://api.weixin.qq.com/sns/oauth2/access_token`, and GET identity `https://api.weixin.qq.com/sns/userinfo`. Require browser-bound state, configured AppID/AppSecret exchange and matching OpenID across token/userinfo. Keep tokens server-side during verification and discard them. UnionID is not a binding/merge key. Website authorization can offer local confirmation or QR login; do not require a particular provider-side presentation.

Before implementation, review parent current official-source research including cancellation and API security configuration. Support the documented AppSecret OAuth flow initially. The public docs do not establish that separate API signing/encryption is mandatory for this flow; neither do they document OAuth PKCE/OIDC fields. If the registered application's console requires a different mode, fail explicit validation and review that mode before enablement rather than silently falling back.

The UI uses expiry/restart when the provider does not redirect on refusal; no indefinite polling. The guide distinguishes verified website/domain/credential setup from unresolved eligibility/review details. Enable only after verified protocol checks, completed adapter and registration/configuration.
