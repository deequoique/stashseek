import type { TimelineStep } from "../chat/StepTimeline";
import { formatTimestamp, workerErrorCopy } from "./copy";
import { type CollectEntry, type FallbackReason, isWorkingStage, type WorkingStage } from "./collectModel";
import { estimateEta, formatElapsed, formatEta } from "./eta";
import type { QaTurn } from "./usePageQa";

const ACTION_NAME = "收藏视频";

const FALLBACK_COPY: Record<FallbackReason, string> = {
  read_failed: "没能读取页面字幕，改用链接导入",
  no_captions: "视频没有字幕，改用链接导入",
  not_paired: "这台设备还没连接，改用链接导入",
  capture_failed: "字幕提交没有成功，改用链接导入",
};

/** Said instead of the no-captions line when the server transcribes the video's platform. */
const NO_CAPTIONS_TRANSCRIBED_COPY = "页面没有字幕，收藏后会自动语音转写";

/** Why the URL import was used; `willTranscribe` is true when the server transcribes this video's platform. */
export function fallbackCopy(reason: FallbackReason, willTranscribe: boolean): string {
  return reason === "no_captions" && willTranscribe ? NO_CAPTIONS_TRANSCRIBED_COPY : FALLBACK_COPY[reason];
}

/** What the 「收藏视频」 block shows for an entry. */
export type CollectBody =
  /** The step list: reading, submitting, and the ends that have no item to show. */
  | "steps"
  /** The ingest progress card of an item being followed, or one that finished and is searchable. */
  | "progress"
  /** The unfinished card of an item whose body is not searchable. */
  | "unfinished";

export function collectBody(entry: CollectEntry): CollectBody {
  if (entry.phase === "tracking") return "progress";
  switch (entry.outcome) {
    case "ready":
    case "already_exists":
      return "progress";
    case "timeout":
      return entry.itemPublicId && entry.stages.length > 0 ? "progress" : "steps";
    case "needs_action":
    case "failed":
      return entry.reason ? "unfinished" : "steps";
    default:
      return "steps";
  }
}

/** The failure line of an ended entry that has no server reason. Only a vanished item gets here. */
function failureText(entry: CollectEntry): string {
  return `未能完成：${entry.errorCode === "item_missing" ? "这个条目已不存在" : "处理出错"}`;
}

/** The small line above the card: how the video reached the server. Null when there is nothing to say. */
export function collectNote(entry: CollectEntry, willTranscribe = false): string | null {
  if (entry.path === "capture") return `已读取页面字幕（${entry.segments ?? 0} 段）`;
  if (entry.path === "url" && entry.fallback) return fallbackCopy(entry.fallback, willTranscribe);
  return null;
}

/** The 「收藏视频」 block as a step list, up to the point where the item takes over. */
export function collectSteps(entry: CollectEntry, willTranscribe = false): TimelineStep[] {
  const steps: TimelineStep[] = [];
  const push = (id: string, text: string, outcome: TimelineStep["outcome"]) => { steps.push({ id, text, outcome }); };

  if (entry.phase === "reading") {
    push("read", "正在读取页面字幕", "running");
    return steps;
  }
  if (entry.path === "capture") push("read", `已读取页面字幕（${entry.segments ?? 0} 段）`, "completed");
  else if (entry.fallback) push("read", fallbackCopy(entry.fallback, willTranscribe), "skipped");

  if (entry.phase === "submitting") {
    push("submit", "正在提交", "running");
    return steps;
  }
  const { outcome } = entry;
  if (outcome === "quota_exceeded") push("end", "未能收藏：收藏数量已达上限", "failed");
  else if (outcome === "unsupported_url") push("end", "未能收藏：暂不支持直接导入这个视频", "failed");
  else if (outcome === "unsupported_part") push("end", "暂不支持收藏这一 P，可以在第 1P 收藏", "failed");
  else if (outcome === "submit_failed") push("end", `未能收藏：${workerErrorCopy(entry.errorCode ?? "request_failed")}`, "failed");
  if (outcome === "quota_exceeded" || outcome === "unsupported_url" || outcome === "unsupported_part" || outcome === "submit_failed") return steps;

  push("submit", "已提交", "completed");
  if (outcome === "archived") push("end", "这个视频已被归档", "skipped");
  else if (outcome === "failed" || outcome === "needs_action") push("end", failureText(entry), "failed");
  return steps;
}

