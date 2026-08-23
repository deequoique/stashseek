# Implementation plan

The user approved `design.md` on 2026-08-23.

1. Build a claim-and-command inventory from the current code, launcher, CLI,
   Web surfaces, connectors, extension documentation, and existing runbooks.
2. Create the approved Diataxis directory structure and a migration checklist
   so every existing section has a destination.
3. Rewrite the Chinese product README first, validate its narrative and claims,
   then create the semantically aligned English README.
   Add the user-approved public product-page captures for the hero, four-step
   flow, and evidence-backed answer; do not capture authenticated content.
4. Write the first-run tutorial and documentation landing/index pages.
5. Split and rewrite user-goal how-to guides, keeping security warnings at the
   action where they matter.
6. Consolidate factual contracts into reference pages and verify them against
   the current command/API/configuration surfaces.
7. Write explanation pages for architecture, ingestion/retrieval, privacy/trust,
   and identity/channel concepts.
8. Move and refresh specialised production runbooks, then update every
   repository link that points into the old documentation hierarchy.
9. Review all pages for document-type drift, terminology consistency,
   unsupported claims, repetition, and missing operational guidance.

## Validation

- Check every repository-relative Markdown link and image target.
- Compare launcher examples with `./scripts/notebook-agent --help` and relevant
  subcommand help.
- Compare app CLI examples with `.venv/bin/python -m app.cli --help` and relevant
  subcommand help.
- Compare source/platform claims with registered connectors and current Web/
  extension behavior.
- Search for stale paths, inconsistent source support, mixed-language index
  prose, placeholder repository URLs, and planned capabilities stated as shipped.
- Review the final diff to ensure unrelated untracked and modified files remain
  untouched.

## Completion record

- Product README and Diataxis structure approved by the user before writing.
- Root README files remain semantically aligned and product-first.
- Three local public-page screenshots show the product promise, user flow, and
  evidence-with-timestamps outcome, with links back to the live product page.
- All local Markdown links resolve; no stale pre-migration documentation paths
  remain in the rewritten surface.
- `.env.example` has 122 documented configuration variables with no omissions.
- Launcher-aware operator examples preserve process > `.env` > `.env.runtime`
  precedence without printing or copying secrets.
- Notification heartbeat, PostgreSQL delivery ledger, manual redrive, and the
  retired `ingest-completion` queue prohibition remain documented and tested.
- Relevant deployment, MCP, notification, and split-frontend contract tests
  pass (138 tests in the final deployment-focused run). Web auth, source
  connector, and browser-companion contracts pass in a separate 145-test run.
- The complete repository suite was also sampled: 765 tests passed and 73 were
  skipped; unrelated pre-existing environment-sensitive failures and sandboxed
  local-socket errors remain outside this documentation task.
- No `.trellis/spec/` update is required: this task changes documentation
  structure and examples, not an implementation contract.
