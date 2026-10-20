# Component Guidelines

> How React components are built in the StashSeek Chat Web client.

---

## Overview

Components are plain function components. Pages orchestrate queries and mutations; views receive browser-safe DTOs and callbacks. Components do not infer tenant identity, ingestion state, retry eligibility, or transcript ownership.

---

## Component Structure

1. Import generated contract aliases with `import type`.
2. Define a narrow local props interface when the generated DTO is not the complete prop shape.
3. Derive display-only values during render.
4. Keep event handlers close to the relevant form or control.
5. Return semantic HTML before adding ARIA.

`VideoDetailPage` owns network state. `VideoDetailView` owns the visual layout and is tested with explicit data and callbacks.

---

## Props Conventions

- Accept public IDs and browser-safe DTOs only.
- Inject network functions only where it materially improves isolated testing, as in `LoginPage`, `LibraryPage`, and `AddVideosDialog`.
- Do not pass `user_id`, `app_user_id`, `ChannelIdentity`, request keys, raw object keys, task IDs, or ORM objects.
- Use callbacks for user actions. A presentation component must not import a database or channel concept.
- Optional data is rendered only when meaningful. Null title uses an honest pending label; null author is omitted rather than invented.

---

## Styling Patterns

- One tracked stylesheet, `src/styles.css`, defines the current small design system.
- Use CSS custom properties for palette and common surfaces.
- The mobile layout is the default; desktop changes live in min-width media queries.
- The desktop page container is capped at `1080px`.
- Do not add Tailwind, CSS-in-JS, a component framework, or runtime theme state without a separate design decision.
- Motion must have a `prefers-reduced-motion` fallback.

---

## Accessibility

- Every form field has a programmatic label.
- Icon-only buttons have an accessible name.
- Use native `<dialog>`, `<details>`, headings, lists, links, and buttons.
- Dialogs use `showModal()` so focus and the backdrop are real browser behavior.
- Async status changes use `aria-live`, `role="alert"`, or `aria-busy` as appropriate.
- Focus indicators remain visible; keyboard and touch controls are at least 44px high where practical.
- Thumbnails are decorative when the adjacent title already names the destination.
- Keep `main#main-content` focusable by the skip link, but suppress the global
  focus outline on `main.page-container` itself. A page-sized outline appears
  as unwanted red side lines; interactive controls and scroll regions retain
  their own visible keyboard focus indicators.

## Fixed Chat Viewport

- Only the chat route locks the document to the dynamic viewport height. The
  application shell reserves its actual top bar and optional announcement
  heights; the main area and nested chat grids use `min-height: 0` to share
  the remaining space. Leaving chat restores normal document scrolling.
- The history list and transcript are separately named, keyboard-focusable
  scroll areas with overscroll containment. Keep the history heading/new button
  and the question composer outside those scrolling areas.
- The complete textarea, shortcut hint, and send button stay inside the
  viewport on desktop, narrow, and short screens. Compact heading spacing and
  allocate remaining height instead of imposing a fixed minimum chat height.
- Present the library return link as a prominent, keyboard-accessible action
  with at least a 44px hit area, sufficient contrast, and a visible focus state.
- Question positioning measures the actual bounded transcript content height
  at every breakpoint. It must scroll the transcript without moving the page.
- Verify real browser geometry and wheel/keyboard behavior: no chat document
  overflow, independently scrollable history/transcript, fully visible composer,
  retained latest-question positioning, and normal library scrolling on exit.

## Chat Composer Keyboard

- The `chat-question` textarea sends on Enter; Shift+Enter inserts a newline.
- Submit through the existing form with `requestSubmit()` so keyboard and button
  sends share validation, loading state, and duplicate-submit protection.
- Never send while an IME is composing (`nativeEvent.isComposing` or the
  composition key code `229`), including Enter used to confirm Chinese text.
  Ignore repeated Enter keydown events to prevent an accidental second send.
- Empty drafts, a missing conversation, transcript loading, and pending
  conversation creation or sending must block both submission paths.
- Keep a visible shortcut hint associated with the textarea through
  `aria-describedby`. Interaction tests cover sending, multiline entry,
  composition confirmation, and blocked/duplicate submissions.
- After a valid send, align the latest question with the top of the visible
  conversation area after its turn is committed to the DOM. Leave enough room
  beneath a short pending turn for that alignment. Keep the question's position
  through the durable transcript handoff, including repeated identical questions.
- Render the answer below the question without repeatedly scrolling on answer
  deltas. Readers may scroll freely after positioning. Shift+Enter, IME
  confirmation, and blocked sends must not initiate scrolling. Use instant
  positioning to respect reduced motion.
- Opening a populated historical conversation, including the initial selected
  conversation and reopening its sidebar row, aligns its last question at the
  top once its transcript is ready. Keep every earlier turn in chronological
  order and accessible by scrolling upward. Cached and delayed transcripts
  share this behavior; empty or failed transcript loads do not scroll.
- Background transcript refreshes must not reset the reader's scroll position.
  A new history selection may position once again, including returning to a
  previously opened conversation. History positioning must not override an
  in-flight question or its pending-to-durable handoff.
- Test that the latest question is the scroll target, short turns can align at
  the top, streaming does not force scrolling, and the final handoff preserves
  the question's position without pulling readers back from a manual scroll.
  History tests verify last-question positioning while retaining all older
  turns, cached/async loading, reopening, and no refetch-driven scroll reset.

## Safe Markdown Answers

- AI answer Markdown is rendered by a feature-local presentation component,
  not with `dangerouslySetInnerHTML`.
- Keep raw HTML and optional Markdown extensions disabled. Override model-authored
  links and images to inert text; only the separate server-owned citation DTO
  may create clickable source anchors.
- Remove the deterministic appended text-source block before Markdown parsing,
  then render structured citation cards as adjacent semantic content.
- Citation cards show the server-owned title, link, and timestamp by default;
  subtitle/excerpt text remains inside a collapsed native `<details>` block and
  is expanded only by the user's keyboard or pointer action.
- Scope answer typography beneath `.chat-markdown`. Plain historical text must
  remain readable without migration.

---

## Common Mistakes

- Showing the first-empty Agent card during loading or error states.
- Treating a YouTube description as an AI summary.
- Rendering a retry button from lifecycle alone instead of `available_actions`.
- Setting the `open` attribute before calling `dialog.showModal()`, which creates a non-modal dialog.
- Inventing placeholder metadata such as “unknown author” for newly queued items.
