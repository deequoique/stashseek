import type { IngestReason, IngestStage } from "../api/contracts";
import { stageRank } from "./ingestCopy";

/**
 * The fields of a library item the ingest status reads. A `LibraryItem` fits, and so does the
 * answer of a server that predates `stages`, `reason` and `body_searchable`. Values are plain
 * strings so a lifecycle or stage this build does not know can be passed without a cast.
 */
export interface IngestSource {
  lifecycle: string;
  stage?: string;
  stages?: readonly IngestStage[];
  reason?: IngestReason | null;
  body_searchable?: boolean;
  error_code?: string | null;
}

export const BODY_KEY = "body";

/** Reasons for an unfinished item whose server sends none. */
const LEGACY_FAILED_REASON: IngestReason = { code: "ingestion_failed", category: "error", retry_useful: true };
const LEGACY_NEEDS_ACTION_REASON: IngestReason = { code: "asr_unavailable", category: "no_transcription", retry_useful: true };

/** Lifecycle values that end with the body not searchable. */
export function isUnfinished(item: Pick<IngestSource, "lifecycle">): boolean {
  return item.lifecycle === "failed" || item.lifecycle === "needs_action";
}

/**
 * Why the body is not searchable, or null for an item that is not unfinished. Uses the server's
 * reason and falls back to a generic one for a server that does not send it.
 */
export function reasonOf(item: IngestSource): IngestReason | null {
  if (!isUnfinished(item)) return null;
  const body = item.stages?.find((stage) => stage.key === BODY_KEY);
  const reason = item.reason ?? body?.reason;
  if (reason) return reason;
  if (item.lifecycle === "needs_action") return LEGACY_NEEDS_ACTION_REASON;
  return item.error_code ? { ...LEGACY_FAILED_REASON, code: item.error_code } : LEGACY_FAILED_REASON;
}

/** The body stage of a server that sends no `stages`, read from the lifecycle and `stage`. */
function derivedBodyStage(item: IngestSource): IngestStage {
  switch (item.lifecycle) {
    case "queued":
      return { key: BODY_KEY, status: "pending", detail: "queued" };
    case "ready":
      return { key: BODY_KEY, status: "done" };
    // An archived item of an old server carries no hint of how far its body got.
    case "archived":
      return { key: BODY_KEY, status: "done" };
    case "failed":
    case "needs_action":
      return { key: BODY_KEY, status: "failed", reason: reasonOf(item) };
    default: {
      // "processing", and a lifecycle this build does not know: still in progress.
      const detail = item.stage === "transcribing"
        ? "transcribing"
        : item.stage === "chunking" || item.stage === "embedding" ? "indexing" : "fetching";
      return { key: BODY_KEY, status: "running", detail };
    }
  }
}

/** Keeps the stages this build knows, drops `skipped` ones, and orders them like the server's table. */
export function normalizeStages(stages: readonly IngestStage[]): IngestStage[] {
  return stages
    .filter((stage) => stageRank(stage.key) >= 0 && stage.status !== "skipped")
    .sort((a, b) => stageRank(a.key) - stageRank(b.key));
}

/**
 * The stages to show: known keys only, never `skipped`, body first. A server without `stages`
 * (or without a body stage) gets a body stage derived from `lifecycle` and `stage`.
 */
export function visibleStages(item: IngestSource): IngestStage[] {
  const stages = normalizeStages(item.stages ?? []);
  return stages.some((stage) => stage.key === BODY_KEY) ? stages : [derivedBodyStage(item), ...stages];
}

export function bodyStageOf(item: IngestSource): IngestStage {
  return visibleStages(item).find((stage) => stage.key === BODY_KEY) ?? derivedBodyStage(item);
}

/** Every shown stage has finished, successfully or not. */
export function allStagesSettled(item: IngestSource): boolean {
  return visibleStages(item).every((stage) => stage.status === "done" || stage.status === "failed");
}

/** The body is in the search index. A server that does not say is read from the lifecycle. */
export function isBodySearchable(item: IngestSource): boolean {
  return item.body_searchable ?? item.lifecycle === "ready";
}

/**
 * The body is not searchable and nothing is about to change that: the item is not waiting for
 * or running its body stage. An old server's answer decides by lifecycle alone.
 */
export function isBodyUnsearchable(item: IngestSource): boolean {
  if (item.body_searchable === undefined) return isUnfinished(item);
  if (item.body_searchable) return false;
  const body = bodyStageOf(item);
  return body.status !== "pending" && body.status !== "running";
}
