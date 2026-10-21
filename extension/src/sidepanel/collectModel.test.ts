import { describe, expect, it } from "vitest";

import { libraryItem } from "../test/sidepanelEnvironment";
import type { IngestReason, IngestStage } from "../api/contracts";
import { applyItem, type CollectEntry, entryStages, finishEntry, newCollectEntry, parseStored, progressSignature, restartTracking, toStored } from "./collectModel";

const T0 = 1_700_000_000_000;

function tracking(overrides: Partial<CollectEntry> = {}): CollectEntry {
  return { ...newCollectEntry("entry-1", "youtube:aaaaaaaaaaa", T0), phase: "tracking", path: "url", itemPublicId: "item-1", ...overrides };
}

describe("applyItem", () => {
  it("records the first stage and uses the server's start time once, clamped to the past", () => {
    const next = applyItem(tracking(), libraryItem({ stage: "fetching", stage_updated_at: new Date(T0 - 20_000).toISOString() }), T0 + 3_000);
    expect(next.stage).toBe("fetching");
    expect(next.stageSince).toBe(T0 - 20_000);
    expect(next.workingStage).toBe("fetching");
    expect(next.phase).toBe("tracking");
  });

  it("ignores a server time in the future (clock skew) or a stale one", () => {
    expect(applyItem(tracking(), libraryItem({ stage_updated_at: new Date(T0 + 60_000).toISOString() }), T0 + 1_000).stageSince).toBe(T0 + 1_000);
    expect(applyItem(tracking(), libraryItem({ stage_updated_at: new Date(T0 - 7 * 3_600_000).toISOString() }), T0).stageSince).toBe(T0);
    expect(applyItem(tracking(), libraryItem({ stage_updated_at: null }), T0 + 5).stageSince).toBe(T0 + 5);
  });

  it("starts later stages when the panel sees the change, not at the dispatch time", () => {
    const first = applyItem(tracking(), libraryItem({ stage: "fetching" }), T0 + 1_000);
    const again = applyItem(first, libraryItem({ stage: "fetching" }), T0 + 4_000);
    expect(again.stageSince).toBe(first.stageSince);
    const chunking = applyItem(again, libraryItem({ stage: "chunking", stage_updated_at: new Date(T0 - 90_000).toISOString() }), T0 + 9_000);
    expect(chunking.stageSince).toBe(T0 + 9_000);
    expect(chunking.workingStage).toBe("chunking");
  });

  it("remembers that speech transcription ran", () => {
    const transcribing = applyItem(tracking(), libraryItem({ stage: "transcribing" }), T0);
    expect(transcribing.sawTranscribing).toBe(true);
    expect(applyItem(transcribing, libraryItem({ stage: "chunking" }), T0 + 1).sawTranscribing).toBe(true);
  });

  it("takes the duration and the retry action from the item", () => {
    const next = applyItem(tracking(), libraryItem({ duration_sec: 1234, available_actions: ["archive", "retry"] }), T0);
    expect(next.durationSec).toBe(1234);
    expect(next.retryable).toBe(true);
    expect(applyItem(tracking({ durationSec: 99 }), libraryItem({ duration_sec: null }), T0).durationSec).toBe(99);
  });

  it("ends on ready, needs_action, failed and archived, and labels a pre-existing ready item", () => {
    expect(applyItem(tracking(), libraryItem({ lifecycle: "ready", stage: "ready" }), T0)).toMatchObject({ phase: "done", outcome: "ready" });
    expect(applyItem(tracking({ existing: true }), libraryItem({ lifecycle: "ready", stage: "ready" }), T0)).toMatchObject({ phase: "done", outcome: "already_exists" });
    expect(applyItem(tracking(), libraryItem({ lifecycle: "needs_action", stage: "needs_action" }), T0)).toMatchObject({ phase: "done", outcome: "needs_action" });
    expect(applyItem(tracking(), libraryItem({ lifecycle: "failed", stage: "failed", error_code: "ingestion_failed" }), T0))
      .toMatchObject({ phase: "done", outcome: "failed", errorCode: "ingestion_failed" });
    expect(applyItem(tracking(), libraryItem({ lifecycle: "archived", stage: "archived" }), T0)).toMatchObject({ phase: "done", outcome: "archived" });
  });

  it("keeps the last working stage when the item ends", () => {
    const working = applyItem(tracking(), libraryItem({ stage: "embedding" }), T0);
    const failed = applyItem(working, libraryItem({ lifecycle: "failed", stage: "failed", error_code: "ingestion_failed" }), T0 + 10);
    expect(failed.workingStage).toBe("embedding");
    expect(failed.stage).toBe("failed");
  });

  it("derives a stage from the lifecycle on a server without the stage field", () => {
    const legacy: Partial<ReturnType<typeof libraryItem>> = libraryItem({ lifecycle: "processing" });
    delete legacy.stage;
    expect(applyItem(tracking({ path: "url" }), legacy as ReturnType<typeof libraryItem>, T0).stage).toBe("fetching");
    expect(applyItem(tracking({ path: "capture" }), legacy as ReturnType<typeof libraryItem>, T0).stage).toBe("chunking");
    expect(applyItem(tracking(), libraryItem({ lifecycle: "queued", stage: undefined }), T0).stage).toBe("queued");
  });
});

