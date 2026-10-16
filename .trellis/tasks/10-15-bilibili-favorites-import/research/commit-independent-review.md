# Independent local commit split review

Date: 2026-10-16. Reviewer: `trellis-check` (`/root/multipart_check`).
Status: prepared A/B/C source, dependency boundary and canonical contract gates
passed. Final index inspection remains pending; this does not approve an
uninspected or unexpectedly populated index.

## Scope and authorization

Reviewed the approved three-commit plan in
`.trellis/tasks/10-15-bilibili-favorites-import/commit-plan.md`, its current task
requirements and the Trellis finish-work procedure. The user approved the three
local work commits by replying “继续” after the concrete plan. This does not
authorize a push, deployment, production migration or importing the real sample.

Compared the cumulative `chat` and `auth` snapshots against original commit
`74182624a89393ea6dbd0a054e708322d287924b`, the backend split manifests and the
frontend preparation manifest. No tracked baseline file was missing from either
snapshot. The reviewer has not changed product files, snapshots, Git index or
commits during this read-only split review.

The separate pre-commit `working-before.json` is available: all 1,116 recorded
paths were compared by SHA-256, with zero missing paths. Only the six explicitly
sanitized logs, confirmation text in commit-plan, the approved exporter/type-safety
fix and its two regenerated files differed. Other original product source files
were unchanged by the split preparation. This is a pre-commit preservation check,
not a claim to have recovered the older missing pre-implementation snapshot.

## Work boundaries

- A contains chat keyboard/viewport/history behavior, public history column
  projection, lazy routes and cancelable/preloaded queries. The final preparation
  also includes the narrow deterministic exporter fix/test/spec described below.
  It has no external login routes or new Bilibili APIs/capabilities.
- B is cumulative A plus external login. Its model additions contain external
  identities/flows/email anchors and session constraints, without the Bilibili
  source fields or their `String` import. Incremental API/runtime/exporter service
  additions contain external login only. Its five external route patterns are present;
  parts/favorites routes and generated capabilities are absent.
- C retains the final reviewed working tree, including independent CID identity,
  the source fence, bounded public collection discovery, explicit selection and
  current-source navigation. Its migration follows the preceding SSO migration.

The following small reclassifications were explicitly approved by the root and
are recorded in `frontend-preparation.json`:

- AppShell changes belong to B because their behavior is solely SSO.
- Shared capability-query call sites in LoginPage and LibraryPage belong to A:
  they are dependencies of the common cancelable history-query behavior.
- Generic App observer remount by generation/key belongs to A: rotating private
  QueryClients must remount observers to retain request deduplication and cache
  isolation. The external login/probe/provider flow stays in B.

The backend path hashes matched the preparation manifest. Read-only source
inspection found no unrelated scope leaked into the staged preparations. The
earlier Bilibili capability fixture residues were reported and the owner removed
all seven occurrences from B before its final frontend gates.

## Runtime and canonical contracts

The review found that the shared venv imported Starlette 1.8.0 while `uv.lock`
pins 0.52.1. Root assigned one operator to correct the local environment, without
changing product dependency declarations or the lockfile. The operator reports
verified wheel hash/size/metadata, only Starlette changed, `pip check` passed and
123 API tests passed on 0.52.1. The reverse backup is kept outside the repository.

Nineteen older A OpenAPI 422 descriptions also differed under the current Python
3.13.15 standard library: `HTTPStatus(422).phrase` is “Unprocessable Content”.
That difference persisted after restoring locked Starlette, and the checked CI
workflow uses Python 3.11. Root therefore fixed the canonical exporter, applying
the same narrow common fix to A/B/C: normalize only descriptions exactly equal
to one of the two standard 422 phrases to the original “Unprocessable Entity”.
The fix preserves custom descriptions, other statuses and schema content and
does not change runtime HTTP errors. No generated file was hand-edited.

Independent final checks passed on all three trees:

- `tests/test_web_openapi_export.py`: 2 passed per tree. These render the real
  canonical app under both standard phrases and retain a real custom 422 probe.
- `node scripts/openapi.mjs check`, then the installed `openapi-typescript` CLI
  with `--check`: both passed per tree. A's generated JSON and TypeScript are
  byte-identical to original HEAD; B/C were regenerated from their own sources.
- A has 29 API paths and no external/Bilibili additions. B has 34 paths,
  exactly the five external route patterns, no Bilibili routes/capabilities or
  Bilibili generated types. C has 38 paths: those routes plus three Bilibili
  routes and owned source opening, and its two Bilibili capabilities/types.
- The new regression and type-safety spec are identical across all three trees.

Plain pnpm 11.25 startup in the temporary symlinked snapshots attempted an
automatic dependency check/install and was safely refused by pnpm's modules-dir
boundary. No dependency directory was removed or installed. The reviewer used
the exact two local commands in `check:api` directly afterward; no network or
package-manager self-update is needed for this gate. C's plain command also
completed successfully, and its direct command pair was checked separately.
The installed frontend tools and gate semantics were used; this is not a claim
that this local launcher version equals the pinned CI pnpm version.

Only Starlette was aligned to that particular lock entry. Other installed
dependencies are pip-compatible; a full lockfile synchronization is not claimed.

Alembic graph inspection found one head per stage: A `d04fdae36884`,
B `e2f3a4b5c6d7`, then C `f8a9b0c1d2e3`. No migration was applied to user or
production data by this review.

