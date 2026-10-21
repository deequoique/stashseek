import type { LibraryItemSummary, LibraryLifecycle } from "../api/contracts";

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