const NO_SPEECH: IngestReason = { code: "asr_no_speech", category: "no_speech", retry_useful: false };
const body = (status: IngestStage["status"], extra: Partial<IngestStage> = {}): IngestStage => ({ key: "body", status, ...extra });
const later = (key: string, status: IngestStage["status"]): IngestStage => ({ key, status });
const ready = (...stages: IngestStage[]) => libraryItem({ lifecycle: "ready", stage: "ready", stages: [body("done"), ...stages], body_searchable: true });

describe("applyItem reads the ingest status", () => {
  it("keeps the stages, the title and the platform the server reports", () => {
    const stages = [body("running", { detail: "fetching" }), later("classify", "pending")];
    const next = applyItem(tracking(), libraryItem({ stages, title: "Real title", platform: "bilibili" }), T0);
    expect(next.stages).toEqual(stages);
    expect(next.itemTitle).toBe("Real title");
    expect(next.itemPlatform).toBe("bilibili");
    expect(next.bodySearchable).toBe(false);
    expect(next.reason).toBeNull();
  });

  it("keeps the title it already had when the server sends none", () => {
    expect(applyItem(tracking({ itemTitle: "Earlier" }), libraryItem({ title: null }), T0).itemTitle).toBe("Earlier");
  });

  it("shows only the stages this build knows and the server did not skip", () => {
    const next = applyItem(tracking(), libraryItem({ stages: [body("running", { detail: "fetching" }), later("classify", "skipped"), later("translation", "running")] }), T0);
    expect(next.stages.map((stage) => stage.key)).toEqual(["body"]);
  });

  it("derives the stages of a server that sends none", () => {
    const next = applyItem(tracking(), libraryItem({ lifecycle: "processing", stage: "chunking" }), T0);
    expect(next.stages).toEqual([{ key: "body", status: "running", detail: "indexing" }]);
  });

  it("keeps the reason of an item that did not finish, and the searchable flag", () => {
    const failed = applyItem(tracking(), libraryItem({
      lifecycle: "failed", stage: "failed", error_code: "asr_no_speech", reason: NO_SPEECH, stages: [body("failed", { reason: NO_SPEECH })], body_searchable: false,
    }), T0);
    expect(failed).toMatchObject({ phase: "done", outcome: "failed", reason: NO_SPEECH, bodySearchable: false });

    const needsAction = applyItem(tracking(), libraryItem({ lifecycle: "needs_action", stage: "needs_action", reason: { code: "asr_disabled", category: "no_transcription", retry_useful: true } }), T0);
    expect(needsAction).toMatchObject({ outcome: "needs_action", reason: { category: "no_transcription" } });
  });

  it("gives a server that sends no reason a generic one", () => {
    expect(applyItem(tracking(), libraryItem({ lifecycle: "failed", stage: "failed", error_code: "transient_fetch_failed" }), T0).reason)
      .toMatchObject({ code: "transient_fetch_failed", category: "error" });
  });

  it("clears the reason once the item is no longer unfinished", () => {
    const failed = applyItem(tracking(), libraryItem({ lifecycle: "failed", stage: "failed", reason: NO_SPEECH }), T0);
    expect(applyItem(failed, libraryItem({ lifecycle: "queued", stage: "queued" }), T0 + 1).reason).toBeNull();
  });

  it("reports transcription of a URL import, where the stage field still says fetching", () => {
    const next = applyItem(tracking(), libraryItem({ stage: "fetching", stages: [body("running", { detail: "transcribing" })] }), T0);
    expect(next.stage).toBe("transcribing");
    expect(next.workingStage).toBe("transcribing");
    expect(next.sawTranscribing).toBe(true);
  });
});

