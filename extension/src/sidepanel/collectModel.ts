import type { LibraryItem } from "../api/contracts";

/** Processing stage reported by the library item projection. */
export type ItemStage = NonNullable<LibraryItem["stage"]>;
export type WorkingStage = "queued" | "fetching" | "transcribing" | "chunking" | "embedding";
export type CollectPath = "capture" | "url";
export type CollectPhase = "reading" | "submitting" | "tracking" | "done";

/** Why a click on 「收藏视频」 used the server URL import instead of the browser captions. */
export type FallbackReason = "read_failed" | "no_captions" | "not_paired" | "capture_failed";

export type CollectOutcome =
  /** The item reached an end state. */
  | "ready"
  | "already_exists"
  | "needs_action"
  | "failed"
  | "archived"
  /** The item was queued but could not be followed, or polling stopped after the cap. */
  | "submitted"
  | "timeout"
  /** Nothing was queued. */
  | "quota_exceeded"
  | "unsupported_url"
  /** A Bilibili part after the first: the server cannot import it by URL. */
  | "unsupported_part"
  | "submit_failed";

/**
 * One 「收藏视频」 action in the panel thread. It never holds caption text; only counts and the
 * item reference needed to resume progress after the panel is reopened.
 */
export interface CollectEntry {
  kind: "collect";
  id: string;
  videoKey: string;
  phase: CollectPhase;
  path: CollectPath | null;
  fallback: FallbackReason | null;
  itemPublicId: string | null;
  startedAt: number;
  durationSec: number | null;
  /** Caption cues read in the browser (capture path). */
  segments: number | null;
  /** The page read showed no caption track. */
  captionless: boolean;
  /** The item was already in the library when the video was collected. */
  existing: boolean;
  stage: ItemStage | null;
  /** When the current stage began, in `now()` time. */
  stageSince: number | null;
  /** The last non-terminal stage reported. */
  workingStage: WorkingStage | null;
  sawTranscribing: boolean;
  outcome: CollectOutcome | null;
  /** Safe server error code, or the stable code of a failed submission. */
  errorCode: string | null;
  retryable: boolean;
  /** Polling is failing and backing off; display only. */
  retrying: boolean;
}

export const TERMINAL_OUTCOMES: ReadonlySet<CollectOutcome> = new Set<CollectOutcome>([
  "ready",
  "already_exists",
  "needs_action",
  "failed",
  "archived",
  "submitted",
  "timeout",
  "quota_exceeded",
  "unsupported_url",
  "unsupported_part",
  "submit_failed",
]);

export function newCollectEntry(id: string, videoKey: string, now: number): CollectEntry {
  return {
    kind: "collect",
    id,
    videoKey,
    phase: "reading",
    path: null,
    fallback: null,
    itemPublicId: null,
    startedAt: now,
    durationSec: null,
    segments: null,
    captionless: false,
    existing: false,
    stage: null,
    stageSince: null,
    workingStage: null,
    sawTranscribing: false,
    outcome: null,
    errorCode: null,
    retryable: false,
    retrying: false,
  };
}

/** Ends the entry with an outcome that needs no further polling. */
export function finishEntry(entry: CollectEntry, outcome: CollectOutcome, errorCode: string | null = null): CollectEntry {
  return { ...entry, phase: "done", outcome, errorCode: errorCode ?? entry.errorCode, retrying: false };
}

const WORKING_STAGES: ReadonlySet<string> = new Set<WorkingStage>(["queued", "fetching", "transcribing", "chunking", "embedding"]);

export function isWorkingStage(stage: string | null): stage is WorkingStage {
  return stage !== null && WORKING_STAGES.has(stage);
}

/** The stage of an item from a server that predates the `stage` field. */
function stageFromLifecycle(item: LibraryItem, path: CollectPath | null): ItemStage {
  if (item.lifecycle === "processing") return path === "capture" ? "chunking" : "fetching";
  return item.lifecycle;
}

const SIX_HOURS_MS = 6 * 60 * 60 * 1000;

