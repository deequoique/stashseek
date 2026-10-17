import { describe, expect, it } from "vitest";

import { applyItem, type CollectEntry, finishEntry, newCollectEntry } from "./collectModel";
import { libraryItem } from "../test/sidepanelEnvironment";
import { collectProgress, collectResult, collectSteps, collectSummary, collectWorkspacePath, qaSteps, qaSummary } from "./steps";
import type { QaTurn } from "./usePageQa";

const T0 = 1_700_000_000_000;

function entry(overrides: Partial<CollectEntry> = {}): CollectEntry {
  return { ...newCollectEntry("entry-1", "youtube:aaaaaaaaaaa", T0), ...overrides };
}

/** An entry that was submitted and is following `item`. */
function following(path: "capture" | "url", ...items: Array<Parameters<typeof libraryItem>[0]>): CollectEntry {
  let current = entry({
    phase: "tracking",
    path,
    fallback: path === "url" ? "read_failed" : null,
    segments: path === "capture" ? 12 : null,
    itemPublicId: "item-1",
  });
  items.forEach((item, index) => { current = applyItem(current, libraryItem(item), T0 + (index + 1) * 5_000); });
  return current;
}

const summarize = (steps: ReturnType<typeof collectSteps>) => steps.map((step) => `${step.outcome}:${step.text}`);

describe("collectSteps before the item exists", () => {
  it("shows a running read, then a running submission", () => {
    expect(summarize(collectSteps(entry()))).toEqual(["running:正在读取页面字幕"]);
    expect(summarize(collectSteps(entry({ phase: "submitting", path: "capture", segments: 12 })))).toEqual([
      "completed:已读取页面字幕（12 段）",
      "running:正在提交",
    ]);
    expect(summarize(collectSteps(entry({ phase: "submitting", path: "url", fallback: "no_captions" })))).toEqual([
      "skipped:视频没有字幕，改用链接导入",
      "running:正在提交",
    ]);
  });

  it("explains each reason the URL import was used", () => {
    const text = (fallback: CollectEntry["fallback"]) =>
      collectSteps(entry({ phase: "submitting", path: "url", fallback }))[0]?.text;
    expect(text("read_failed")).toBe("没能读取页面字幕，改用链接导入");
    expect(text("no_captions")).toBe("视频没有字幕，改用链接导入");
    expect(text("not_paired")).toBe("这台设备还没连接，改用链接导入");
    expect(text("capture_failed")).toBe("字幕提交没有成功，改用链接导入");
  });
});

describe("collectSteps on the capture path", () => {
  it("waits in the queue, then chunks, then embeds", () => {
    expect(summarize(collectSteps(following("capture")))).toEqual([
      "completed:已读取页面字幕（12 段）", "completed:已提交", "running:排队等待处理",
    ]);
    expect(summarize(collectSteps(following("capture", { stage: "queued", lifecycle: "queued" })))).toEqual([
      "completed:已读取页面字幕（12 段）", "completed:已提交", "running:排队等待处理",
    ]);
    expect(summarize(collectSteps(following("capture", { stage: "chunking" })))).toEqual([
      "completed:已读取页面字幕（12 段）", "completed:已提交", "completed:排队等待处理", "running:切分段落",
    ]);
    expect(summarize(collectSteps(following("capture", { stage: "chunking" }, { stage: "embedding" })))).toEqual([
      "completed:已读取页面字幕（12 段）", "completed:已提交", "completed:排队等待处理", "completed:切分段落", "running:生成检索向量",
    ]);
  });

  it("finishes with the library message", () => {
    const steps = collectSteps(following("capture", { stage: "embedding" }, { lifecycle: "ready", stage: "ready" }));
    expect(summarize(steps)).toEqual([
      "completed:已读取页面字幕（12 段）", "completed:已提交", "completed:排队等待处理", "completed:切分段落", "completed:生成检索向量",
      "completed:完成，可以在资料库里搜索和提问",
    ]);
  });
});

