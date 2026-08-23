# Content inventory, 2026-08-23

## Verified product surfaces

- Private Web library with login, saved-item inventory, save notes, search,
  item detail/transcript, archive/restore, retry, and batch URL save.
- Evidence-first Web conversations with citations, excerpts, and timestamp links.
- MCP over stdio and Streamable HTTP with tenant-bound scoped grants.
- Server-side YouTube and Bilibili ordinary-video ingestion connectors.
- Optional Chrome/Chromium browser companion for current-page YouTube and
  NTULearn/Kaltura captions, including explicit pairing and device revocation.
- Optional LangBot bridge for Telegram and WeChat, including cross-channel
  identity linking.
- Managed `read`, `full`, and `langbot` runtime profiles.

## Documentation gaps observed

- Root README starts with internal implementation capabilities before the user
  has a clear product picture.
- Root README does not give the browser companion a place in the product story.
- `docs/README.md`, getting-started, interface, integration, and several
  production indexes are English while the detailed operational content is
  mostly Chinese.
- `docs/deployment/README.md` is over a thousand lines and combines architecture,
  prerequisites, installation, configuration reference, deployment, verification,
  backup, rollback, and troubleshooting.
- `docs/getting-started/configuration.md` combines scenario recipes with an
  environment-variable dictionary.
- User-facing Web and browser-companion tasks are discoverable primarily through
  UI/package sources rather than an intentional docs path.

## Claim cautions

- Bilibili captions are only available when the server can access them without
  persisted account cookies; some videos require later browser capture support.
- Browser companion support is narrower than arbitrary authenticated websites:
  it is implemented for YouTube and NTULearn/Kaltura paths documented by the
  extension.
- ASR is not a generally available ingestion path and must not be described as
  shipped.
- LangBot, Telegram, WeChat, and the browser companion are optional components,
  not prerequisites for the core read-only MCP path.

## Recorded user-experience feedback

The archived `08-09-why-saved-collection-tags/context.md` contains direct user
review follow-ups from a live Web-library preview. Publishable, non-identifying
observations include:

- The URL entry was too large for its initial content. It was changed to a
  compact field whose confirmed URLs become removable, wrapping tags.
- Users wanted immediate matching against existing titles and authors while
  typing. Local suggestions were added without sending a request per keystroke.
- Ready videos and work-in-progress items needed a clearer hierarchy. They were
  separated into the readable library and a lower processing queue.
- The primary video count appeared inconsistent because it included work-queue
  items. Counts were changed to describe each visible region independently.
- A long source title visually overwhelmed its cover. Responsive title density
  and a better desktop media/text balance were added without truncation.
- The label “为什么保存” was changed to the shorter task-oriented “备注” after
  direct review feedback.

These changes were validated by focused component tests, complete frontend
checks, production builds, and browser smoke at desktop and approximately
390x844 mobile viewports. These are validation results, not additional user
feedback or satisfaction metrics.

## Other validation evidence

- The browser companion has a recorded 7/7 real-video recognition matrix:
  five YouTube pages and two authorized NTULearn/Kaltura entries, with opening,
  middle, and ending cue checks. Private transcript text and credentials were
  deliberately not retained.
- Human Agent evaluation and automated acceptance may support capability and
  quality claims, but must be labeled separately from target-user experience.

## Missing user-confirmed fact

The repository supports an origin story about people who save courses,
interviews, and talks but later remember only a vague idea. It does not identify
the actual initiating person or provide a publishable background. The user must
confirm whether this should be the project creator, an anonymized first user, or
a composite persona before the README can make a truthful person-level claim.
