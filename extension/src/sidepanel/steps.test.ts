import { describe, expect, it } from "vitest";

import { applyItem, type CollectEntry, finishEntry, newCollectEntry } from "./collectModel";
import { libraryItem } from "../test/sidepanelEnvironment";
import { collectBody, collectNote, collectProgress, collectResult, collectSteps, collectSummary, collectWorkspacePath, qaSteps, qaSummary } from "./steps";
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

  it("promises transcription for a caption-less video only when the server transcribes its platform", () => {
    const submitting = entry({ phase: "submitting", path: "url", fallback: "no_captions" });
    expect(collectSteps(submitting, true)[0]?.text).toBe("页面没有字幕，收藏后会自动语音转写");
    expect(collectSteps(submitting, false)[0]?.text).toBe("视频没有字幕，改用链接导入");
    // The other reasons have nothing to do with transcription.
    expect(collectSteps(entry({ phase: "submitting", path: "url", fallback: "read_failed" }), true)[0]?.text).toBe("没能读取页面字幕，改用链接导入");
  });
});

describe("collectSteps once the server has the item", () => {
  it("stops at the submission: the ingest card shows everything after it", () => {
    expect(summarize(collectSteps(following("capture", { stage: "chunking" })))).toEqual([
      "completed:已读取页面字幕（12 段）", "completed:已提交",
    ]);
    expect(summarize(collectSteps(following("url", { stage: "fetching" })))).toEqual([
      "skipped:没能读取页面字幕，改用链接导入", "completed:已提交",
    ]);
  });

  it("has no steps of its own for a finished ready item", () => {
    expect(summarize(collectSteps(following("url", { stage: "fetching" }, { lifecycle: "ready", stage: "ready" })))).toEqual([
      "skipped:没能读取页面字幕，改用链接导入", "completed:已提交",
    ]);
  });

  it("closes an archived item and a vanished item with one line each", () => {
    expect(collectSteps(following("url", { stage: "fetching" }, { lifecycle: "archived", stage: "archived" })).at(-1))
      .toMatchObject({ outcome: "skipped", text: "这个视频已被归档" });
    const vanished = finishEntry(following("url", { stage: "fetching" }), "failed", "item_missing");
    expect(collectSteps(vanished).at(-1)).toMatchObject({ outcome: "failed", text: "未能完成：这个条目已不存在" });
    expect(collectSteps(finishEntry(following("url", { stage: "fetching" }), "failed", "x")).at(-1)).toMatchObject({ text: "未能完成：处理出错" });
  });
});

describe("collectBody", () => {
  const unfinished = (...items: Array<Parameters<typeof libraryItem>[0]>) => following("url", { stage: "fetching" }, ...items);

  it("shows steps while reading and submitting, and the progress card once the item is followed", () => {
    expect(collectBody(entry())).toBe("steps");
    expect(collectBody(entry({ phase: "submitting" }))).toBe("steps");
    expect(collectBody(following("capture"))).toBe("progress");
    expect(collectBody(following("url", { stage: "transcribing" }))).toBe("progress");
  });

  it("keeps the progress card for a ready item, an existing one, and one that stopped being followed", () => {
    expect(collectBody(unfinished({ lifecycle: "ready", stage: "ready" }))).toBe("progress");
    expect(collectBody(applyItem({ ...following("url"), existing: true }, libraryItem({ lifecycle: "ready", stage: "ready" }), T0))).toBe("progress");
    expect(collectBody(finishEntry(unfinished(), "timeout"))).toBe("progress");
  });

  it("shows the unfinished card for an item that failed or needs action, with its reason", () => {
    expect(collectBody(unfinished({ lifecycle: "failed", stage: "failed", error_code: "asr_no_speech" }))).toBe("unfinished");
    expect(collectBody(unfinished({ lifecycle: "needs_action", stage: "needs_action" }))).toBe("unfinished");
  });

  it("falls back to steps for an end that has no item to show", () => {
    expect(collectBody(finishEntry(following("url"), "failed", "item_missing"))).toBe("steps");
    expect(collectBody(finishEntry(entry({ phase: "tracking", path: "url" }), "timeout"))).toBe("steps");
    expect(collectBody(unfinished({ lifecycle: "archived", stage: "archived" }))).toBe("steps");
    for (const outcome of ["submitted", "quota_exceeded", "unsupported_url", "unsupported_part", "submit_failed"] as const) {
      expect(collectBody(finishEntry(entry({ path: "url" }), outcome))).toBe("steps");
    }
  });
});

