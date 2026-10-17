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

/** Server-side reasons an import fails, keyed by the item's safe error code. */
const FAILURE_REASON: Record<string, string> = {
  queue_unavailable: "处理队列暂时不可用",
  ingestion_failed: "处理过程出错",
  transient_fetch_failed: "获取视频内容时遇到临时错误",
  item_missing: "这个条目已不存在",
  missing_dispatch: "处理任务没有启动",
  transcript_unavailable: "没有可用的字幕文本",
  transcript_invalid: "字幕内容无法解析",
  ingest_too_large: "视频或字幕太长，超出处理上限",
  asr_too_long: "视频太长，无法转写",
  asr_audio_unavailable: "无法获取视频音频",
  asr_audio_too_large: "音频太大，无法转写",
  asr_provider_failed: "语音转写服务出错",
  asr_no_speech: "没有检测到语音",
  asr_not_configured: "服务器没有开启语音转写",
  youtube_rate_limited: "服务器暂时无法访问 YouTube",
  bilibili_rate_limited: "服务器暂时无法访问 Bilibili",
};

const NEEDS_ACTION_REASON = "没有可用字幕，需要语音转写或从浏览器读取字幕";

interface Slot {
  id: string;
  text: string;
  stages: readonly WorkingStage[];
}

/** The working steps this entry passes through, in order. Transcription appears only once it is reported. */
function workingSlots(entry: CollectEntry): Slot[] {
  const slots: Slot[] = [
    entry.path === "url"
      ? { id: "fetch", text: "获取视频信息与字幕", stages: ["queued", "fetching"] }
      : { id: "queue", text: "排队等待处理", stages: ["queued", "fetching"] },
  ];
  if (entry.sawTranscribing) slots.push({ id: "transcribe", text: "语音转写中", stages: ["transcribing"] });
  slots.push(
    { id: "chunk", text: "切分段落", stages: ["chunking"] },
    { id: "embed", text: "生成检索向量", stages: ["embedding"] },
  );
  return slots;
}

function slotIndex(slots: Slot[], stage: WorkingStage | null): number {
  if (stage === null) return 0;
  const index = slots.findIndex((slot) => slot.stages.includes(stage));
  return index === -1 ? 0 : index;
}

function failureText(entry: CollectEntry): string {
  const reason = entry.errorCode ? FAILURE_REASON[entry.errorCode] ?? "处理出错" : "处理出错";
  return `未能完成：${reason}${entry.retryable ? "，可以在工作区重试" : ""}`;
}

/** The 「收藏视频」 block as a step list, from the click to the end state. */
export function collectSteps(entry: CollectEntry): TimelineStep[] {
  const steps: TimelineStep[] = [];
  const push = (id: string, text: string, outcome: TimelineStep["outcome"]) => { steps.push({ id, text, outcome }); };

  if (entry.phase === "reading") {
    push("read", "正在读取页面字幕", "running");
    return steps;
  }
  if (entry.path === "capture") push("read", `已读取页面字幕（${entry.segments ?? 0} 段）`, "completed");
  else if (entry.fallback) push("read", FALLBACK_COPY[entry.fallback], "skipped");

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
  if (outcome === "submitted") return steps;

  const slots = workingSlots(entry);
  const reached = slotIndex(slots, entry.workingStage);
  const completeThrough = (count: number) => {
    slots.slice(0, count).forEach((slot) => push(slot.id, slot.text, "completed"));
  };

  if (entry.phase === "tracking") {
    const current = slotIndex(slots, entry.workingStage);
    completeThrough(current);
    const slot = slots[current] as Slot;
    push(slot.id, slot.text, "running");
    return steps;
  }

  switch (outcome) {
    case "ready":
    case "already_exists":
      if (entry.workingStage !== null) completeThrough(slots.length);
      push("done", outcome === "ready" ? "完成，可以在资料库里搜索和提问" : "已在资料库中", "completed");
      break;
    case "needs_action":
      if (entry.workingStage !== null) completeThrough(reached + 1);
      push("end", `需要处理：${NEEDS_ACTION_REASON}`, "failed");
      break;
    case "failed":
      if (entry.workingStage !== null) completeThrough(reached);
      push("end", failureText(entry), "failed");
      break;
    case "archived":
      if (entry.workingStage !== null) completeThrough(reached + 1);
      push("end", "这个视频已被归档", "skipped");
      break;
    case "timeout": {
      completeThrough(reached);
      const slot = slots[reached] as Slot;
      push(slot.id, slot.text, "skipped");
      break;
    }
    default:
      break;
  }
  return steps;
}

/** One-line state shown when the block is collapsed. */
export function collectSummary(entry: CollectEntry, steps: TimelineStep[]): string {
  const running = steps.find((step) => step.outcome === "running");
  if (running) return `${ACTION_NAME} · ${running.text}`;
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
    if (eta) parts.push(eta);
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
    case "needs_action":
    case "failed":
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
