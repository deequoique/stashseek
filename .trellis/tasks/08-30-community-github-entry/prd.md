# Add community and GitHub entry points

## Goal

Celebrate the first real users of StashSeek Chat by giving them a clear in-app
announcement and durable ways to join the user community or contribute to the
open-source project.

## Requirements

- Show a concise Chinese announcement to authenticated users beneath the main
  top bar. The copy should thank users, announce the new QQ user group, and
  invite feedback and discussion.
- Include QQ group number `1107151131` in the announcement as selectable text.
- Let a user dismiss the announcement. Remember the dismissal in local browser
  storage under a versioned, non-sensitive key so the same announcement does
  not return on every route or reload; if storage is unavailable, dismissal
  should still work for the current page lifetime.
- Add durable community and open-source entries directly to the authenticated
  top bar as first-level actions, not inside the account menu:
  - `加入用户交流群` exposes QQ group number `1107151131` without pretending
    that a universal browser deep link is available.
  - `GitHub · 欢迎提 PR` links to
    `https://github.com/deequoique/stashseek` in a new tab with safe external
    link attributes.
- Give both top-level entries recognizable icons: the Tencent QQ brand mark for
  the group and the GitHub brand mark for the repository. The compact top bar
  may use icon-only controls, but each control must retain an explicit
  accessible label and reveal its purpose through native title text or its
  popover.
- Add a public GitHub contribution link to the showcase footer so visitors can
  discover the repository before logging in.
- Keep the announcement and menu usable at the mobile baseline, preserve
  visible keyboard focus, and use semantic HTML/accessibility labels.
- Keep this as static frontend product copy. Do not add an API, database state,
  analytics, or a new dependency.

## Acceptance Criteria

- [x] An authenticated user sees a Chinese community announcement directly
      below the sticky top bar, including QQ group number `1107151131`.
- [x] The announcement can be dismissed with a labelled control and remains
      dismissed across routes and reloads in the same browser profile.
- [x] The app top bar always provides first-level QQ and GitHub actions, even
      after the announcement has been dismissed, with a distinct icon for each
      entry; neither action is nested inside the account menu.
- [x] The public showcase footer links to the confirmed repository URL and
      explicitly welcomes contributions/PRs.
- [x] External GitHub links open safely in a new tab; the QQ number remains
      readable and selectable without relying on an unsupported QQ URL scheme.
- [x] App-shell tests cover announcement visibility, persistent dismissal, QQ
      content, and the GitHub link; showcase tests cover its public GitHub link.
- [x] Frontend tests, type checking, lint, build, and API staleness checks pass.
- [x] At 390px width, the banner, account menu, and showcase footer have no
      horizontal overflow and all interactive controls remain reachable.

## Notes

- Approved announcement copy:
  `非常高兴看到您在使用这款产品。StashSeek 仍处于非常初期的阶段。为了更好地维护和迭代这款产品，诚邀您加入用户交流群（QQ群：1107151131）。同时，StashSeek 完全开源，也欢迎任何人在 GitHub 上提交 Issue 与 PR。`
- The authenticated top bar is the durable product-level placement. The
  showcase footer is the durable public/open-source placement. The banner is a
  one-time announcement rather than permanent navigation.
- This is a lightweight, frontend-only task; a PRD is sufficient without
  separate design and implementation documents.
