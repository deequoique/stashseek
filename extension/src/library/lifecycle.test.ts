import { describe, expect, it } from "vitest";

import { estimateWorkItemProgress, lifecycleCopy, shouldPollLibrary } from "./lifecycle";

describe("library lifecycle presentation", () => {
  it("uses safe, useful Chinese copy for every server state", () => {
    expect(lifecycleCopy.queued.label).toBe("等待整理");
    expect(lifecycleCopy.processing.label).toBe("正在整理");
    expect(lifecycleCopy.ready.label).toBe("可阅读");
    expect(lifecycleCopy.needs_action.label).toBe("字幕不可用");
    expect(lifecycleCopy.failed.label).toBe("整理失败");
    expect(lifecycleCopy.archived.label).toBe("已归档");
  });

  it("polls only while at least one visible item is nonterminal", () => {
    expect(shouldPollLibrary([{ lifecycle: "queued" }])).toBe(true);
    expect(shouldPollLibrary([{ lifecycle: "processing" }])).toBe(true);
    expect(shouldPollLibrary([{ lifecycle: "ready" }, { lifecycle: "failed" }])).toBe(false);
    expect(shouldPollLibrary([])).toBe(false);
  });

  it("does not present failed or action-required items as completed progress", () => {
    expect(estimateWorkItemProgress([{ lifecycle: "failed" }, { lifecycle: "needs_action" }])).toBe(0);
    expect(estimateWorkItemProgress([{ lifecycle: "queued" }, { lifecycle: "processing" }])).toBe(43);
  });
});