describe("collectSteps on the URL path", () => {
  it("maps every stage to its step", () => {
    const base = ["skipped:没能读取页面字幕，改用链接导入", "completed:已提交"];
    expect(summarize(collectSteps(following("url", { stage: "queued", lifecycle: "queued" })))).toEqual([...base, "running:获取视频信息与字幕"]);
    expect(summarize(collectSteps(following("url", { stage: "fetching" })))).toEqual([...base, "running:获取视频信息与字幕"]);
    expect(summarize(collectSteps(following("url", { stage: "fetching" }, { stage: "transcribing" })))).toEqual([
      ...base, "completed:获取视频信息与字幕", "running:语音转写中",
    ]);
    expect(summarize(collectSteps(following("url", { stage: "fetching" }, { stage: "transcribing" }, { stage: "chunking" })))).toEqual([
      ...base, "completed:获取视频信息与字幕", "completed:语音转写中", "running:切分段落",
    ]);
    expect(summarize(collectSteps(following("url", { stage: "fetching" }, { stage: "chunking" }, { stage: "embedding" })))).toEqual([
      ...base, "completed:获取视频信息与字幕", "completed:切分段落", "running:生成检索向量",
    ]);
  });

  it("shows transcription in the finished list only when it was reported", () => {
    const withAsr = collectSteps(following("url", { stage: "transcribing" }, { lifecycle: "ready", stage: "ready" }));
    expect(withAsr.map((step) => step.text)).toContain("语音转写中");
    const without = collectSteps(following("url", { stage: "fetching" }, { lifecycle: "ready", stage: "ready" }));
    expect(without.map((step) => step.text)).not.toContain("语音转写中");
    expect(without.at(-1)).toMatchObject({ outcome: "completed", text: "完成，可以在资料库里搜索和提问" });
  });
});

describe("collectSteps for an ended item", () => {
  it("says an item that was already in the library is there, without inventing steps", () => {
    const existing = applyItem(
      entry({ phase: "tracking", path: "url", fallback: "read_failed", itemPublicId: "item-1", existing: true }),
      libraryItem({ lifecycle: "ready", stage: "ready" }),
      T0,
    );
    expect(existing.outcome).toBe("already_exists");
    expect(summarize(collectSteps(existing))).toEqual([
      "skipped:没能读取页面字幕，改用链接导入", "completed:已提交", "completed:已在资料库中",
    ]);
  });

  it("needs action after the steps that did run", () => {
    const steps = collectSteps(following("url", { stage: "fetching" }, { lifecycle: "needs_action", stage: "needs_action" }));
    expect(summarize(steps)).toEqual([
      "skipped:没能读取页面字幕，改用链接导入", "completed:已提交", "completed:获取视频信息与字幕",
      "failed:需要处理：没有可用字幕，需要语音转写或从浏览器读取字幕",
    ]);
  });

  it("turns the step that was running into the failure, with the reason and a retry hint", () => {
    const failed = following(
      "url",
      { stage: "fetching" },
      { stage: "chunking" },
      { lifecycle: "failed", stage: "failed", error_code: "transcript_invalid", available_actions: ["retry"] },
    );
    expect(summarize(collectSteps(failed))).toEqual([
      "skipped:没能读取页面字幕，改用链接导入", "completed:已提交", "completed:获取视频信息与字幕",
      "failed:未能完成：字幕内容无法解析，可以在工作区重试",
    ]);
  });

  it("falls back to a generic reason for an unknown error code and omits the retry hint without the action", () => {
    const failed = following("capture", { lifecycle: "failed", stage: "failed", error_code: "something_new" });
    expect(collectSteps(failed).at(-1)).toMatchObject({ outcome: "failed", text: "未能完成：处理出错" });
  });

  it("covers archived and the 30 minute cap", () => {
    expect(collectSteps(following("url", { stage: "fetching" }, { lifecycle: "archived", stage: "archived" })).at(-1))
      .toMatchObject({ outcome: "skipped", text: "这个视频已被归档" });
    const timedOut = finishEntry(following("url", { stage: "chunking" }), "timeout");
    expect(summarize(collectSteps(timedOut)).slice(-2)).toEqual(["completed:获取视频信息与字幕", "skipped:切分段落"]);
  });
});

