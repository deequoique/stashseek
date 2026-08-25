# Rename project to StashSeek Chat

## Goal

Rename the repository and product from **Notebook Agent** to **StashSeek Chat**,
with **搜藏助手** as the Chinese product name and `stashseek` as the canonical
repository name. The renamed product should present itself as a browser-first
conversational Agent that searches inside content saved across platforms and
returns the matching source video, rather than as a notebook, classifier, or
collection-management product.

## Background

- The current GitHub remote is `https://github.com/deequoique/notebook-agent.git`.
- The current checkout directory is `/Volumes/PeeB/projects/notebook-agent`.
- The current product name appears throughout the Web UI, extension, README,
  documentation, deployment assets, optional LangBot integration, promotional
  assets, and tests.
- The current implementation also exposes long-lived technical identifiers such
  as `scripts/notebook-agent`, `NOTEBOOK_AGENT_*` environment variables,
  `notebook-agent-*` systemd units and filesystem paths, `__Host-kb_*` cookies,
  `KB_*` gateway headers/configuration, and the Python distribution name `kb`.
  These identifiers have different compatibility and migration risks from
  user-facing branding.
- Existing untracked files and task directories predate this rename task and
  must not be overwritten or removed.

## Requirements

1. The canonical GitHub repository name is `stashseek`, and repository URLs and
   clone instructions use `deequoique/stashseek` after the external rename.
2. User-facing English branding uses **StashSeek Chat**.
3. User-facing Chinese branding uses **搜藏助手**.
4. The primary product description communicates a zero-organization chat flow:
   open the browser extension, ask a question, and receive matching saved videos
   with verifiable source context.
5. User-facing copy must not position classification or manual organization as
   the product's core workflow.
6. Browser-extension metadata, visible Web surfaces, README files, current docs,
   current deployment examples, package metadata, tests, and generated-contract
   sources must be internally consistent with the approved rename policy.
7. Existing user data, credentials, paired browser devices, database contents,
   object storage, cookies, grants, and production service state must not be
   silently invalidated by cosmetic branding changes.
8. Historical Trellis tasks, archived research, migration history, and other
   immutable historical records retain their original names unless a live link
   must be repaired.
9. The rename must preserve unrelated user work in the dirty working tree.
10. Compatibility behavior follows a layered policy:
    - New user-facing and developer-facing entry points use StashSeek names.
    - The former launcher, environment keys, and MCP ask tool remain supported
      as documented compatibility aliases.
    - Invisible persisted/wire identifiers (`__Host-kb_*`, `X-KB-*`,
      `KB_BOT_CHANNELS`, Redis session prefixes, the default database/bucket,
      production systemd unit names, and production filesystem paths) retain
      their existing values in this task.
11. When both a new `STASHSEEK_*` environment key and its legacy
    `NOTEBOOK_AGENT_*` alias are set, the new key wins without exposing either
    value in logs.
12. The existing extension signing key is retained so the renamed extension
    keeps the same Chrome extension identity and pairing continuity.
13. The existing public production domain and Telegram bot handle remain
    operational endpoints; visible copy around them uses the new brand.
14. The existing generic logo is retained because it already depicts a chat
    bubble and does not contain the former product name; its source filename
    may be renamed to match the new brand.

## Acceptance Criteria

- [ ] The GitHub repository is named `deequoique/stashseek`, and the local
  `origin` remote targets its canonical URL.
- [ ] Current user-facing English product surfaces display `StashSeek Chat` and
  current Chinese surfaces display `搜藏助手`.
- [ ] The extension store/package surface identifies the product as a
  conversational Agent for searching inside saved content.
- [ ] Current README clone commands, repository links, badges, and product copy
  use the canonical repository and product names.
- [ ] A scoped repository scan finds no unintended current/live references to
  `Notebook Agent`, `notebook-agent`, or the old public repository URL; approved
  compatibility aliases and historical records are explicitly allowlisted.
- [ ] Existing persisted identity and storage contracts remain usable according
  to the approved compatibility policy.
- [ ] `./scripts/stashseek` is the canonical launcher and
  `./scripts/notebook-agent` continues to invoke the same lifecycle behavior.
- [ ] MCP clients can use the canonical `ask_stashseek` tool while existing
  clients using `ask_notebook_agent` continue to work with the same tenant,
  scope, and evidence behavior.
- [ ] New `STASHSEEK_*` configuration keys work, legacy `NOTEBOOK_AGENT_*`
  aliases still work, and new keys have deterministic precedence.
- [ ] Existing browser extension installations retain their extension ID and
  pairing state after receiving the renamed build.
- [ ] Relevant Python, Web, extension, deployment, documentation, and contract
  checks pass after the rename.
- [ ] The existing dirty worktree remains intact apart from files intentionally
  changed by this task.

## Out of Scope

- Implementing new platform connectors, automatic classification, or a new chat
  experience beyond renaming and aligning the current product surfaces.
- Rewriting archived Trellis task prose, old migration commentary, or immutable
  historical artifacts solely to erase the former name.
- Changing the public production domain, Telegram bot handle, or legal entity
  unless explicitly included by a later approved decision.
- Renaming invisible database/storage identifiers, Redis key prefixes, gateway
  header names, installed production systemd units, production service users,
  or production filesystem roots.
- Renaming the local checkout directory while this workspace is active. The
  GitHub repository and remote URL are the repository-name acceptance target.

## Key Decisions

- Product: **StashSeek Chat** / **搜藏助手**.
- Repository: `deequoique/stashseek`.
- Positioning: conversational browser Agent; no classification or manual
  organization workflow is introduced by this task.
- Migration: compatibility-first. Canonical names change now; persistence and
  wire contracts remain stable; selected developer-facing aliases bridge old
  clients and automation.
- Historical records remain historical rather than being rewritten.
