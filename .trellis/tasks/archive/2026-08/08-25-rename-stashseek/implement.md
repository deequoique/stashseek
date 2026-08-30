# StashSeek Chat Rename Implementation Plan

## 1. Protect the Worktree

- Record `git status --short`, current branch, remote, and the tracked/untracked
  old-name file inventory.
- Do not remove or overwrite unrelated untracked tasks, package stores, promo
  work, or extension artifacts.
- Set the Trellis task branch metadata to the existing `dev` branch.

## 2. Add Canonical Compatibility Entry Points

- Add `scripts/stashseek` as the canonical launcher.
- Convert `scripts/notebook-agent` to a thin compatibility wrapper and preserve
  executable mode, argument forwarding, signals, and exit status.
- Change CLI display metadata and Python distribution metadata to StashSeek.
- Add canonical `STASHSEEK_*` configuration keys with deterministic
  `NOTEBOOK_AGENT_*` fallback; rename internal setting fields and update callers.
- Add `ask_stashseek` to the MCP surface and retain
  `ask_notebook_agent` as an alias to the same implementation.

Focused validation:

```bash
sh -n scripts/stashseek scripts/notebook-agent
python -m pytest -q tests/test_deployment_cli.py tests/test_web_cli.py \
  tests/test_mcp_server.py tests/test_config.py
```

## 3. Rename Product Surfaces

- Replace current/live English product copy with `StashSeek Chat` and Chinese
  product copy with `搜藏助手` across backend public metadata, Web, email, safe
  public errors, extension, integration metadata, evaluation descriptions, and
  current docs.
- Reframe primary copy around direct conversation with saved content and
  returning the matching source video; do not add classification claims.
- Rename the generic logo source file without modifying its pixels.
- Rename current source files whose filename is branded, updating imports,
  references, tests, docs, and package scripts.
- Keep endpoint-only domain and Telegram handle values unchanged.

## 4. Rename Browser Extension Packaging

- Update manifest/package metadata and popup copy.
- Preserve the manifest signing key and exact production/local API origins.
- Generate audited production and local packages under canonical StashSeek
  filenames.
- Keep the current old-name download path as a compatibility alias if it is a
  tracked/public URL, while Web surfaces link to the new filename.

Validation:

```bash
cd extension
pnpm test
pnpm typecheck
pnpm lint
pnpm package
pnpm package:local
```

## 5. Align Web, Docs, Specs, and Deployment Examples

- Update current README clone URLs to `deequoique/stashseek` and commands to
  `./scripts/stashseek`.
- Update Web package metadata, browser titles, accessibility labels, product
  page copy, tests, and companion download constant.
- Regenerate OpenAPI and TypeScript schema through the repository scripts; do
  not manually edit generated contracts.
- Update current documentation and active Trellis specs to lead with canonical
  names while documenting Tier B/Tier C compatibility.
- Preserve production unit/path/header/cookie/storage identifiers and mark them
  as stable operational compatibility contracts rather than stale branding.

Validation:

```bash
cd web
pnpm test
pnpm typecheck
pnpm lint
pnpm build
pnpm check:api
```

## 6. Repository-Wide Validation

- Run focused backend/deployment/extension/Web tests, then the full Python
  suite in proportion to runtime availability.
- Run migration head/current checks without downgrade or destructive data work.
- Run `git diff --check` and shell syntax checks for changed scripts.
- Run an allowlist-aware old-name scan. Any current/live former-brand reference
  must be classified as Tier B alias, Tier C stable identifier, historical
  record, old versioned artifact, or fixed.
- Review `git status --short` against the baseline to prove unrelated work was
  preserved.

## 7. Rename the GitHub Repository

- Verify repository ownership/authentication and destination availability.
- Rename `deequoique/notebook-agent` to `deequoique/stashseek` only after local
  checks pass.
- Set `origin` to the canonical new URL and verify fetch/repository metadata.
- Do not rename the active local checkout directory.

## 8. Review, Spec Update, and Finish

- Dispatch Trellis check after implementation.
- Update active specs with any durable compatibility convention learned during
  implementation.
- Commit only task-scoped tracked/new files, preserving unrelated dirty files.
- Complete the Trellis finish/archive flow with validation evidence and the
  canonical repository URL.

## Completion Evidence

- Canonical repository renamed and verified at
  `https://github.com/deequoique/stashseek`; local `origin` points to the same
  URL and `git ls-remote` resolves `main`.
- Focused backend rename, deployment, MCP, public API, and demo coverage:
  96 passed. The five demo tests require local socket binding and passed when
  run outside the filesystem/network sandbox.
- Web: 140 tests passed; TypeScript, ESLint, and generated OpenAPI checks
  passed.
- Browser extension: 98 tests passed; TypeScript and ESLint checks passed.
- Production and local compatibility archive pairs are byte-identical within
  each target; production and loopback permissions remain separated.
- `git diff --check` and the Trellis task context validator passed.
- The allowlist-aware legacy-name scan found no remaining current product copy;
  matches are compatibility aliases, stable operational identifiers, the
  LangBot plugin ID, or historical Trellis records.
- Independent implementation review found no remaining P1/P2 issues after the
  final compatibility and README corrections.

## Rollback Points

- Before package regeneration: revert source branding changes only.
- Before GitHub rename: all code remains usable through the old repository URL.
- After GitHub rename: reverse the repository rename and reset `origin` if the
  new repository cannot be verified.
- Never roll back by deleting data roots, cookies, grants, buckets, or installed
  production units.
