# Domain move and store build 0.2.1

Parent: `10-19-chrome-web-store-listing`.

## Goal

Serve the product at `https://stashseek.deequoique.tech` and ship extension
0.2.1 for the Chrome Web Store, with the store build pointing at the new
domain. Internal users of the old domain and of the 0.2.0 zip keep working
during a transition.

## Facts (2026-10-19)

- **DNS.** `deequoique.tech` is hosted on DNSPod. `stashseek` has an A record
  to `51.79.159.110`, the OVH production host, added by the user.
  - The `notebookai` A record was removed at the same time, so public
    resolvers no longer resolve the production domain. **The user must re-add
    it.** This is an outage until they do.
- **Server config.**
  - Caddy serves only `notebookai.deequoique.tech`
    (`deploy/caddy/notebook-agent.caddy`).
  - On `main`, `WEB_PUBLIC_ORIGIN=https://notebookai.deequoique.tech` is set in
    `deploy/systemd/notebook-agent.service`.
  - `ASR_PUBLIC_BASE_URL` in the host env file probably points at the old
    domain. It is not in the repo; the user edits it on the server.
- **Extension.**
  - The production build target's API origin and host permission are
    `https://notebookai.deequoique.tech` (`extension/scripts/build-targets.mjs`,
    `manifest.json`).
  - The store item ID is `jeemllapbelpglmpnhjfamcnoibpeail`, and the store
    rejects a manifest that contains `key`.
  - The manifest has no `icons`. The 128 px store icon exists in the
    scratchpad and on the user's Desktop.
- About 24 repo files mention the old domain (docs, README, tests, specs, CI).

## Decisions (user)

- **Direct cutover; no old domain** (user, 2026-10-19). `notebookai` is gone,
  so 0.2.0 installs and MCP clients on the old URL must move to the new one.
- **Production build and zip also drop NTULearn/Kaltura** (user, 2026-10-19).
  There is one production build. The store zip is that build with `key`
  removed.

## Done on the production host (2026-10-19, main session via SSH; backups `*.bak-<ts>`)

- `/etc/caddy/Caddyfile` site `notebookai…` became `stashseek.deequoique.tech`.
  The Let's Encrypt certificate was issued.
- `/etc/notebook-agent/notebook-agent.env`:
  - `ASR_PUBLIC_BASE_URL` and `WEB_TRUSTED_PROXY_HOSTS` now use the new domain.
  - `BROWSER_COMPANION_ALLOWED_ORIGINS` now also lists
    `chrome-extension://jeemllapbelpglmpnhjfamcnoibpeail`.
  - The env file overrides the unit's `Environment=` lines.
- `/etc/systemd/system/notebook-agent.service`: `WEB_PUBLIC_ORIGIN` is now
  `https://stashseek.deequoique.tech` (plus the same allowed-origins edit).
- Verified: capabilities 200, `/login` 200, web auth Origin check accepts the
  new origin and rejects the old one, the store-ID preflight returns 204, and
  a random ID returns 403.

## Previous decisions

- New domain `stashseek.deequoique.tech`.
- **The store build drops the NTULearn/Kaltura host permissions** (2026-10-19).
  The zip/production build is unchanged unless decided otherwise.

## Requirements (draft)

- **D1 Caddy reference.** Update `deploy/caddy/notebook-agent.caddy` to match the host:
  a single `stashseek.deequoique.tech` site, with no old-domain block.
- **D2.** `WEB_PUBLIC_ORIGIN` (main) and all docs, tests and specs move to the
  new domain.
- **D3 Extension 0.2.1.**
  - The production build uses the new API origin and host permission. There is
    one production build, with no NTULearn/Kaltura hosts or adapter.
  - `extension/scripts/package-release.mjs` (`pnpm package:release`) builds and
    audits it, then writes two deterministic zips to `extension/release/`: the
    production zip as is (with `key`) and the store zip (identical except the
    manifest has no `key`).
  - The manifest gains `icons` 16/32/48/128 and `action.default_icon`, from the
    same artwork as the store icon (`extension/public/icons/`).
- **D4 Allowed origins.** `BROWSER_COMPANION_ALLOWED_ORIGINS` adds
  `chrome-extension://jeemllapbelpglmpnhjfamcnoibpeail` and keeps the zip ID.
- **D5 Web download entry.** On `main` it serves the 0.2.1 production zip.
- **D6 Release.** It lands on `dev` first, then reaches `main` through a
  release branch. The user approves the deploy and then updates
  `ASR_PUBLIC_BASE_URL` on the host.

## Acceptance criteria (draft)

- [ ] `https://stashseek.deequoique.tech/api/v1/capabilities` returns 200 with a
      valid certificate. There is no old domain (direct cutover).
- [ ] Web login works on the new domain.
- [ ] The store zip has no `key` and no NTU hosts, includes icons, and passes
      its audit. A store-installed extension (ID
      `jeemllapbelpglmpnhjfamcnoibpeail`) can log in, ask, and collect against
      production.
- [ ] A 0.2.0 zip install must move to 0.2.1 (its API origin is the removed
      domain); the zip ID stays allowed on the server.
