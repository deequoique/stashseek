# YouTube content observation draft (non-freezing)

Date: 2026-08-25 (Asia/Singapore)

Scope: YT-01 through YT-06 only.

This is a bounded review note, not a `benchmark-v1` freeze. It does not change
`evals/video_recognition/catalog.yaml`, the probe snapshot, the catalog
revision/status, or any production code. The catalog's `classification_status`
and `hard_subtitle_status` values must remain as checked in until a separate
manual truth review is completed.

## Evidence method and limits

- I cross-checked the checked-in sample metadata and subtitle projections in
  `evals/video_recognition/catalog.yaml` and
  `evals/video_recognition/probe_snapshot.yaml`.
- I used the canonical public YouTube watch pages in the selected sample list,
  navigated to a small number of requested playback positions, and read the
  visible DOM player state plus a screenshot after a bounded wait. The times
  below are the player times displayed in those observations; they are sample
  points, not audio-window boundaries or frame-bundle definitions.
- No full video, audio, subtitle body, transcript export, provider response, or
  model call was used or persisted. No media bytes were written to the
  repository or a task research file.
- “Rendered text observed” means text was visible in the video viewport while
  the sampled player controls reported that captions were unavailable. This is
  evidence of on-screen text in the sampled frames, but it does not establish a
  whole-video hard-subtitle status or distinguish every possible renderer. The
  catalog/probe `hard_subtitle_status: unknown` values therefore remain the
  authoritative pending state.
- The browser observations do not independently establish complete audio
  language, full-video content, cue coverage, transcript wording, or exact
  scene boundaries. The formal probe's platform-track results are metadata/body
  projections only and are not hard-subtitle evidence.

## Per-sample observations

### YT-01

- Catalog/probe cross-check: public single video, 268 seconds; English
  original-language automatic track with complete projected coverage; catalog
  languages are `en`, `zh`; preliminary tags are course/slides/machine-learning.
- Observed player times: `0:30` and `3:12`.
- At both sampled points the viewport showed a talking-head lecturer seated in
  front of a monitor containing a machine-learning course slide. English and
  Chinese lines were visible across the lower part of the video. This supports
  an English-led lecture/course with slide-backed educational visuals and
  bilingual rendered text in the sampled frames.
- Hard-text observation: rendered bilingual text was visible at both samples;
  whole-video hard-subtitle presence remains unverified.
- Not established: exact spoken transcript, normal/difficult ASR boundaries,
  representative frame-bundle boundaries, or query time truth.

### YT-02

- Catalog/probe cross-check: public single video, 2,887 seconds; no platform
  subtitle track; catalog language is `zh`; preliminary tags are
  interview/documentary/multi-speaker/long-video.
- Observed player time: `0:47` (the player initially showed a short loading
  state, then displayed the frame and time).
- The viewport showed a red archival/book slide with two portrait images and an
  English quotation, plus a Chinese line at the bottom. This supports a
  Mandarin-led documentary/interview presentation using archival stills and
  text slides. The sampled frame is not sufficient to classify every later
  segment or confirm the number of speakers.
- Hard-text observation: a Chinese line was visibly rendered in the sampled
  video frame while no platform caption track was available; whole-video
  hard-subtitle presence remains unverified.
- Not established: audio language beyond the sampled visible Chinese text,
  normal/difficult ASR boundaries, representative frame-bundle boundaries, or
  query time truth.

### YT-03

- Catalog/probe cross-check: public single video, 568 seconds; English
  original-language automatic track with complete projected coverage; catalog
  language is `en`; preliminary tags are software/screen-recording/code/UI.
- Observed player times: `0:32` and `5:50`.
- At `0:32` the viewport showed a Godot editor node-creation dialog and a
  shape-related technical caption. At `5:50` it showed a code editor with a
  Godot input-event function and surrounding code. English and Chinese lines
  were visible at both samples. This supports an English software/tutorial
  screen recording with code and UI as primary visual evidence.
- Hard-text observation: rendered bilingual text was visible at both samples;
  whole-video hard-subtitle presence remains unverified.
- Not established: exact code transcription, normal/difficult ASR boundaries,
  representative frame-bundle boundaries, or query time truth.

### YT-04

- Catalog/probe cross-check: public single video, 4,284 seconds; English and
  Japanese manual tracks with partial projected coverage; catalog languages are
  `zh`, `en`; preliminary tags are documentary/interview/charts/real-world.
