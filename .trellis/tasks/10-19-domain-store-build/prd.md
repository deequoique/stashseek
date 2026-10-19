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

- New domain `stashseek.deequoique.tech`.
- **The store build drops the NTULearn/Kaltura host permissions** (2026-10-19).
  The zip/production build is unchanged unless decided otherwise.

## Requirements (draft)

- **D1 Caddy.**
  - Serve `stashseek.deequoique.tech`.
  - During the transition, `notebookai.deequoique.tech` keeps proxying `/api/*`,
    `/mcp*` and extension traffic, and 308-redirects page navigations to the
    new host. This is pending user confirmation.
- **D2.** `WEB_PUBLIC_ORIGIN` (main) and all docs, tests and specs move to the
  new domain.
- **D3 Extension 0.2.1.**
  - The production and store targets use the new API origin and host
    permission.
  - A new `store` build target:
    - no `key`;
    - no NTULearn/Kaltura hosts;
    - same code, its own audit;
    - a packaging script that produces the store zip.
  - The manifest gains `icons` 16/32/48/128, generated from the same artwork as
    the store icon.
- **D4 Allowed origins.** `BROWSER_COMPANION_ALLOWED_ORIGINS` adds
  `chrome-extension://jeemllapbelpglmpnhjfamcnoibpeail` and keeps the zip ID.
- **D5 Web download entry.** On `main` it serves the 0.2.1 production zip.
- **D6 Release.** It lands on `dev` first, then reaches `main` through a
  release branch. The user approves the deploy and then updates
  `ASR_PUBLIC_BASE_URL` on the host.

## Acceptance criteria (draft)

- [ ] `https://stashseek.deequoique.tech/api/v1/capabilities` returns 200 with a
      valid certificate. The old domain keeps the API and MCP working, and
      pages redirect.
- [ ] Web login works on the new domain.
- [ ] The store zip has no `key` and no NTU hosts, includes icons, and passes
      its audit. A store-installed extension (ID
      `jeemllapbelpglmpnhjfamcnoibpeail`) can log in, ask, and collect against
      production.
- [ ] A 0.2.0 zip install still works through the old domain during the
      transition.
