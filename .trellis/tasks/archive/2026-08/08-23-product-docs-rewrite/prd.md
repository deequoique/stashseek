# Rewrite product README and user documentation

## Goal

Present Notebook Agent as a product before presenting it as a codebase, then
give self-hosting users a documentation system in which every page has one
clear purpose and one intended outcome.

## Audience and user goals

- The primary README reader is evaluating the product: they want to understand
  the problem it solves, the experience it provides, its trust boundaries, and
  whether it is relevant to them.
- The primary documentation reader has basic command-line experience and wants
  to self-host, connect, use, or operate Notebook Agent without first learning
  the repository architecture.
- Maintainers and integrators remain secondary readers. Technical contracts
  must remain available, but they must not dominate the product landing page or
  first-run tutorial.

## Requirements

### Root README files

- Rewrite both `README.md` and `README.zh-CN.md` as parallel product landing
  pages, with English and Simplified Chinese content kept semantically aligned.
- Lead with the user problem, product promise, representative workflow, and
  evidence-backed outcome. Move prerequisites, infrastructure, repository
  layout, and contributor verification below the product story or into docs.
- Explain the supported product surfaces: Web library and search, MCP, optional
  Telegram/WeChat through LangBot, and the browser companion.
- Describe supported sources and meaningful limitations accurately. Do not
  present planned sources, ASR, or platform access paths as generally available.
- Make the difference between a saved-link collection and a source-grounded,
  searchable video knowledge library concrete.
- Provide a short route to self-hosting and clear navigation for product users,
  deployers, integrators, and contributors without duplicating runbooks.
- Use three locally stored screenshots from the public product page to show the
  product promise, four-step user flow, and evidence-with-timestamps outcome.
  Link each image back to the corresponding public page section, and do not
  capture authenticated or private library content.

### Documentation

- Rewrite `docs/` around the Diataxis quadrants: tutorial, how-to, reference,
  and explanation. Each page must have one dominant document type.
- Use Chinese as the primary language for `docs/`, retaining standard English
  protocol, command, and configuration names where precision requires them.
- Replace the current mixed-language indexes and split the monolithic deployment
  and configuration material by user goal without losing operational guidance.
- Include the browser companion in the user journey and link to its package
  installation/safety notes rather than duplicating the extension manifest
  reference.
- Verify claims and examples against the current branch, especially launcher
  profiles, CLI surfaces, MCP scopes, Web behavior, connectors, and browser
  companion behavior.
- Preserve unrelated worktree changes and avoid edits to `demo/`, `promo/`,
  `evals/`, and package-owned plugin documentation.

## Content principles

- Product copy must be concrete and falsifiable; avoid generic AI claims.
- A reader should be able to distinguish shipped behavior, optional components,
  operational constraints, and future work.
- Security guidance must remain explicit at the point of action, especially for
  secrets, public listeners, TLS, tenant boundaries, and browser capture.
- Existing Markdown may be used as a factual inventory, but all rewritten prose
  should be organized and expressed for the newly defined reader goal.

## Acceptance criteria

- [x] Both root README files foreground the product and keep their claims and
      navigation in sync.
- [x] Both root README files link to the live product page and use the approved
      public screenshots to explain the user journey and answer experience.
- [x] A new reader can explain the product's problem, workflow, differentiator,
      supported inputs, output, and privacy posture before reaching setup steps.
- [x] `docs/` has distinct tutorial, how-to, reference, and explanation entry
      points with concise index pages and intentional reading paths.
- [x] The first-run tutorial reaches a verifiable successful outcome without
      requiring the reader to consult the full configuration reference.
- [x] Deployment, backup/restore, upgrade/rollback, and troubleshooting content
      are reachable as problem-oriented guides rather than sections in one long
      manual.
- [x] Configuration, runtime profiles, MCP, Web API, and CLI facts are reachable
      as reference material.
- [x] Existing operational guidance is mapped to a rewritten destination or is
      explicitly rejected as obsolete after source verification.
- [x] Every relative Markdown link resolves and documented commands/options used
      in critical paths match the current CLI surfaces.
- [x] No unsupported or planned capability is written as a shipped feature.

## Scope exclusions

- Product code, UI behavior, deployment automation, and API changes.
- New custom illustrations, authenticated screenshots, or marketing assets
  beyond the three public product-page captures approved on 2026-08-23.
- Rewriting `demo/`, `promo/`, `evals/`, migration notes, or package-owned
  LangBot/plugin documentation.
