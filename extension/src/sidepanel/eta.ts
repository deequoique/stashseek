/**
 * Client-side ETA for a library import. It is a heuristic: every per-stage number below is a
 * conservative placeholder to be calibrated from real runs, and lives only in this table.
 */

export type EtaStage = "queued" | "fetching" | "transcribing" | "chunking" | "embedding";
export type EtaPath = "capture" | "url";

/** Expected seconds spent in a stage for a video of `minutes` minutes. */
export const STAGE_EXPECTED_SECONDS: Record<EtaStage, (minutes: number) => number> = {
  queued: () => 10,
  fetching: (minutes) => 20 + 2 * minutes,
  transcribing: (minutes) => 30 + 15 * minutes,
  chunking: (minutes) => 5 + 2 * minutes,
  embedding: (minutes) => 5 + 3 * minutes,
};

/** A stage is "taking longer than expected" once it has run this many times its estimate. */
export const OVERRUN_FACTOR = 1.5;

const CANONICAL_ORDER: readonly EtaStage[] = ["queued", "fetching", "transcribing", "chunking", "embedding"];

export interface EtaInput {
  path: EtaPath;
  stage: EtaStage;
  /** Video length; null or non-positive when unknown. */
  durationSec: number | null;
  /** Seconds since the current stage began. */
  secondsInStage: number;
  /** The page read showed no caption track, so a URL import will need speech transcription. */
  captionless: boolean;
  /** Speech transcription was already reported for this item. */
  sawTranscribing: boolean;
}

export type Eta =
  | { kind: "unknown" }
  | { kind: "overrun" }
  | { kind: "remaining"; seconds: number };

/** Stages this import passes through, in order, including the current one. */
export function stagesOnPath(input: Pick<EtaInput, "path" | "stage" | "captionless" | "sawTranscribing">): EtaStage[] {
  const withTranscribing = input.stage === "transcribing"
    || input.sawTranscribing
    || (input.path === "url" && input.captionless);
  return CANONICAL_ORDER.filter((stage) => {
    if (stage === input.stage) return true;
    if (stage === "fetching") return input.path === "url";
    if (stage === "transcribing") return withTranscribing;
    return true;
  });
}

export function estimateEta(input: EtaInput): Eta {
  const { durationSec } = input;
  if (durationSec === null || !Number.isFinite(durationSec) || durationSec <= 0) return { kind: "unknown" };
  const minutes = durationSec / 60;
  const expected = (stage: EtaStage) => STAGE_EXPECTED_SECONDS[stage](minutes);
  const inStage = Math.max(0, input.secondsInStage);
  const current = expected(input.stage);
  if (inStage > OVERRUN_FACTOR * current) return { kind: "overrun" };
  const sequence = stagesOnPath(input);
  const later = sequence.slice(sequence.indexOf(input.stage) + 1);
  const remaining = Math.max(current - inStage, 0) + later.reduce((sum, stage) => sum + expected(stage), 0);
  return { kind: "remaining", seconds: remaining };
}

/** Copy for an estimate, or null when none can be given (elapsed time only). */
export function formatEta(eta: Eta): string | null {
  if (eta.kind === "unknown") return null;
  if (eta.kind === "overrun") return "比预计久，仍在处理";
  return eta.seconds < 60 ? "预计还需不到 1 分钟" : `预计还需约 ${Math.ceil(eta.seconds / 60)} 分钟`;
}

/** `42 秒`, `3 分 05 秒`, `1 小时 02 分`. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${String(minutes % 60).padStart(2, "0")} 分`;
}
