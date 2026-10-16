# Execution and ownership

1. GPT-6 Luna backend implementer: server extension authentication/session transport; reuse library/conversation/account routes; security and runtime tests. Own app/ and tests/ changes related to this migration.
2. GPT-6 Luna frontend implementer: migrate old tested Web components into extension-owned workspace, capture popup integration, contracts/build/audits/tests. Own extension/ and scripts/export_web_openapi.py.
3. GPT-6 Sol reviewer: read-only initial audit, then independent final review of auth, functional parity and infrastructure.
4. Coordinator: maintain task plan and acceptance matrix, reconcile contract and findings, dispatch a Luna implementation pass for CI/deploy/docs/spec corrections after initial backend work.
5. Verify appropriate focused backend suites and all extension checks, production/local builds and audits; browser smoke where supported. Re-review fixes. Do not claim complete based only on pre-existing tests.

Worktree contains substantial unrelated work. No blanket restore/reset/stage/commit. Do not deploy or alter live accounts/data. Validation should use .venv/bin/python and a supported installed Node runtime.

## Completion record

- Luna implemented API-only runtime, extension auth/session transport, workspace migration, capture popup integration, CI/deploy preservation, and documentation updates.
- Sol independently reviewed the integrated changes. Findings around auth bootstrap, OpenAPI coverage, invalid bearer handling, CI action ordering, and capture-grant library privilege were fixed and rechecked.
- The extension lockfile was regenerated from the migrated dependency graph so CI's frozen install matches `extension/package.json`.