## Validation evidence and limits

Owner handoffs, not fresh full reruns by this reviewer:

- A frontend: 176 tests across 19 files, typecheck, eslint and production build
  passed, including the repaired observer remount behavior.
- B frontend: 205 tests across 20 files, typecheck, eslint and production build
  passed. The reviewer additionally passed the final canonical checks above.
- A backend split: 9 passed. B backend split: 169 passed, 19 skipped and 14 MSAL
  warnings with isolated services disabled. No new services were started.
- Full C before the environment correction: 1216 passed, 74 skipped, two known
  date-dependent recycle-bin failures. These pre-existing August-seed/October
  execution failures were not hidden by unrelated test edits.
- Final C frontend: 256 tests and all frontend gates passed before the split.
- Existing independent feature checks additionally cover focused backend/frontend
  behavior, isolated PostgreSQL, a real owned loopback Redis gate, root native
  browser acceptance using RAM-only fixtures, and bounded real metadata canaries.
  Those reports distinguish real provider metadata from fixtures and do not claim
  real favorites import, complete subtitle availability or provider-login success.

Root's final full backend confirmation after the Starlette correction reported
1194 passed, 104 skipped and the same two known date-dependent failures. This
run includes the two new exporter tests, has no live PostgreSQL and skips the
six Redis opt-in tests. It is a separate run rather than an arithmetic sum with
the earlier isolated-service run. Earlier real PostgreSQL/Redis results remain
separately documented; package compatibility is not a full lock synchronization.
The reviewer independently parsed `artifacts/commit-backend.xml`: 1,300 cases,
104 skipped, zero errors and exactly the same two recycle-bin assertion names.

## Private and large artifacts

Read-only scanning of the included dirty/untracked paths found no live private
key/API token/session-cookie capture, credential-bearing live DSN, HAR, database
or persistence dump, downloaded Redis/PostgreSQL binary, or dirty file over 1 MiB.
DSN/key-like text found in `.env.example`, config templates and disposable fixtures
is example/test material. Generated OpenAPI/TypeScript, lockfile and test XML are
the largest ordinary records. This is not a claim of exhaustive OCR of screenshots.

Six pytest logs initially contained a local Mac hostname and `/Users/...` paths.
Root preserved their originals outside the repository in `raw-test-logs/` and
redacted the personal host/home paths in these included records:

- `artifacts/foundation-backend.xml` and `.txt`
- `artifacts/combined-backend.xml` and `.txt`
- `artifacts/combined-backend-final.xml` and `.txt`

The reviewer rescanned all six sanitized files: no personal `.local` hostname or
home-directory path remains, and result counts are unchanged. Public Bilibili
sample IDs/titles are bounded public input evidence rather than credentials.
Snapshot caches and temporary runtime backups are not staging manifests and must
remain outside the work commits.
The later `artifacts/commit-backend.txt`/`.xml` and
`research/commit-environment.json` were also independently scanned: no personal
home-directory path or Mac `.local` hostname remained.

## Exclusions and finish-work

Keep these two unrecognized task directories untracked and untouched:

- `.trellis/tasks/10-14-email-login-delivery/`
- `.trellis/tasks/10-14-verify-local-runtime/`

Only the two current dependency children and current parent are to be archived.
After the three work commits and an empty-index check, archive multipart first,
favorites-preview second and parent favorites-import last. `find_task_by_name`
only resolves active direct children, so this order avoids modifying archived
child parent links. Preserve their historical parent value. Other old SSO/history/
BV tasks are records in work commits and are not automatically archived this time.

Both archive and journal helpers commit all existing index entries, so verify an
empty index before each bookkeeping operation. The archive helper stages the
archive subtree; verify it has no unrelated dirty paths. The correctly initialized
`workspace/codex` belongs to the final journal bookkeeping, not A/B/C. Do not add
ignored `.developer`/`.runtime`, or the earlier discarded `--help` probe record.
Journal the three work-commit hashes, excluding archive hashes. Do not force-add
`.trellis` or blindly stage whole active task trees.

Expected final leftover status is only the two excluded untracked directories.
No push/deployment/production migration follows from these local commits.

## Final index gate

A passed independent index review before committing, while HEAD was still
`74182624a89393ea6dbd0a054e708322d287924b`: exactly 31 paths matched
`chat-stage-paths.json`, every index blob was byte-identical to the prepared chat
snapshot, no protected/excluded path was staged, and `git diff --cached --check`
passed. No SSO or Bilibili service/schema additions entered A. The common exporter
normalization was the only new contract-source behavior in this stage. Root then
created A as `b4d60fe6f5529498fbab0501ebccd56f6ff6cedb`.

B passed independent index review with HEAD at A: exactly 104 paths matched
`auth-stage-paths.json`, all index blobs were byte-identical to the updated auth
snapshot, and `git diff --cached --check` passed. The sole preparation adjustment
was removal of an extra EOF blank line in the temporary auth client, which did
not alter behavior or the real working source. No Bilibili/protected/excluded
runtime path was staged; its generated contract and migration boundaries are the
previously checked B stage. The largest file was the ordinary 429,664-byte uv.lock.
Root then created B as `2bb6dbc`.

Pending: C actual index comparison against the final reviewed workspace and
protected exclusions before its commit.
