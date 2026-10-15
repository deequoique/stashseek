# Bilibili content-observation draft (non-freezing evidence)

Date: 2026-08-25 (Asia/Singapore)

Status: **non-freezing evidence only**. This note does not replace the
benchmark catalog, does not freeze truth, and must not be used to authorize a
provider run. It records one bounded, public browser observation of BI-01 and
the already-checked-in metadata/formal-probe facts for BI-02 through BI-06.

## Scope and evidence boundary

- No network or media inspection was performed for this reduced deliverable.
- The BI-01 observation below came from the earlier public, cookie-free browser
  session. The player was loaded for P1 only; no login, cookie, subtitle
  download, media download, or provider call was used.
- The browser displayed frames in memory only. No screenshot, video, audio,
  subtitle body, page response, signed URL, or provider response was written
  to the repository or a temporary media directory.
- BI-02..BI-06 statements are **metadata-only** and come from the existing
  `sample-metadata-subtitle-probe-2026-08-25.md` and checked-in
  `evals/video_recognition/catalog.yaml`. They are not visual or audio
  verification.
- The earlier metadata probe cross-checked Bilibili `view`/`player v2`; the
  checked-in formal connector snapshot records yt-dlp metadata. Both safe
  projections report no platform subtitle track for BI-01..BI-06. That is
  distinct from hard subtitles burned into video frames.

## BI-01 browser observation

Observed public page: `BI-01` / `BV1bx411M7Zx`, P1, player duration displayed as
19:13 (the player clock reported approximately 1152.853 seconds).

At an in-player seek of approximately 00:30, the visible frame showed a black
background with an animated stylized character, a pixel/grid rendering of the
digit 3, and comparison examples of the same digit. A bilingual hard-subtitle
line was visible at the bottom of the player in Chinese and English. This
confirms, for the observed frame, an explanatory animation/diagram scene and
the presence of burned-in bilingual text.

At a later observed player position of approximately 01:43, the frame showed
an animated multi-layer node/edge diagram with numbered output nodes. A
Chinese/English hard-subtitle line was again visible at the bottom. This
confirms that the hard-subtitle observation was not limited to one initial
frame and that the observed visual content is consistent with a narrated
technical animation.

What remains unverified for BI-01: the audio language (the browser observation
read visible text but did not produce a transcript), whole-video hard-subtitle
coverage, exact transcript wording, and whether either observed position is a
representative or difficult ASR window. The existing metadata classification
(`content_languages: [en, zh]`, course/animation/slides) is retained as
metadata evidence, not upgraded to a frozen whole-video label.

## Metadata/formal-probe facts for BI-02..BI-06

The local probe confirms all five pages were publicly accessible, single-video
P1 records on 2026-08-25, with `probe_status: no_platform_track`,
`subtitle_coverage_status: none`, and `hard_subtitle_status: unknown`.
The following language/content labels are the catalog's preliminary metadata
classification only:

| Sample | Duration | Metadata-only language label | Metadata-only content/visual label | Platform subtitle probe | Rights note |
| --- | ---: | --- | --- | --- | --- |
| BI-02 | 2128 s | `zh`, `en` (code-switch label) | software screen recording, code, UI | no platform track; hard subtitles unknown | `uncertain_user_requested` |
| BI-03 | 1971 s | `zh` | course, algorithm, code, UI | no platform track; hard subtitles unknown | `uncertain_user_requested` |
| BI-04 | 1134 s | `zh` | game/simulation screen recording, dense UI, numbers | no platform track; hard subtitles unknown | `uncertain_user_requested` |
| BI-05 | 295 s | `zh` | music plus film montage, visual semantics, rights risk | no platform track; hard subtitles unknown | `uncertain_user_requested` |
| BI-06 | 153 s | `zh` | cooking, hands-on real-world action | no platform track; hard subtitles unknown | `uncertain_user_requested` |

These rows do not establish that the spoken language, on-screen text, or
visual class is present throughout the video. They also do not establish that
the absence of a platform track means absence of burned-in subtitles.

## Explicit unverified matrix

| Sample | Language truth | Content/visual truth | Hard-subtitle truth | ASR windows | Frame bundles | Query truth |
| --- | --- | --- | --- | --- | --- | --- |
| BI-01 | **Unverified for audio**; visible sampled text was bilingual zh/en | **Partially observed only**: technical animation/diagram at ~00:30 and ~01:43; whole-video label unverified | **Present on the two observed frames**; whole-video coverage and persistence unverified | **Unselected**; no normal/difficult 30–60 s windows frozen | **Unselected**; no 3–6-frame bundles frozen | **Unverified**; no query drafted here |
| BI-02 | Metadata-only `zh` + `en`; spoken mix unverified | Metadata-only software/UI/code label; visual scenes unverified | Unknown; no platform track is not a hard-subtitle result | **Unselected**; no windows proposed | **Unselected**; no bundles proposed | **Unverified**; no query drafted |
| BI-03 | Metadata-only `zh`; spoken language unverified | Metadata-only algorithm/course/code/UI label; visual scenes unverified | Unknown; no platform track is not a hard-subtitle result | **Unselected**; no windows proposed | **Unselected**; no bundles proposed | **Unverified**; no query drafted |
| BI-04 | Metadata-only `zh`; spoken language unverified | Metadata-only game/simulation/dense-UI label; visual scenes unverified | Unknown; no platform track is not a hard-subtitle result | **Unselected**; no windows proposed | **Unselected**; no bundles proposed | **Unverified**; no query drafted |
| BI-05 | Metadata-only `zh`; speech versus music balance unverified | Metadata-only film-montage/music label; sampled scene semantics unverified | Unknown; no platform track is not a hard-subtitle result | **Unselected**; no windows proposed | **Unselected**; no bundles proposed | **Unverified**; no query drafted |
| BI-06 | Metadata-only `zh`; spoken language and speech amount unverified | Metadata-only cooking/action label; sampled actions unverified | Unknown; no platform track is not a hard-subtitle result | **Unselected**; no windows proposed | **Unselected**; no bundles proposed | **Unverified**; no query drafted |

## Deliberate omissions

This reduced note intentionally contains no frozen time ranges, source hashes,
selection descriptors, transcripts, key terms, distractors, or search queries.
Those fields require bounded observation of each sample and a separate manual
review. Inventing them from titles, authors, descriptions, or durations would
create metadata leakage and would violate the benchmark's truth-freeze gate.

## Local evidence used

- `.trellis/tasks/08-24-video-recognition-benchmark/research/sample-metadata-subtitle-probe-2026-08-25.md`
- `evals/video_recognition/catalog.yaml` (read-only; not modified)
- Earlier public browser observation of BI-01 (not persisted as media)
