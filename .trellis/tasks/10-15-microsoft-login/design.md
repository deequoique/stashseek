# Microsoft Adapter Design

Inherit parent shared flow, identity/session, email-binding and rollout contracts. Add the adapter/config and fixtures, not another authentication service.

Use documented MSAL Python confidential-client authorization-code helpers with S256 PKCE, state, nonce, a fixed HTTPS callback and top-level GET response mode. Exclude unnecessary offline access/Graph scopes. Keep protected short-lived SDK flow server-side and discard acquired tokens after identity validation.

Explicitly verify signature/allowed algorithm with a maintained JOSE library where not guaranteed by the SDK, using trusted Microsoft metadata keys. Validate audience, nonce, times and exact tenant issuer under documented multi-tenant rules. Untrusted token headers/issuers cannot trigger arbitrary HTTP. Bound JWKS cache and refresh once on unknown key before safe failure.

Identity key: `(microsoft, client scope, verified issuer, sub)`. No email/UPN/name/home-tenant correlation for ownership. Registration must support personal and organizational audiences; organization consent policy still applies.

Callback: `/api/v1/auth/external/microsoft/callback`. Configuration stays server-side. Pin dependencies/API evidence before implementation review; registration is not performed by this plan.
