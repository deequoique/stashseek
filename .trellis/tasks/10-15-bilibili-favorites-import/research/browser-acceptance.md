# Root IAB acceptance — 2026-10-16

## Environment and isolation

Earlier subagent sessions had no browser while the Mac was locked. A later root
inventory exposed Codex IAB, although native apps remained locked. Root used that
documented CUA browser, exclusively. No AppleScript, external browser automation,
OS unlock, private account or production data was used.

The actual frontend ran at 5181 against browser_qa_api.py on 8021, with canonical
DTOs/signer and all fake rows labelled with test-data copy. The fixture keeps
results/counters in RAM and never uses a DB, queue or provider. Existing user
tab 1 at 5173/login was preserved; root created QA tab 2 and source tab 3 only.

## Observations

1. Native dialog matched :modal, initial focus was its form, and background
   controls were inert. Keyboard Tab visited dialog controls; after its last
   control the browser can focus its chrome, and the next Tab returned to the
   dialog close button. Escape closed it. Reopening cleared unsaved import state.
2. Collection 22 preview showed 20 parents, then two on page two; one unavailable
   row was not selectable. Expanding first parent fetched 20 of 29 parts with
   no initial selection. P4 was disabled; legacy P3 consent defaulted off.
3. P1/P2/P3 became three choices. Confirmation remained disabled until explicit
   legacy consent. The confirmation shortcut scrolled/focused the proper region.
   Results stayed mapped to P1 duplicate, P2 quota failure, P3 queued; only P2
   remained chosen, and queued did not claim captions were ready.
4. Bulk adding the first loaded page rejected >10 atomically with explicit
   feedback. Loading its second nine-part page and bulk adding produced exactly
   ten including the retained P2. Changing favorites page preserved all ten.
   The repeated parent showed P2 checked; an unchosen P5 was disabled as the
   eleventh choice. No unseen bulk traversal occurred.
5. Default small viewport exposed an actual count-visibility issue. The owner
   fixed it with a single top sticky status/confirmation bar. Desktop 1280×900
   and mobile 390×844 were rechecked after reload. Mobile document scrollWidth
   and clientWidth both equalled 390; dialog width was 390. After scrolling its
   long list, count bounds remained top188/bottom230 inside height844.
6. Explicit video ?p=21 preview opened part page two with no selection. Keyboard
   Space selected P21. The intentional unknown note caused an uncertain result,
   locked the draft and offered same-operation confirmation. Explicit replay
   returned queued. RAM counters after the earlier partial batch plus this
   attempt/replay were batch_calls3 and unique_operations2, confirming no fresh
   operation for the uncertain replay.
7. Detail source, chapter and transcript links used owned public-item paths.
   Actual chapter click opened its source tab, whose fixture JSON reported
   https://www.bilibili.com/video/BV1xx411c7mD?p=6&t=185. This verifies the UI
   action/local time; real service ownership/reorder/303 checks are backend tests.
8. Browser dev error/warn logs were empty before and after the interactions.
   No JavaScript error was observed. Fixture stderr had cancellation-related
   BrokenPipeError from a browser abort, not a frontend error or data operation.

## Screenshots and limits

- ../artifacts/browser-before-count-fix.jpg records the real layout issue.
- ../artifacts/favorites-desktop.jpg records the fixed count bar and ten choices.
- ../artifacts/favorites-mobile.jpg records the fixed 390×844 layout.
- ../artifacts/source-click.jpg records the test-only resolved P/time JSON.

The read-only evaluator did not expose Web Storage objects. Its initial attempted
storage count raised a tool-scope TypeError before any subsequent UI action;
storage enumeration was not performed. No refs or storage contents were printed.
Do not describe the source/unit no-persistence checks as real storage inspection.
The public provider canaries are separate from this synthetic UI fixture; no
actual user collection was imported. Session/CSRF/account-rotation security and
held-response disposal are covered by canonical API/component tests, not fake
fixture authentication.

Root closed both created tabs and reset the viewport override. Final inventory
contained only the unchanged original 5173/login tab. The fixture owner stopped
both owned server sessions and verified 8021/5181 had no listener.
