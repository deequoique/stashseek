# QQ Adapter Design

Follow parent shared flow/identity/session/binding/rollout contracts. Use documented QQ Connect server-side authorization code, strict browser-bound state and callback `/api/v1/auth/external/qq/callback`.

Fixed endpoints: `https://graph.qq.com/oauth2.0/authorize`, `https://graph.qq.com/oauth2.0/token`, `https://graph.qq.com/oauth2.0/me`. Obtain OpenID with the exchanged token and verify returned client/application against AppID. Identity scope includes AppID/OpenID; no UnionID/email/nickname automatic association.

Prefer documented JSON modes. If a documented form/JSONP envelope is required, parse that exact bounded grammar without evaluating JavaScript. Require successful status/necessary fields. Never log credential-bearing URLs or return provider bodies. Use the documented supported exchange method with verified TLS rather than inventing generic POST/PKCE requirements.

Request minimum login permissions; discard tokens after verification. The guide distinguishes standard/international-domain review based on official instructions without promising eligibility or approval dates.
