# Configure Microsoft, WeChat and QQ website login

The website supports email verification codes and optional Microsoft, WeChat
website, and QQ authorization. On first provider use, the user verifies a mailbox
and chooses **验证并绑定** to attach the provider to that mailbox's existing or new
library. Later provider sign-ins enter the same library directly. A provider email
claim, display name or UnionID does not select or merge a library.

Provider adapters and synthetic tests are code support, not proof that this
deployment has approved applications or can complete live login. The repository
ships no registered applications or real credentials; the deployment owner must
complete the registration and real-domain acceptance below before enabling them.

## Deployment prerequisites

Use the full canonical browser backend with a migrated PostgreSQL database,
`WEB_AUTH_ENABLED=true`, a high-entropy `WEB_AUTH_SECRET` of at least 32 characters,
`WEB_PUBLIC_ORIGIN` set to the exact HTTPS origin, `WEB_COOKIE_SECURE=true`,
and a working SMTP/Resend sender. Keep secrets in the server's private environment
file and out of frontend variables, source control, chat and diagnostic output.

The local in-memory demo does not perform real login, send mail or ingest videos.
Use a registered HTTPS staging/production domain for real-provider acceptance.
Apply the additive migration through the existing deployment migration admission
before enabling providers. It retains existing accounts, emails, videos and history.

Each provider has its own enable flag. Leave it false until its application,
permissions, domain and credentials are ready. An enabled provider with incomplete
configuration fails validation; a disabled provider has no usable login button.

## Microsoft

Register a server **Web** application in the [Microsoft Entra admin center](https://entra.microsoft.com/).
For personal accounts and organizational accounts, select the audience allowing
both organizational directories and personal Microsoft accounts. Organization
administrators may still restrict consent. See the [official registration guide](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app).

Register this exact Web redirect URI, replacing the example origin:

```text
https://app.example.com/api/v1/auth/external/microsoft/callback
```

Set the private server configuration:

```dotenv
WEB_MICROSOFT_ENABLED=true
WEB_MICROSOFT_CLIENT_ID=<application-client-id>
WEB_MICROSOFT_CLIENT_SECRET=<private-client-secret-value>
WEB_MICROSOFT_TENANT=common
```

`common` permits both audiences; `organizations`, `consumers`, or a tenant UUID
can deliberately restrict the deployment. No arbitrary authority URL is accepted.
The adapter uses the [MSAL code-flow helpers](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)
with PKCE/nonce and independently validates signed ID tokens. No Graph/mailbox
permission or refresh-token feature is required. Credential/client-ID changes
invalidate unfinished authorization flows; they do not silently relink accounts.

## WeChat website application

Create and obtain approval for a website application and its WeChat-login
permission through [WeChat Open Platform](https://open.weixin.qq.com/).
Use the [official website-login guide](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html)
and [review requirements](https://developers.weixin.qq.com/doc/oplatform/Website_App/operation.html)
for the submitted owner/category/domain. Personal-owner eligibility, certification
fees and approval times must be checked in the actual registration process.

Authorize the HTTPS callback domain and use:

```text
https://app.example.com/api/v1/auth/external/wechat_website/callback
```

```dotenv
WEB_WECHAT_WEBSITE_ENABLED=true
WEB_WECHAT_WEBSITE_APP_ID=<website-app-id>
WEB_WECHAT_WEBSITE_APP_SECRET=<private-app-secret>
```

This uses the documented AppSecret website `snsapi_login` flow and app-scoped
OpenID. It is separate from WeChat Bot login and Official Account authorization.
The provider can show local confirmation or QR authorization. Rejecting consent
may leave the browser at WeChat without a callback; return to the app and restart.

The separate API signing/encryption documentation does not establish a universal
requirement for this OAuth flow. If the registered application's console requires
a different protocol mode, keep this provider disabled and review that mode
before enabling; do not assume PKCE/OIDC or a generic adapter implements it.

## QQ Connect website application

Create and obtain review for a website application at [QQ Connect](https://connect.qq.com/).
Follow its [application setup guide](https://wiki.connect.qq.com/__trashed-2),
including website/domain/filing information and any international-domain review
route applicable to the deployment. Configure the exact callback:

```text
https://app.example.com/api/v1/auth/external/qq/callback
```

```dotenv
WEB_QQ_ENABLED=true
WEB_QQ_APP_ID=<application-id>
WEB_QQ_APP_KEY=<private-app-key>
```

The server exchanges codes and checks that the returned OpenID belongs to the
configured AppID. No QQ sharing, contacts or mailbox synchronization is requested.
QQ's documented token requests contain credentials in query parameters: never
log outbound URLs, provider bodies or exception messages.

See the [configuration reference](../reference/configuration.md#可选外部登录配置)
for defaults and restart requirements, and the [Web API reference](../reference/web-api.md#可选外部网站登录)
for the shared start/callback/binding routes. No login provider configuration belongs
in frontend `VITE_*` variables.

## Real acceptance and recovery

For each enabled provider, verify on the registered domain:

1. First authorization enters the email step without creating an independent
   empty library. Use the email that already owns the intended library.
2. A wrong code remains a form error; resend/cancel/expiry can recover. Only
   **验证并绑定** completes the association and enters the existing library.
3. Sign out, then use the same provider again. It should enter the same videos
   and search history without another email code.
4. An already-authenticated browser or a login completed in another tab must not
   be silently replaced or used as an implicit binding target.
5. Disabled/unavailable providers and failed callbacks show bounded retry/email
   guidance; logs, browser storage and frontend state contain no provider tokens.

Record only pass/fail outcomes. Synthetic-provider tests establish code behavior,
not platform approval, real mail delivery or real-provider readiness. An uncertain
binding response is reconciled using session/flow status; if the binding committed
but its session cookie was lost, start provider sign-in again rather than attempting
to move an account. Use normal email login as the independent fallback.

Provider sessions retain provenance and use database validation rather than Redis
session caching. Existing explicit Telegram linking preserves provider anchors;
direct CLI reassignment of an externally bound email identity is rejected. This
release does not add self-service unlink, email replacement or silent merging.

For an application rollback, disable providers and revoke their issued sessions
before returning to older code that cannot understand provenance. Retain additive
tables/mappings; do not automatically downgrade or delete production auth data.
Reverse-proxy access logs must omit/redact OAuth callback query strings even
though the canonical application also removes them from its shared request scope.