- Observed player times: `3:02` and `25:02`.
- At `3:02` the viewport showed a busy airport concourse with travelers,
  luggage, and directional signs including `DEF` and `ABC`. At `25:02` it
  showed an outdoor/forest scene with shoes and travel/camp equipment. English
  and Chinese lines were visible at both samples. These samples support a
  documentary with real-world travel/field footage and bilingual rendered text;
  they do not by themselves verify the catalog's chart tag.
- Hard-text observation: rendered bilingual text was visible at both samples;
  whole-video hard-subtitle presence remains unverified.
- Not established: exact language mix of the audio, normal/difficult ASR
  boundaries, representative frame-bundle boundaries, chart locations, or
  query time truth.

### YT-05

- Catalog/probe cross-check: public single video, 501 seconds; no platform
  subtitle track; catalog language is `zh`; preliminary tags are live-music,
  noise, non-speech, and real-world.
- Observed player times: `0:32` and `5:02`.
- At `0:32` the viewport showed a singer with an acoustic guitar and microphone
  in a dim, intimate stage setting. At `5:02` it showed the performer bent over
  the guitar in the same live-performance environment. No on-screen caption or
  other persistent subtitle text was visible in either sampled frame.
- Hard-text observation: no rendered subtitle text was observed at the two
  samples; this is not proof that the entire performance lacks hard subtitles.
- Not established: exact lyric/spoken language, speech versus singing content
  inside either 30–60 second window, normal/difficult ASR boundaries,
  representative frame-bundle boundaries, or query time truth.

### YT-06

- Catalog/probe cross-check: public single video, 213 seconds; English manual
  and original-language automatic tracks with complete projected coverage;
  catalog language is `en`; preliminary tags are commercial-music-video,
  singing, people, and scene-changes.
- Observed player times: `0:32` and `2:12`.
- At `0:32` the viewport showed a male singer in sunglasses walking outdoors,
  with bilingual lyric lines rendered over the image. At `2:12` it showed a
  silhouette/shadow on the ground and no lyric line in the sampled frame. This
  supports an English commercial music video with singing, people, outdoor
  imagery, and scene changes.
- Hard-text observation: rendered bilingual lyrics were visible at `0:32`,
  while no lyric text was visible at `2:12`; this does not establish a
  whole-video hard-subtitle status.
- Not established: exact lyric transcript, normal/difficult ASR boundaries,
  representative frame-bundle boundaries, or query time truth.

## Still-unverified freeze inputs

Every row still requires the benchmark's two 30–60 second ASR windows (one
normal and one difficult), two frame bundles of 3–6 timestamps, and a separate
manual query-truth review. The observations above are not selections and must
not be copied into the catalog as frozen plans.

| Sample | Bounded visual observations only | ASR window 1 | ASR window 2 | Frame bundle 1 | Frame bundle 2 | Query truth and time ranges |
| --- | --- | --- | --- | --- | --- | --- |
| YT-01 | Player samples at 0:30 and 3:12 | Unverified; not selected | Unverified; not selected | Unverified; not selected | Unverified; not selected | No query text, expected ID, key terms, distractors, or acceptable range frozen |
| YT-02 | Player sample at 0:47 | Unverified; not selected | Unverified; not selected | Unverified; not selected | Unverified; not selected | No query text, expected ID, key terms, distractors, or acceptable range frozen |
| YT-03 | Player samples at 0:32 and 5:50 | Unverified; not selected | Unverified; not selected | Unverified; not selected | Unverified; not selected | No query text, expected ID, key terms, distractors, or acceptable range frozen |
| YT-04 | Player samples at 3:02 and 25:02 | Unverified; not selected | Unverified; not selected | Unverified; not selected | Unverified; not selected | No query text, expected ID, key terms, distractors, or acceptable range frozen |
| YT-05 | Player samples at 0:32 and 5:02 | Unverified; not selected | Unverified; not selected | Unverified; not selected | Unverified; not selected | No query text, expected ID, key terms, distractors, or acceptable range frozen |
| YT-06 | Player samples at 0:32 and 2:12 | Unverified; not selected | Unverified; not selected | Unverified; not selected | Unverified; not selected | No query text, expected ID, key terms, distractors, or acceptable range frozen |

No selection descriptor or source hash was computed. No transcript, frame
bundle, audio window, query, or provider result is asserted by this note.
