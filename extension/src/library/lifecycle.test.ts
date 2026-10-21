import { describe, expect, it } from "vitest";

import { estimateWorkItemProgress, isLibraryWorkItem, shouldPollLibrary } from "./lifecycle";

describe("library lifecycle presentation", () => {
  it("polls only while at least one visible item is nonterminal", () => {
    expect(shouldPollLibrary([{ lifecycle: "queued" }])).toBe(true);
    expect(shouldPollLibrary([{ lifecycle: "processing" }])).toBe(true);
    expect(shouldPollLibrary([{ lifecycle: "ready" }, { lifecycle: "failed" }])).toBe(false);
    expect(shouldPollLibrary([])).toBe(false);
  });

  it("keeps queued, processing, failed and action-required items in the work queue", () => {
    for (const lifecycle of ["queued", "processing", "needs_action", "failed"] as const) {
      expect(isLibraryWorkItem({ lifecycle })).toBe(true);
    }
    expect(isLibraryWorkItem({ lifecycle: "ready" })).toBe(false);
    expect(isLibraryWorkItem({ lifecycle: "archived" })).toBe(false);
  });

  it("does not present failed or action-required items as completed progress", () => {
    expect(estimateWorkItemProgress([{ lifecycle: "failed" }, { lifecycle: "needs_action" }])).toBe(0);
    expect(estimateWorkItemProgress([{ lifecycle: "queued" }, { lifecycle: "processing" }])).toBe(43);
  });
});