describe("applyItem when the body is ready", () => {
  it("ends at once for an item without later stages", () => {
    expect(applyItem(tracking(), ready(), T0)).toMatchObject({ phase: "done", outcome: "ready", bodySearchable: true });
    expect(applyItem(tracking(), libraryItem({ lifecycle: "ready", stage: "ready" }), T0)).toMatchObject({ phase: "done", outcome: "ready" });
  });

  it("keeps following while a later stage has not finished", () => {
    const running = applyItem(tracking(), ready(later("classify", "running"), later("summary", "pending")), T0);
    expect(running).toMatchObject({ phase: "tracking", outcome: null, bodySearchable: true, stage: "ready" });
    expect(running.stages.map((stage) => `${stage.key}:${stage.status}`)).toEqual(["body:done", "classify:running", "summary:pending"]);
  });

  it("ends once every later stage is done or failed, whichever way they ended", () => {
    const running = applyItem(tracking(), ready(later("classify", "running"), later("summary", "pending")), T0);
    expect(applyItem(running, ready(later("classify", "done"), later("summary", "running")), T0 + 1).phase).toBe("tracking");
    expect(applyItem(running, ready(later("classify", "done"), later("summary", "failed")), T0 + 2)).toMatchObject({ phase: "done", outcome: "ready" });
    expect(applyItem(tracking({ existing: true }), ready(later("classify", "failed")), T0)).toMatchObject({ phase: "done", outcome: "already_exists" });
  });

  it("waits for the later stages of a new collect, but not of an item that was already in the library", () => {
    const unsettled = ready(later("classify", "running"), later("summary", "pending"));
    expect(applyItem(tracking({ existing: false }), unsettled, T0)).toMatchObject({ phase: "tracking", outcome: null });
    expect(applyItem(tracking({ existing: true }), unsettled, T0)).toMatchObject({ phase: "done", outcome: "already_exists", bodySearchable: true });
    // Items saved before later stages existed have no stage rows: they read as pending, and would never settle.
    const legacy = ready(later("classify", "pending"), later("summary", "pending"));
    expect(applyItem(tracking({ existing: true }), legacy, T0)).toMatchObject({ phase: "done", outcome: "already_exists" });
    expect(applyItem(tracking({ existing: false }), legacy, T0).phase).toBe("tracking");
  });

  it("still follows an existing item until its body is ready", () => {
    const processing = libraryItem({ stage: "chunking", stages: [{ key: "body", status: "running", detail: "indexing" }, later("classify", "pending")] });
    const waiting = applyItem(tracking({ existing: true }), processing, T0);
    expect(waiting).toMatchObject({ phase: "tracking", outcome: null });
    expect(applyItem(waiting, ready(later("classify", "pending")), T0 + 1)).toMatchObject({ phase: "done", outcome: "already_exists" });
  });

  it("ends on failed, needs_action and archived without waiting for any stage", () => {
    expect(applyItem(tracking(), libraryItem({ lifecycle: "failed", stage: "failed", stages: [body("failed", { reason: NO_SPEECH }), later("classify", "skipped")] }), T0).phase).toBe("done");
    expect(applyItem(tracking(), libraryItem({ lifecycle: "archived", stage: "archived", stages: [body("done"), later("classify", "pending")] }), T0)).toMatchObject({ phase: "done", outcome: "archived" });
  });
});

describe("restartTracking and the stage list", () => {
  it("starts an unfinished entry over: same item, new clock, nothing left from the last run", () => {
    const failed = applyItem(tracking({ durationSec: 600, itemTitle: "t" }), libraryItem({
      lifecycle: "failed", stage: "failed", error_code: "asr_no_speech", reason: NO_SPEECH, available_actions: ["retry"], title: null,
    }), T0);
    const again = restartTracking(failed, T0 + 99);
    expect(again).toMatchObject({
      id: failed.id, itemPublicId: "item-1", phase: "tracking", path: "url", startedAt: T0 + 99, durationSec: 600, itemTitle: "t",
      outcome: null, errorCode: null, reason: null, retryable: false, stage: null, stages: [], retryState: "idle", existing: false,
    });
  });

  it("draws an item that was not read yet as waiting in the queue", () => {
    expect(entryStages(tracking())).toEqual([{ key: "body", status: "pending", detail: "queued" }]);
    const stages = [body("running", { detail: "fetching" })];
    expect(entryStages(tracking({ stages }))).toEqual(stages);
  });

  it("changes its progress signature when a later stage moves, not only the item stage", () => {
    const first = applyItem(tracking(), ready(later("classify", "running")), T0);
    const second = applyItem(first, ready(later("classify", "done"), later("summary", "running")), T0 + 1);
    expect(second.stage).toBe(first.stage);
    expect(progressSignature(second)).not.toBe(progressSignature(first));
    expect(progressSignature(applyItem(first, ready(later("classify", "running")), T0 + 2))).toBe(progressSignature(first));
  });
});

