import type { IngestReason, IngestReasonCategory, IngestStage } from "../api/contracts";

/** Every ingest-status string the side panel and the workspace show lives here. */

/** Stage keys this build can show, in display order. The server may send others; they are ignored. */
export const KNOWN_STAGE_KEYS = ["body", "classify", "summary", "movie"] as const;
export type KnownStageKey = (typeof KNOWN_STAGE_KEYS)[number];

const STAGE_KEY_ORDER: readonly string[] = KNOWN_STAGE_KEYS;

export function isKnownStageKey(key: string): key is KnownStageKey {
  return STAGE_KEY_ORDER.includes(key);
}

/** Position of a stage key in the display order, or -1 for a key this build does not know. */
export function stageRank(key: string): number {
  return STAGE_KEY_ORDER.indexOf(key);
}

export const STAGE_LABEL: Record<KnownStageKey, string> = {
  body: "读取字幕与内容",
  classify: "分类与主题",
  summary: "内容要点",
  movie: "电影识别",
};

/** What the body stage is doing while it is waiting or running. */
export const BODY_DETAIL_LABEL: Record<NonNullable<IngestStage["detail"]>, string> = {
  queued: "排队中",
  fetching: "读取字幕与内容",
  transcribing: "语音转写中",
  indexing: "建立检索索引",
};

/** Shown in a stage's region when that stage failed. */
export const STAGE_PLACEHOLDER: Record<Exclude<KnownStageKey, "body">, string> = {
  classify: "未分类",
  summary: "暂无内容要点",
  movie: "未识别",
};

/** The status line of an unfinished item, before 「，正文暂不可检索。」. */
export const REASON_COPY: Record<IngestReasonCategory, string> = {
  no_speech: "没有字幕，也没有识别出语音",
  no_transcription: "没有字幕，暂时无法语音转写",
  fetch_failed: "没能获取视频内容，可能是平台暂时限制了访问",
  asr_failed: "语音转写服务暂时出错",
  quota: "处理额度不足",
  too_large: "视频太长或内容太大，超出处理上限",
  login_required: "这个视频的字幕需要登录 B站 才能读取",
  error: "整理时出错了",
};

export function isReasonCategory(value: string): value is IngestReasonCategory {
  return Object.hasOwn(REASON_COPY, value);
}

/** The reason's short phrase; a category this build does not know reads as a plain error. */
export function reasonPhrase(reason: Pick<IngestReason, "category">): string {
  return isReasonCategory(reason.category) ? REASON_COPY[reason.category] : REASON_COPY.error;
}

/** The full status line of the unfinished screen. */
export function reasonStatusLine(reason: Pick<IngestReason, "category">): string {
  return `${reasonPhrase(reason)}，正文暂不可检索。`;
}

export const RETRY_NOT_USEFUL_HINT = "再试一次大概率还是同样的结果。";

export const UNFINISHED_EXPLANATION = "可以按标题和备注查找，也可以稍后重试。";
export const LOGIN_REQUIRED_EXPLANATION = "请先登录 B站，再在视频页重新收藏。";

export const PROGRESS_TITLE = "已经保存，不必等在这里";
export const PROGRESS_EXPLANATION = "现在可以继续看视频，关闭插件也不影响处理。";
export const UNFINISHED_TITLE = "收藏还在，整理没有完成";
export const SEARCHABLE_STATUS = "已整理，可检索";
export const SAVED_LINK_STEP = "保存视频链接";

export const ACTION_LABEL = {
  retry: "重新整理",
  recollect: "重新收藏",
  openVideo: "打开视频页",
  searchTitles: "仅查标题 / 备注",
} as const;

export const UNSEARCHABLE_BADGE = "正文不可检索";
export const UNSEARCHABLE_BADGE_DETAIL = "正文不可检索，可按标题 / 备注查找";

export const QUOTA_RETRY_COPY = "处理额度不足，请稍后再试。";
export const RETRY_FAILED_COPY = "操作未完成，请稍后重试。";

/** The workspace route behind 「仅查标题 / 备注」. */
export const TITLE_SEARCH_ROUTE = "/library?focus=search";

const LIFECYCLE_LABEL: Record<string, string> = {
  queued: "排队中",
  processing: "整理中",
  ready: "可检索",
  failed: "整理失败",
  needs_action: "整理失败",
  archived: "已归档",
};

/** The status pill text; a lifecycle this build does not know reads as still processing. */
export function lifecycleLabel(lifecycle: string): string {
  return (Object.hasOwn(LIFECYCLE_LABEL, lifecycle) ? LIFECYCLE_LABEL[lifecycle] : undefined) ?? "整理中";
}

/** The library filter option text: the pill label, except that "needs action" is told apart from "failed". */
export function lifecycleFilterLabel(lifecycle: string): string {
  return lifecycle === "needs_action" ? "需要处理" : lifecycleLabel(lifecycle);
}

const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube",
  bilibili: "哔哩哔哩",
  ntu_kaltura: "NTULearn",
};

export function platformLabel(platform: string): string {
  return (Object.hasOwn(PLATFORM_LABEL, platform) ? PLATFORM_LABEL[platform] : undefined) ?? platform;
}
