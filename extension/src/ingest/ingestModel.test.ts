import { describe, expect, it } from "vitest";

import type { IngestReason, IngestStage } from "../api/contracts";
import {
  allStagesSettled,
  bodyStageOf,
  isBodySearchable,
  isBodyUnsearchable,
  isUnfinished,
  normalizeStages,
  reasonOf,
  visibleStages,
} from "./ingestModel";

const NO_SPEECH: IngestReason = { code: "asr_no_speech", category: "no_speech", retry_useful: false };
const body = (status: IngestStage["status"], extra: Partial<IngestStage> = {}): IngestStage => ({ key: "body", status, ...extra });
const later = (key: string, status: IngestStage["status"], extra: Partial<IngestStage> = {}): IngestStage => ({ key, status, ...extra });

describe("visibleStages", () => {
  it("keeps the stages the server sends, body first, in the order of the stage table", () => {
    const shown = visibleStages({
      lifecycle: "ready",
      stages: [later("summary", "running"), later("classify", "done"), body("done")],
    });
    expect(shown.map((stage) => stage.key)).toEqual(["body", "classify", "summary"]);
  });

  it("drops stages this build does not know and stages the server marks skipped", () => {
    const shown = visibleStages({
      lifecycle: "ready",
      stages: [body("done"), later("classify", "skipped"), later("translation", "running"), later("movie", "pending")],
    });
    expect(shown.map((stage) => `${stage.key}:${stage.status}`)).toEqual(["body:done", "movie:pending"]);
  });

  it("keeps the failed reason of a stage", () => {
    const failed = later("summary", "failed", { reason: { code: "ingestion_failed", category: "error", retry_useful: true } });
    expect(visibleStages({ lifecycle: "ready", stages: [body("done"), failed] })[1]?.reason?.category).toBe("error");
  });

  it("does not change the stages it was given", () => {
    const stages = [later("summary", "pending"), body("done")];
    visibleStages({ lifecycle: "ready", stages });
    expect(stages.map((stage) => stage.key)).toEqual(["summary", "body"]);
  });

  describe("on a server that sends no stages", () => {
    it("derives the body stage from the lifecycle", () => {
      expect(visibleStages({ lifecycle: "queued" })).toEqual([{ key: "body", status: "pending", detail: "queued" }]);
      expect(visibleStages({ lifecycle: "ready" })).toEqual([{ key: "body", status: "done" }]);
      expect(visibleStages({ lifecycle: "archived" })).toEqual([{ key: "body", status: "done" }]);
      expect(visibleStages({ lifecycle: "failed", error_code: "asr_no_speech" })[0]).toMatchObject({ key: "body", status: "failed" });
      expect(visibleStages({ lifecycle: "needs_action" })[0]).toMatchObject({ key: "body", status: "failed" });
    });

    it("derives what a processing body is doing from the stage field", () => {
      expect(visibleStages({ lifecycle: "processing", stage: "fetching" })).toEqual([{ key: "body", status: "running", detail: "fetching" }]);
      expect(visibleStages({ lifecycle: "processing", stage: "transcribing" })[0]).toMatchObject({ status: "running", detail: "transcribing" });
      expect(visibleStages({ lifecycle: "processing", stage: "chunking" })[0]).toMatchObject({ detail: "indexing" });
      expect(visibleStages({ lifecycle: "processing", stage: "embedding" })[0]).toMatchObject({ detail: "indexing" });
      expect(visibleStages({ lifecycle: "processing" })[0]).toMatchObject({ status: "running", detail: "fetching" });
    });

    it("treats a lifecycle it does not know as still running instead of failing", () => {
      expect(visibleStages({ lifecycle: "teleporting" })).toEqual([{ key: "body", status: "running", detail: "fetching" }]);
    });

    it("treats an empty list like a missing one", () => {
      expect(visibleStages({ lifecycle: "ready", stages: [] })).toEqual([{ key: "body", status: "done" }]);
    });

    it("still shows later stages that arrive without a body stage", () => {
      const shown = visibleStages({ lifecycle: "ready", stages: [later("classify", "running")] });
      expect(shown.map((stage) => stage.key)).toEqual(["body", "classify"]);
    });
  });
});

describe("normalizeStages", () => {
  it("is idempotent", () => {
    const once = normalizeStages([later("summary", "done"), body("done"), later("x", "done"), later("movie", "skipped")]);
    expect(normalizeStages(once)).toEqual(once);
  });
});

