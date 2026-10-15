import { describe, expect, it } from "vitest";

import {
  collectCollectionNames,
  formatWhySaved,
  formatWhySavedWithCollections,
  parseWhySaved,
  validateCollectionName,
} from "./collections";

describe("why-saved collection tags", () => {
  it("parses supported hashtags and separates them from the human reason", () => {
    expect(parseWhySaved("准备访谈前复习 #产品调研 #AI_入门 #产品调研")).toEqual({
      reason: "准备访谈前复习",
      collections: ["产品调研", "AI_入门"],
    });
    expect(parseWhySaved("保留普通内容 #not.valid #名称里有 空格")).toEqual({
      reason: "保留普通内容 #not.valid 空格",
      collections: ["名称里有"],
    });
  });

  it("validates a short portable collection name", () => {
    expect(validateCollectionName("产品调研")).toBeNull();
    expect(validateCollectionName("AI_notes-2026")).toBeNull();
    expect(validateCollectionName("  ")).toBe("请输入收藏夹名称");
    expect(validateCollectionName("产品 调研")).toBe("名称只能使用中文、字母、数字、短横线或下划线");
    expect(validateCollectionName("a".repeat(21))).toBe("名称最多 20 个字符");
  });

  it("formats one selected collection without changing the request shape", () => {
    expect(formatWhySaved("准备周末精读", "产品调研")).toEqual({
      value: "准备周末精读 #产品调研",
      error: null,
    });
    expect(formatWhySaved("已有说明 #旧目录", "新目录")).toEqual({
      value: "已有说明 #新目录",
      error: null,
    });
    expect(formatWhySaved("只有说明 #旧目录", null)).toEqual({
      value: "只有说明",
      error: null,
    });
    expect(formatWhySaved("", null)).toEqual({ value: null, error: null });
  });

  it("preserves every existing collection when the reason is edited", () => {
    expect(formatWhySavedWithCollections("更新后的说明", ["产品调研", "AI_入门"])).toEqual({
      value: "更新后的说明 #产品调研 #AI_入门",
      error: null,
    });
  });

  it("rejects a combined value above the upstream 500 character limit", () => {
    expect(formatWhySaved("x".repeat(496), "产品调研")).toEqual({
      value: null,
      error: "保存说明和收藏夹合计最多 500 个字符",
    });
  });

  it("discovers unique collection names in first-seen order", () => {
    expect(collectCollectionNames([
      "第一条 #产品调研 #AI",
      null,
      "第二条 #ai #学习计划",
    ])).toEqual(["产品调研", "AI", "学习计划"]);
  });
});