describe("persisted entries", () => {
  it("round-trips an unfinished entry without the transient retry flag", () => {
    const entry = tracking({ stage: "chunking", stageSince: T0 + 5, workingStage: "chunking", sawTranscribing: true, segments: 12, durationSec: 600, fallback: "no_captions", retrying: true });
    const stored = toStored(entry);
    expect(stored).not.toHaveProperty("retrying");
    expect(parseStored(JSON.parse(JSON.stringify(stored)))).toEqual({ ...entry, retrying: false });
  });

  it("never carries caption text", () => {
    expect(Object.keys(toStored(tracking())).sort()).toEqual([
      "bodySearchable", "captionless", "durationSec", "errorCode", "existing", "fallback", "id", "itemPlatform", "itemPublicId",
      "itemTitle", "kind", "outcome", "path", "phase", "reason", "retryable", "sawTranscribing", "segments", "stage", "stageSince",
      "stages", "startedAt", "videoKey", "workingStage",
    ]);
  });

  it("round-trips the ingest status of an entry that is waiting for later stages", () => {
    const entry = applyItem(tracking(), ready(later("classify", "running")), T0);
    const restored = parseStored(JSON.parse(JSON.stringify(toStored(entry))));
    expect(restored).toMatchObject({
      phase: "tracking", bodySearchable: true, itemTitle: "Video aaaaaaaaaaa", itemPlatform: "youtube", stage: "ready",
      stages: [{ key: "body", status: "done" }, { key: "classify", status: "running" }],
    });
  });

  it("keeps the reason of the entry and of its stages through a reload", () => {
    const entry = { ...tracking(), reason: NO_SPEECH, stages: [body("failed", { reason: NO_SPEECH })], itemTitle: "t", itemPlatform: "bilibili" };
    expect(parseStored(JSON.parse(JSON.stringify(toStored(entry))))).toMatchObject({
      reason: NO_SPEECH, stages: [{ key: "body", status: "failed", reason: NO_SPEECH }], itemTitle: "t", itemPlatform: "bilibili",
    });
  });

  it("gives an entry stored before the ingest status existed empty defaults", () => {
    const old: Partial<ReturnType<typeof toStored>> = toStored(tracking());
    for (const key of ["stages", "reason", "bodySearchable", "itemTitle", "itemPlatform"] as const) delete old[key];
    expect(parseStored(old)).toMatchObject({ stages: [], reason: null, bodySearchable: false, itemTitle: null, itemPlatform: null });
  });

  it("drops stage and reason values that are not well formed", () => {
    const restored = parseStored({
      ...toStored(tracking()),
      stages: [null, "x", { key: 3, status: "done" }, { key: "body", status: "paused" }, { key: "body", status: "running", detail: "warp" }, { key: "classify", status: "done", reason: { code: 1 } }],
      reason: { code: "x", category: "mystery", retry_useful: true },
    });
    expect(restored?.stages).toEqual([
      { key: "body", status: "running", detail: null, reason: null },
      { key: "classify", status: "done", detail: null, reason: null },
    ]);
    // A category this build does not know reads as a plain error.
    expect(restored?.reason).toEqual({ code: "x", category: "error", retry_useful: true });
    expect(parseStored({ ...toStored(tracking()), stages: "body", reason: "no" })).toMatchObject({ stages: [], reason: null });
  });

  it("rejects anything that is not an entry being followed", () => {
    expect(parseStored(null)).toBeNull();
    expect(parseStored("x")).toBeNull();
    expect(parseStored({})).toBeNull();
    expect(parseStored({ ...toStored(tracking()), itemPublicId: null })).toBeNull();
    expect(parseStored({ ...toStored(tracking()), phase: "done" })).toBeNull();
    expect(parseStored({ ...toStored(tracking()), path: "teleport" })).toBeNull();
    expect(parseStored({ ...toStored(tracking()), startedAt: "yesterday" })).toBeNull();
  });

  it("drops unknown stage, fallback and working-stage values instead of trusting them", () => {
    const restored = parseStored({ ...toStored(tracking()), stage: "warp", fallback: "because", workingStage: "ready" });
    expect(restored).toMatchObject({ stage: null, fallback: null, workingStage: null });
  });
});

describe("finishEntry", () => {
  it("ends the entry and keeps an earlier error code when none is given", () => {
    const ended = finishEntry({ ...tracking(), errorCode: "x", retrying: true }, "timeout");
    expect(ended).toMatchObject({ phase: "done", outcome: "timeout", errorCode: "x", retrying: false });
  });
});
