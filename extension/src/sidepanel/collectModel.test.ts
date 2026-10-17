import { describe, expect, it } from "vitest";

import { libraryItem } from "../test/sidepanelEnvironment";
import { applyItem, type CollectEntry, finishEntry, newCollectEntry, parseStored, toStored } from "./collectModel";

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

describe("persisted entries", () => {
  it("round-trips an unfinished entry without the transient retry flag", () => {
    const entry = tracking({ stage: "chunking", stageSince: T0 + 5, workingStage: "chunking", sawTranscribing: true, segments: 12, durationSec: 600, fallback: "no_captions", retrying: true });
    const stored = toStored(entry);
    expect(stored).not.toHaveProperty("retrying");
    expect(parseStored(JSON.parse(JSON.stringify(stored)))).toEqual({ ...entry, retrying: false });
  });

  it("never carries caption text", () => {
    expect(Object.keys(toStored(tracking())).sort()).toEqual([
      "captionless", "durationSec", "errorCode", "existing", "fallback", "id", "itemPublicId", "kind", "outcome",
      "path", "phase", "retryable", "sawTranscribing", "segments", "stage", "stageSince", "startedAt", "videoKey", "workingStage",
    ]);
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
