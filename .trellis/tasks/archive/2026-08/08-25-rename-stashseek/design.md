# StashSeek Chat Rename Design

## 1. Rename Boundaries

The rename uses three explicit tiers so branding can change without turning a
cosmetic release into a data or production migration.

### Tier A — canonical names change now

- GitHub repository: `deequoique/stashseek`
- Product: `StashSeek Chat`
- Chinese product: `搜藏助手`
- Repository/package metadata and current README clone URLs
- Web titles, wordmarks, accessibility labels, email subjects, API titles, safe
  public errors, extension metadata, popup copy, and current documentation
- Canonical launcher: `scripts/stashseek`
- Canonical MCP ask tool: `ask_stashseek`
- Canonical environment family: `STASHSEEK_*`
- Current source filenames and generated browser-extension archive names where
  the former brand is part of the filename

### Tier B — compatibility aliases remain

- `scripts/notebook-agent` remains a thin executable wrapper around the same
  deployment entry point.
- `NOTEBOOK_AGENT_*` environment keys remain fallbacks for their corresponding
  `STASHSEEK_*` keys. New keys win on conflict.
- `ask_notebook_agent` remains an MCP alias for `ask_stashseek`; both execute
  the same validated facade method and preserve grant scope and tenant binding.
- Existing public download URLs for the current extension package may remain as
  aliases while new pages link to the StashSeek filename.

Aliases must be centralized and tested. Business logic must not be forked by
brand name.

### Tier C — stable invisible contracts do not change

- `__Host-kb_session` and `__Host-kb_csrf`
- `X-KB-Timestamp`, `X-KB-Nonce`, `X-KB-Signature`, and `KB_BOT_CHANNELS`
- Redis session key prefixes and existing persisted grant/identity data
- default PostgreSQL database `kb` and object bucket `kb-raw`
- installed `notebook-agent*.service` unit names, `/opt/notebook-agent`,
  `/etc/notebook-agent`, `/var/lib/notebook-agent`, `/var/log/notebook-agent`,
  and deployment service identities
- the production hostname and Telegram bot handle

These values are operational identifiers, not current product copy. Renaming
them requires a separately planned dual-read/dual-run migration and offers no
user benefit in this release.

## 2. Product Copy Contract

The primary description is:

> Chat with everything you've saved.

The Chinese promise is:

> 打开插件，直接问，找到你收藏过的那段视频。

Current user-facing copy should describe the implemented product honestly:
supported sources and current limitations remain explicit. The rename may
state the cross-platform saved-content direction, but it must not claim that
all streaming-platform connectors already ship. Classification, folders, tags,
and manual organization must not become the proposed workflow.

## 3. Configuration Compatibility

Introduce a single configuration lookup that accepts a canonical key and an
optional legacy key:

```text
process STASHSEEK_* -> process NOTEBOOK_AGENT_* -> existing lower-precedence
configuration source -> default
```

Only the name changes; validation, redaction, and production safety gates stay
the same. Code-level settings fields become brand-neutral or StashSeek-named so
new implementation does not continue spreading the former brand. Tests cover
new-only, legacy-only, both-set, invalid-value, and secret-free diagnostics.

## 4. MCP Compatibility

`ask_stashseek` becomes the canonical read tool. `ask_notebook_agent` remains a
deprecated compatibility alias. Both names:

- accept the same bounded schema;
- enter the same `ChannelService -> KnowledgeAgent` flow;
- resolve the same tenant-bound grant;
- return the same evidence/citation response;
- reject slash commands and invalid schemas identically.

Read-scope discovery contains the canonical tool plus the compatibility alias
and the existing inventory reads. Direct invocation is guarded by the same
allowlist as discovery. Documentation leads with `ask_stashseek` and labels the
old name as compatibility-only.

## 5. Launcher and Packaging Compatibility

`scripts/stashseek` becomes the documented launcher. The old executable stays
as a minimal wrapper and passes arguments and exit status unchanged.

Python distribution metadata changes from the placeholder `kb` name to
`stashseek`; import paths remain `app.*`, so no database or Python package
namespace migration is required. Web, extension, and promotional package names
use lowercase `stashseek-chat-*` identifiers.

The extension keeps its manifest signing `key`. Its visible name becomes
`StashSeek Chat — 搜藏助手`, its description and popup become conversation-led,
and its package archive uses a StashSeek filename. Retaining the signing key
preserves the extension ID; retaining the existing API origin preserves paired
grant origin binding.

## 6. Repository and External Rename

After repository edits and validation succeed:

1. Verify `deequoique/stashseek` is available.
2. Rename the GitHub repository from `notebook-agent` to `stashseek`.
3. Set local `origin` explicitly to
   `https://github.com/deequoique/stashseek.git`.
4. Verify repository metadata, default branch, and remote fetch URL.

The active local checkout directory remains unchanged because renaming a parent
directory underneath the running Codex workspace can invalidate tool paths. A
future fresh clone naturally uses `stashseek/`.

## 7. Historical and Dirty-Worktree Policy

Archived Trellis tasks, migration history, and journal prose retain the names
that were accurate when written. Active specs are updated because they govern
future work. Existing untracked user files with current product copy may receive
only scoped rename edits when they are direct current product artifacts; they
must not be deleted, reformatted wholesale, or otherwise rewritten.

The final legacy-name scan distinguishes:

- approved compatibility identifiers;
- stable Tier C contracts;
- historical records;
- stale current/live references, which fail the gate.

## 8. Rollout and Rollback

- Branding/source rollback is an ordinary Git revert.
- Compatibility aliases make CLI, MCP, and environment rollback unnecessary
  for existing clients.
- The GitHub repository rename is reversed through GitHub if validation after
  rename fails; `origin` is reset to the restored URL.
- No database migration, session invalidation, bucket copy, production unit
  replacement, or extension re-pairing is part of rollout.