describe("collectSteps when nothing was queued", () => {
  it("explains each rejected submission", () => {
    const base = entry({ phase: "done", path: "url", fallback: "no_captions" });
    expect(collectSteps({ ...base, outcome: "quota_exceeded" }).at(-1)).toMatchObject({ outcome: "failed", text: "未能收藏：收藏数量已达上限" });
    expect(collectSteps({ ...base, outcome: "unsupported_url" }).at(-1)).toMatchObject({ outcome: "failed", text: "未能收藏：暂不支持直接导入这个视频" });
    expect(collectSteps({ ...base, outcome: "unsupported_part" }).at(-1)).toMatchObject({ outcome: "failed", text: "暂不支持收藏这一 P，可以在第 1P 收藏" });
    expect(collectSteps({ ...base, outcome: "submit_failed", errorCode: "network_unavailable" }).at(-1))
      .toMatchObject({ outcome: "failed", text: "未能收藏：无法连接搜藏助手，请确认服务正在运行。" });
    expect(collectSteps({ ...base, outcome: "submit_failed", errorCode: "capture_conflict" }).at(-1))
      .toMatchObject({ text: "未能收藏：这个视频正在保存或处理中，请稍后再试。" });
  });

  it("shows a submitted item that cannot be followed", () => {
    const submitted = entry({ phase: "done", path: "url", fallback: "read_failed", outcome: "submitted" });
    expect(summarize(collectSteps(submitted))).toEqual(["skipped:没能读取页面字幕，改用链接导入", "completed:已提交"]);
  });
});

describe("collectSummary, collectResult and collectWorkspacePath", () => {
  it("names the running step, then the way the action ended", () => {
    const running = following("url", { stage: "chunking" });
    expect(collectSummary(running, collectSteps(running))).toBe("收藏视频 · 切分段落");
    const outcomes: Array<[CollectEntry["outcome"], string]> = [
      ["ready", "收藏视频 · 已完成"], ["already_exists", "收藏视频 · 已在资料库中"], ["needs_action", "收藏视频 · 需要处理"],
      ["failed", "收藏视频 · 未能完成"], ["archived", "收藏视频 · 已归档"], ["submitted", "收藏视频 · 已提交"],
      ["timeout", "收藏视频 · 仍在处理"], ["quota_exceeded", "收藏视频 · 未能提交"], ["unsupported_part", "收藏视频 · 未能提交"],
    ];
    for (const [outcome, text] of outcomes) {
      const ended = { ...entry({ phase: "done", path: "url" }), outcome };
      expect(collectSummary(ended, [])).toBe(text);
    }
  });

  it("closes with a message only where the steps do not say it", () => {
    const ended = (outcome: NonNullable<CollectEntry["outcome"]>) => ({ ...entry({ phase: "done" }), outcome });
    expect(collectResult(ended("ready"))).toEqual({ tone: "success", text: "已收藏到资料库。" });
    expect(collectResult(ended("already_exists"))).toEqual({ tone: "success", text: "这个视频已经在资料库里了。" });
    expect(collectResult(ended("submitted"))?.tone).toBe("action");
    expect(collectResult(ended("timeout"))?.text).toContain("已停止自动刷新");
    expect(collectResult(ended("failed"))).toBeNull();
    expect(collectResult(ended("quota_exceeded"))).toBeNull();
  });

  it("links to the item, or to the library when its id is unknown, and not at all when nothing was saved", () => {
    const ended = (outcome: NonNullable<CollectEntry["outcome"]>, itemPublicId: string | null) => ({ ...entry({ phase: "done", itemPublicId }), outcome });
    expect(collectWorkspacePath(ended("ready", "item-1"))).toBe("/videos/item-1");
    expect(collectWorkspacePath(ended("needs_action", "item-1"))).toBe("/videos/item-1");
    expect(collectWorkspacePath(ended("submitted", null))).toBe("/library");
    expect(collectWorkspacePath(ended("quota_exceeded", null))).toBeNull();
    expect(collectWorkspacePath(ended("unsupported_url", null))).toBeNull();
    expect(collectWorkspacePath(ended("unsupported_part", null))).toBeNull();
    expect(collectWorkspacePath(entry({ phase: "tracking", itemPublicId: "item-1" }))).toBeNull();
  });
});

