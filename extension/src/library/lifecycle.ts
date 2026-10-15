import type { LibraryItemSummary, LibraryLifecycle } from "../api/contracts";

export const lifecycleCopy: Record<
  LibraryLifecycle,
  { label: string; description: string }
> = {
  queued: { label: "等待整理", description: "已保存，正在等待系统开始整理。" },
  processing: { label: "正在整理", description: "正在获取视频信息和字幕。" },
  ready: { label: "可阅读", description: "章节和字幕已准备好。" },
  needs_action: { label: "字幕不可用", description: "暂时没有可用字幕，请打开详情查看可用操作。" },
  failed: { label: "整理失败", description: "整理未完成，请打开详情查看是否可以重新整理。" },
  archived: { label: "已归档", description: "已从默认资料库隐藏，可随时恢复。" },
};

export function shouldPollLibrary(
  items: ReadonlyArray<Pick<LibraryItemSummary, "lifecycle">>,
): boolean {
  return items.some(({ lifecycle }) => lifecycle === "queued" || lifecycle === "processing");
}

const workItemProgress: Partial<Record<LibraryLifecycle, number>> = {
  queued: 20,
  processing: 65,
};

export function isLibraryWorkItem(
  item: Pick<LibraryItemSummary, "lifecycle">,
): boolean {
  return ["queued", "processing", "needs_action", "failed"].includes(item.lifecycle);
}

export function estimateWorkItemProgress(
  items: ReadonlyArray<Pick<LibraryItemSummary, "lifecycle">>,
): number {
  const activeItems = items.filter(
    ({ lifecycle }) => lifecycle === "queued" || lifecycle === "processing",
  );
  if (activeItems.length === 0) return 0;
  const total = activeItems.reduce(
    (sum, item) => sum + (workItemProgress[item.lifecycle] ?? 0),
    0,
  );
  return Math.round(total / activeItems.length);
}