/**
 * When a stage began. The first stage seen may predate the click (an item that was already being
 * processed), so the server's timestamp is used once, clamped to the past. Later stages begin when
 * the panel sees the change: the server timestamp is the dispatch time, which is older than any
 * stage after the first.
 */
function stageStart(item: LibraryItem, first: boolean, now: number): number {
  if (!first || !item.stage_updated_at) return now;
  const serverTime = Date.parse(item.stage_updated_at);
  return Number.isFinite(serverTime) && serverTime <= now && now - serverTime < SIX_HOURS_MS ? serverTime : now;
}

function outcomeFor(item: LibraryItem, entry: CollectEntry): CollectOutcome | null {
  switch (item.lifecycle) {
    case "ready": return entry.existing ? "already_exists" : "ready";
    case "needs_action": return "needs_action";
    case "failed": return "failed";
    case "archived": return "archived";
    default: return null;
  }
}

/** Folds one library item read into the entry. */
export function applyItem(entry: CollectEntry, item: LibraryItem, now: number): CollectEntry {
  const stage = item.stage ?? stageFromLifecycle(item, entry.path);
  const changed = stage !== entry.stage;
  const next: CollectEntry = {
    ...entry,
    retrying: false,
    durationSec: item.duration_sec ?? entry.durationSec,
    retryable: item.available_actions.includes("retry"),
    stage,
    stageSince: changed ? stageStart(item, entry.stage === null, now) : entry.stageSince,
  };
  if (isWorkingStage(stage)) {
    next.workingStage = stage;
    if (stage === "transcribing") next.sawTranscribing = true;
  }
  const outcome = outcomeFor(item, entry);
  if (outcome) {
    next.phase = "done";
    next.outcome = outcome;
    next.errorCode = item.error_code ?? null;
  }
  return next;
}

/** Entry fields that survive a panel reload. `retrying` is transient. */
export type StoredCollect = Omit<CollectEntry, "retrying">;

export function toStored(entry: CollectEntry): StoredCollect {
  const stored: Partial<CollectEntry> = { ...entry };
  delete stored.retrying;
  return stored as StoredCollect;
}

const PATHS: ReadonlySet<unknown> = new Set(["capture", "url"]);
const STAGES: ReadonlySet<unknown> = new Set(["queued", "fetching", "transcribing", "chunking", "embedding", "ready", "needs_action", "failed", "archived"]);
const FALLBACKS: ReadonlySet<unknown> = new Set(["read_failed", "no_captions", "not_paired", "capture_failed"]);

/** Restores a persisted entry, or null when the stored value is not a usable in-progress entry. */
export function parseStored(value: unknown): CollectEntry | null {
  if (typeof value !== "object" || value === null) return null;
  const v = value as Record<string, unknown>;
  const text = (key: string) => (typeof v[key] === "string" ? (v[key] as string) : null);
  const num = (key: string) => (typeof v[key] === "number" && Number.isFinite(v[key]) ? (v[key] as number) : null);
  const id = text("id");
  const videoKey = text("videoKey");
  const itemPublicId = text("itemPublicId");
  const startedAt = num("startedAt");
  if (!id || !videoKey || !itemPublicId || startedAt === null) return null;
  // Only an item that was being followed can resume; earlier phases leave nothing to follow.
  if (v.phase !== "tracking" || !PATHS.has(v.path)) return null;
  const working = text("workingStage");
  return {
    ...newCollectEntry(id, videoKey, startedAt),
    phase: "tracking",
    path: v.path as CollectPath,
    fallback: FALLBACKS.has(v.fallback) ? (v.fallback as FallbackReason) : null,
    itemPublicId,
    durationSec: num("durationSec"),
    segments: num("segments"),
    captionless: v.captionless === true,
    existing: v.existing === true,
    stage: STAGES.has(v.stage) ? (v.stage as ItemStage) : null,
    stageSince: num("stageSince"),
    workingStage: isWorkingStage(working) ? working : null,
    sawTranscribing: v.sawTranscribing === true,
    retryable: v.retryable === true,
  };
}