/** One-line state shown when the block is collapsed. */
export function collectSummary(entry: CollectEntry, steps: TimelineStep[]): string {
  const running = steps.find((step) => step.outcome === "running");
  if (running) return `${ACTION_NAME} · ${running.text}`;
  if (entry.phase === "tracking") return `${ACTION_NAME} · 已提交`;
  switch (entry.outcome) {
    case "ready": return `${ACTION_NAME} · 已完成`;
    case "already_exists": return `${ACTION_NAME} · 已在资料库中`;
    case "needs_action": return `${ACTION_NAME} · 需要处理`;
    case "failed": return `${ACTION_NAME} · 未能完成`;
    case "archived": return `${ACTION_NAME} · 已归档`;
    case "submitted": return `${ACTION_NAME} · 已提交`;
    case "timeout": return `${ACTION_NAME} · 仍在处理`;
    default: return `${ACTION_NAME} · 未能提交`;
  }
}

/** Elapsed time and the ETA for the running step; null once the entry has ended. */
export function collectProgress(entry: CollectEntry, now: number): string | null {
  if (entry.phase === "done") return null;
  const parts = [`已用时 ${formatElapsed((now - entry.startedAt) / 1000)}`];
  if (entry.phase === "tracking") {
    const stage: WorkingStage = isWorkingStage(entry.stage) ? entry.stage : "queued";
    const since = entry.stageSince ?? entry.startedAt;
    const eta = formatEta(estimateEta({
      path: entry.path ?? "url",
      stage,
      durationSec: entry.durationSec,
      secondsInStage: (now - since) / 1000,
      captionless: entry.captionless,
      sawTranscribing: entry.sawTranscribing,
    }));
    // A ready body leaves only later stages, whose duration the estimate does not cover.
    if (eta && entry.stage !== "ready") parts.push(eta);
    if (entry.retrying) parts.push("连接不稳定，正在重试");
  }
  return parts.join(" · ");
}

export interface CollectResult {
  tone: "success" | "action";
  text: string;
}

/** Closing line for the outcomes whose step list alone does not say what happened. */
export function collectResult(entry: CollectEntry): CollectResult | null {
  switch (entry.outcome) {
    case "ready": return { tone: "success", text: "已收藏到资料库。" };
    case "already_exists": return { tone: "success", text: "这个视频已经在资料库里了。" };
    case "submitted": return { tone: "action", text: "已提交，可以在工作区查看处理进度。" };
    case "timeout": return { tone: "action", text: "处理时间比较长，已停止自动刷新，稍后可以在工作区查看进度。" };
    default: return null;
  }
}

/** Where 「在工作区查看」 leads, or null when nothing was saved. */
export function collectWorkspacePath(entry: CollectEntry): string | null {
  switch (entry.outcome) {
    case "ready":
    case "already_exists":
    case "archived":
    case "submitted":
    case "timeout":
      return entry.itemPublicId ? `/videos/${encodeURIComponent(entry.itemPublicId)}` : "/library";
    default:
      return null;
  }
}

/** Client-side steps shown above a streamed answer. */
export function qaSteps(turn: QaTurn): TimelineStep[] {
  const { reading } = turn;
  const steps: TimelineStep[] = [];
  steps.push({
    id: "read",
    text: reading.captioned
      ? `读取视频字幕（${reading.segments} 段${reading.language ? ` · ${languageName(reading.language)}` : ""}）`
      : "该视频没有字幕，使用标题和简介",
    outcome: "completed",
  });
  if (turn.playback === undefined) steps.push({ id: "playback", text: "读取播放位置", outcome: "running" });
  else if (turn.playback === null) steps.push({ id: "playback", text: "没有读到播放位置", outcome: "skipped" });
  else steps.push({ id: "playback", text: `播放位置 ${formatTimestamp(turn.playback)}`, outcome: "completed" });

  const generating = turn.answer !== "" || turn.status === "done";
  steps.push({
    id: "answer",
    text: "生成回答",
    outcome: turn.status === "failed" ? "failed" : turn.status === "stopped" ? "skipped" : generating ? "completed" : "running",
  });
  return steps;
}

export function qaSummary(steps: TimelineStep[]): string {
  const running = steps.find((step) => step.outcome === "running");
  return running ? running.text : `回答过程 · ${steps.length} 个步骤`;
}

function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(["zh-CN"], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}