describe("unfinished items and their reason", () => {
  it("is unfinished only when failed or needing action", () => {
    expect(isUnfinished({ lifecycle: "failed" })).toBe(true);
    expect(isUnfinished({ lifecycle: "needs_action" })).toBe(true);
    for (const lifecycle of ["queued", "processing", "ready", "archived", "teleporting"]) {
      expect(isUnfinished({ lifecycle })).toBe(false);
    }
  });

  it("takes the reason of the item, then the one of the body stage", () => {
    expect(reasonOf({ lifecycle: "failed", reason: NO_SPEECH })).toEqual(NO_SPEECH);
    expect(reasonOf({ lifecycle: "failed", stages: [body("failed", { reason: NO_SPEECH })] })).toEqual(NO_SPEECH);
  });

  it("has no reason for an item that is not unfinished, even when the server sends one", () => {
    expect(reasonOf({ lifecycle: "ready", reason: NO_SPEECH })).toBeNull();
    expect(reasonOf({ lifecycle: "processing" })).toBeNull();
  });

  it("falls back to a generic reason on a server that sends none", () => {
    expect(reasonOf({ lifecycle: "failed" })).toEqual({ code: "ingestion_failed", category: "error", retry_useful: true });
    expect(reasonOf({ lifecycle: "failed", error_code: "transient_fetch_failed" })).toMatchObject({ code: "transient_fetch_failed", category: "error" });
    expect(reasonOf({ lifecycle: "needs_action" })).toEqual({ code: "asr_unavailable", category: "no_transcription", retry_useful: true });
  });
});

describe("settling and searchability", () => {
  it("settles once every shown stage is done or failed", () => {
    expect(allStagesSettled({ lifecycle: "ready" })).toBe(true);
    expect(allStagesSettled({ lifecycle: "ready", stages: [body("done")] })).toBe(true);
    expect(allStagesSettled({ lifecycle: "ready", stages: [body("done"), later("classify", "done"), later("summary", "failed")] })).toBe(true);
    expect(allStagesSettled({ lifecycle: "ready", stages: [body("done"), later("classify", "running")] })).toBe(false);
    expect(allStagesSettled({ lifecycle: "ready", stages: [body("done"), later("classify", "pending")] })).toBe(false);
    expect(allStagesSettled({ lifecycle: "processing" })).toBe(false);
    expect(allStagesSettled({ lifecycle: "queued" })).toBe(false);
    expect(allStagesSettled({ lifecycle: "failed" })).toBe(true);
  });

  it("does not wait for a stage that is skipped or unknown", () => {
    expect(allStagesSettled({ lifecycle: "ready", stages: [body("done"), later("movie", "skipped"), later("translation", "running")] })).toBe(true);
  });

  it("reads the searchable flag from the server, or from the lifecycle on an old server", () => {
    expect(isBodySearchable({ lifecycle: "ready", body_searchable: true })).toBe(true);
    expect(isBodySearchable({ lifecycle: "archived", body_searchable: true })).toBe(true);
    expect(isBodySearchable({ lifecycle: "failed", body_searchable: false })).toBe(false);
    expect(isBodySearchable({ lifecycle: "ready" })).toBe(true);
    expect(isBodySearchable({ lifecycle: "needs_action" })).toBe(false);
  });

  it("marks a body as unsearchable only when nothing is about to make it searchable", () => {
    expect(isBodyUnsearchable({ lifecycle: "failed", body_searchable: false, stages: [body("failed", { reason: NO_SPEECH })] })).toBe(true);
    expect(isBodyUnsearchable({ lifecycle: "needs_action", body_searchable: false })).toBe(true);
    expect(isBodyUnsearchable({ lifecycle: "processing", body_searchable: false, stages: [body("running", { detail: "transcribing" })] })).toBe(false);
    expect(isBodyUnsearchable({ lifecycle: "queued", body_searchable: false })).toBe(false);
    expect(isBodyUnsearchable({ lifecycle: "ready", body_searchable: true })).toBe(false);
  });

  it("still marks an archived item whose body failed", () => {
    expect(isBodyUnsearchable({ lifecycle: "archived", body_searchable: false, stages: [body("failed", { reason: NO_SPEECH })] })).toBe(true);
    expect(isBodyUnsearchable({ lifecycle: "archived", body_searchable: true, stages: [body("done")] })).toBe(false);
  });

  it("decides by lifecycle on an old server, which has no flag and never marks an archived item", () => {
    expect(isBodyUnsearchable({ lifecycle: "failed" })).toBe(true);
    expect(isBodyUnsearchable({ lifecycle: "needs_action" })).toBe(true);
    expect(isBodyUnsearchable({ lifecycle: "ready" })).toBe(false);
    expect(isBodyUnsearchable({ lifecycle: "processing" })).toBe(false);
    expect(isBodyUnsearchable({ lifecycle: "archived" })).toBe(false);
  });

  it("finds the body stage of an item", () => {
    expect(bodyStageOf({ lifecycle: "ready", stages: [later("classify", "done"), body("done")] }).key).toBe("body");
    expect(bodyStageOf({ lifecycle: "queued" })).toMatchObject({ key: "body", status: "pending" });
  });
});