describe("collectProgress", () => {
  const tracked = (overrides: Partial<CollectEntry> = {}) => ({
    ...following("url", { stage: "fetching", duration_sec: 600 }),
    startedAt: T0,
    stageSince: T0,
    ...overrides,
  });

  it("shows elapsed time and the ETA while tracking", () => {
    // 20 s into a 40 s fetching stage: 20 + 25 + 35 = 80 s, so about 2 minutes.
    expect(collectProgress(tracked(), T0 + 20_000)).toBe("已用时 20 秒 · 预计还需约 2 分钟");
  });

  it("says less than a minute near the end", () => {
    // 23 s into the 35 s embedding stage.
    const near = tracked({ stage: "embedding", workingStage: "embedding", stageSince: T0 + 60_000 });
    expect(collectProgress(near, T0 + 83_000)).toBe("已用时 1 分 23 秒 · 预计还需不到 1 分钟");
  });

  it("switches to the overrun copy when a stage runs long", () => {
    expect(collectProgress(tracked(), T0 + 100_000)).toBe("已用时 1 分 40 秒 · 比预计久，仍在处理");
  });

  it("shows elapsed time only when the duration is unknown", () => {
    expect(collectProgress(tracked({ durationSec: null }), T0 + 7_000)).toBe("已用时 7 秒");
  });

  it("shows elapsed time only before the item is known, and nothing once ended", () => {
    expect(collectProgress(entry({ phase: "submitting" }), T0 + 4_000)).toBe("已用时 4 秒");
    expect(collectProgress(finishEntry(tracked(), "ready"), T0 + 4_000)).toBeNull();
  });

  it("mentions an unstable connection while polling backs off", () => {
    expect(collectProgress(tracked({ retrying: true }), T0 + 20_000)).toBe("已用时 20 秒 · 预计还需约 2 分钟 · 连接不稳定，正在重试");
  });

  it("counts the transcription stage for a caption-less URL import", () => {
    const captionless = tracked({ captionless: true });
    // 40 + 180 + 25 + 35 = 280 s minus nothing elapsed in stage -> 5 minutes.
    expect(collectProgress(captionless, T0)).toBe("已用时 0 秒 · 预计还需约 5 分钟");
  });
});

describe("qaSteps", () => {
  const turn = (overrides: Partial<QaTurn> = {}): QaTurn => ({
    kind: "qa",
    id: "t1",
    question: "问",
    answer: "",
    citations: [],
    status: "streaming",
    notFound: false,
    error: null,
    reading: { captioned: true, segments: 42, language: "en" },
    playback: undefined,
    ...overrides,
  });
  const language = new Intl.DisplayNames(["zh-CN"], { type: "language" }).of("en") as string;

  it("runs the playback read and the generation until they finish", () => {
    expect(summarize(qaSteps(turn()))).toEqual([`completed:读取视频字幕（42 段 · ${language}）`, "running:读取播放位置", "running:生成回答"]);
    expect(summarize(qaSteps(turn({ playback: 90 })))).toEqual([`completed:读取视频字幕（42 段 · ${language}）`, "completed:播放位置 1:30", "running:生成回答"]);
  });

  it("marks generation done at the first text and at the end", () => {
    expect(qaSteps(turn({ playback: 3, answer: "好" })).at(-1)).toMatchObject({ outcome: "completed" });
    expect(qaSteps(turn({ playback: 3, status: "done" })).at(-1)).toMatchObject({ outcome: "completed" });
  });

  it("marks a failure, a stop, a missing position and a caption-less video", () => {
    expect(qaSteps(turn({ status: "failed" })).at(-1)).toMatchObject({ outcome: "failed" });
    expect(qaSteps(turn({ status: "stopped" })).at(-1)).toMatchObject({ outcome: "skipped" });
    expect(qaSteps(turn({ playback: null }))[1]).toMatchObject({ outcome: "skipped", text: "没有读到播放位置" });
    expect(qaSteps(turn({ reading: { captioned: false, segments: 0, language: null } }))[0]).toMatchObject({ text: "该视频没有字幕，使用标题和简介" });
    expect(qaSteps(turn({ reading: { captioned: true, segments: 3, language: null } }))[0]?.text).toBe("读取视频字幕（3 段）");
  });

  it("summarizes the running step, then the step count", () => {
    expect(qaSummary(qaSteps(turn()))).toBe("读取播放位置");
    expect(qaSummary(qaSteps(turn({ playback: 1, status: "done" })))).toBe("回答过程 · 3 个步骤");
  });
});