describe("collectNote", () => {
  it("says what was read on the capture path, and why the URL import was used on the other", () => {
    expect(collectNote(following("capture"))).toBe("已读取页面字幕（12 段）");
    expect(collectNote(following("url"))).toBe("没能读取页面字幕，改用链接导入");
    expect(collectNote(entry({ phase: "tracking", path: "url", fallback: "no_captions" }))).toBe("视频没有字幕，改用链接导入");
    expect(collectNote(entry({ phase: "tracking", path: "url", fallback: "no_captions" }), true)).toBe("页面没有字幕，收藏后会自动语音转写");
  });

  it("has nothing to say before the path is known or when there was no fallback", () => {
    expect(collectNote(entry())).toBeNull();
    expect(collectNote(entry({ phase: "tracking", path: "url", fallback: null }))).toBeNull();
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
    const submitting = entry({ phase: "submitting", path: "url" });
    expect(collectSummary(submitting, collectSteps(submitting))).toBe("收藏视频 · 正在提交");
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

  it("calls an item that is being followed submitted", () => {
    const running = following("url", { stage: "chunking" });
    expect(collectSummary(running, collectSteps(running))).toBe("收藏视频 · 已提交");
  });

  it("closes with a message only where the card does not say it", () => {
    const ended = (outcome: NonNullable<CollectEntry["outcome"]>) => ({ ...entry({ phase: "done" }), outcome });
    expect(collectResult(ended("ready"))).toEqual({ tone: "success", text: "已收藏到资料库。" });
    expect(collectResult(ended("already_exists"))).toEqual({ tone: "success", text: "这个视频已经在资料库里了。" });
    expect(collectResult(ended("submitted"))?.tone).toBe("action");
    expect(collectResult(ended("timeout"))?.text).toContain("已停止自动刷新");
    expect(collectResult(ended("failed"))).toBeNull();
    expect(collectResult(ended("needs_action"))).toBeNull();
    expect(collectResult(ended("quota_exceeded"))).toBeNull();
  });

  it("links to the item, or to the library when its id is unknown, and not at all when nothing was saved", () => {
    const ended = (outcome: NonNullable<CollectEntry["outcome"]>, itemPublicId: string | null) => ({ ...entry({ phase: "done", itemPublicId }), outcome });
    expect(collectWorkspacePath(ended("ready", "item-1"))).toBe("/videos/item-1");
    expect(collectWorkspacePath(ended("already_exists", "item 2"))).toBe("/videos/item%202");
    expect(collectWorkspacePath(ended("submitted", null))).toBe("/library");
    expect(collectWorkspacePath(ended("quota_exceeded", null))).toBeNull();
    expect(collectWorkspacePath(ended("unsupported_url", null))).toBeNull();
    expect(collectWorkspacePath(ended("unsupported_part", null))).toBeNull();
    expect(collectWorkspacePath(entry({ phase: "tracking", itemPublicId: "item-1" }))).toBeNull();
  });

  it("leaves the unfinished card to its own buttons instead of a link to the workspace", () => {
    const ended = (outcome: NonNullable<CollectEntry["outcome"]>) => ({ ...entry({ phase: "done", itemPublicId: "item-1" }), outcome });
    expect(collectWorkspacePath(ended("needs_action"))).toBeNull();
    expect(collectWorkspacePath(ended("failed"))).toBeNull();
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

  it("gives only the elapsed time once the body is ready and later stages are still running", () => {
    const waiting = following("url", { stage: "fetching" }, {
      lifecycle: "ready", stage: "ready", body_searchable: true, stages: [{ key: "body", status: "done" }, { key: "classify", status: "running" }],
    });
    expect(waiting.phase).toBe("tracking");
    expect(collectProgress({ ...waiting, startedAt: T0 }, T0 + 9_000)).toBe("已用时 9 秒");
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
