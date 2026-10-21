# Architecture

The server becomes API-only. Keep compatibility factory/config spellings where needed to avoid breaking integrations; never rename static options to nonexistent configuration flags.

The extension owns a full-page workspace for the migrated React library/detail/chat/account UI. The capture popup opens the workspace and retains user-invoked capture. Reuse the former tracked web sources via git show HEAD:path into extension-owned source directories; do not restore a standalone Web deployment. Keep exact API host permissions and stable extension ID.

Authentication must work directly from extension UI using email proof. Implement an explicit authenticated extension session transport that reuses existing server session resolution and tenant identity. Existing capture grants are capture-only and MUST NOT be silently elevated. Prefer a separate extension-session API namespace with bearer session handling, wrapping/reusing existing library/conversation/link handlers. Preserve cookie/CSRF rules for legacy endpoints; exact extension origin admission and safe MV3 missing-origin read policy must be intentional and tested. Communicate the concrete contract between backend and UI implementers before integration.

The previous partial patch is not accepted design: replace prompt-based login, broken auth-origin flow, implicit capture-grant library authorization, deleted CI deployment gates, inaccurate bulk documentation substitutions, and incomplete feature UI. Preserve unrelated dirty files by editing narrowly.
