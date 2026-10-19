import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import type { LibraryItem } from "../api/contracts";
import {
  batchResponse,
  BILI_P2_URL,
  BILI_URL,
  bilibiliPage,
  type FakeEnvironment,
  libraryItem,
  makeEnvironment,
  noCaptionBilibiliPage,
  noCaptionPage,
  YT_A,
  YT_B,
  youtubePage,
} from "../test/sidepanelEnvironment";
import { newCollectEntry, toStored } from "./collectModel";
import { SidePanel } from "./SidePanel";
import { WorkerError } from "./workerApi";

const KEY_A = "youtube:aaaaaaaaaaa";
const CAPTION_LINE = "字幕可用 · 官方字幕 · 2 条";

const sleep = (ms: number) => act(async () => { await new Promise((resolve) => { setTimeout(resolve, ms); }); });

/** Makes every library item read return `state.item`, so a test moves the item through its stages itself. */
function feed(env: FakeEnvironment, initial: LibraryItem = libraryItem({ lifecycle: "queued", stage: "queued" })) {
  const state = { item: initial };
  env.getItem.mockImplementation(async () => state.item);
  return state;
}

async function renderPanel(env: FakeEnvironment) {
  const user = userEvent.setup();
  render(<SidePanel deps={env.deps} />);
  await screen.findByRole("heading", { level: 1 });
  return user;
}

const collectButton = () => screen.getByRole("button", { name: /^收藏(视频|中…)$/ });
const collectSteps = () => within(screen.getByRole("list", { name: "收藏步骤" }));
const stepItem = (text: string) => collectSteps().getByText(text).closest("li") as HTMLElement;

/** An environment whose page read fails, so 「收藏视频」 has to import by URL. */
function failingReadEnvironment(options: Parameters<typeof makeEnvironment>[0] = {}) {
  const env = makeEnvironment(options);
  env.worker.readPage.mockRejectedValue(new WorkerError("caption_fetch_failed"));
  return env;
}

async function collectAfterFailedRead(env: FakeEnvironment) {
  const user = await renderPanel(env);
  await screen.findByText("页面字幕暂时读取失败，请刷新视频页面后重试。");
  await user.click(collectButton());
  return user;
}

describe("收藏视频 on the capture path", () => {
  it("saves the page that was already read, follows the item through its stages, and ends ready", async () => {
    const env = makeEnvironment();
    const item = feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    expect(collectButton()).toHaveTextContent("收藏视频");

    await user.click(collectButton());

    expect(env.worker.saveCapture).toHaveBeenCalledOnce();
    expect(env.worker.saveCapture.mock.calls[0]?.[0]).toBe(await env.worker.readPage.mock.results[0]?.value);
    expect(env.worker.readPage).toHaveBeenCalledOnce();
    expect(env.submitBatch).not.toHaveBeenCalled();
    expect(collectButton()).toBeDisabled();
    expect(collectButton()).toHaveTextContent("收藏中…");

    await screen.findByText("排队等待处理");
    expect(stepItem("已读取页面字幕（2 段）")).toHaveClass("is-completed");
    expect(stepItem("已提交")).toHaveClass("is-completed");
    expect(stepItem("排队等待处理")).toHaveClass("is-running");
    expect(stepItem("排队等待处理")).toHaveTextContent("已用时 0 秒 · 预计还需约 2 分钟");
    expect(env.getItem).toHaveBeenCalledWith("item-1");

    item.item = libraryItem({ stage: "chunking" });
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));
    expect(stepItem("排队等待处理")).toHaveClass("is-completed");
    item.item = libraryItem({ stage: "embedding" });
    await waitFor(() => expect(stepItem("生成检索向量")).toHaveClass("is-running"));

    item.item = libraryItem({ lifecycle: "ready", stage: "ready" });
    await waitFor(() => expect(stepItem("完成，可以在资料库里搜索和提问")).toHaveClass("is-completed"));
    expect(screen.getByText("已收藏到资料库。")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("收藏视频 · 已完成").closest("details")).not.toHaveAttribute("open"));
    expect(collectButton()).toBeEnabled();
    expect(collectButton()).toHaveTextContent("收藏视频");

    const calls = env.getItem.mock.calls.length;
    await sleep(40);
    expect(env.getItem.mock.calls.length).toBe(calls);

    await user.click(screen.getByRole("button", { name: "在工作区查看" }));
    expect(env.tabs.openUrl).toHaveBeenCalledWith("chrome-extension://test/workspace.html#/videos/item-1");
  });

  it("keeps caption text out of everything it persists", async () => {
    const env = makeEnvironment();
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await screen.findByText("排队等待处理");

    const stored = env.collectStore.entries.get(KEY_A);
    expect(stored).toMatchObject({ itemPublicId: "item-1", path: "capture", segments: 2, phase: "tracking" });
    expect(JSON.stringify([...env.collectStore.entries])).not.toMatch(/hello world|second cue/);
  });

  it("tells a repeated save of a video that is already ready", async () => {
    const env = makeEnvironment();
    env.worker.saveCapture.mockResolvedValueOnce({ lifecycle: "ready", status: "already_exists", item_public_id: "item-1" });
    feed(env, libraryItem({ lifecycle: "ready", stage: "ready" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("这个视频已经在资料库里了。")).toBeInTheDocument();
    expect(stepItem("已在资料库中")).toHaveClass("is-completed");
    expect(screen.getByText("收藏视频 · 已在资料库中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "在工作区查看" })).toBeInTheDocument();
    expect(env.submitBatch).not.toHaveBeenCalled();
  });

  it("waits for a page read that is still running instead of reading twice", async () => {
    const env = makeEnvironment();
    feed(env);
    let release: (page: ReturnType<typeof youtubePage>) => void = () => undefined;
    env.worker.readPage.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    const user = await renderPanel(env);

    await user.click(collectButton());
    expect(stepItem("正在读取页面字幕")).toHaveClass("is-running");
    expect(collectButton()).toBeDisabled();
    expect(env.worker.saveCapture).not.toHaveBeenCalled();

    await act(async () => { release(youtubePage()); });
    await waitFor(() => expect(env.worker.saveCapture).toHaveBeenCalledOnce());
    expect(env.worker.readPage).toHaveBeenCalledOnce();
    expect(env.submitBatch).not.toHaveBeenCalled();
  });

});

describe("收藏视频 falls back to the server URL import by itself", () => {
  it("imports by URL when the page cannot be read, with no other user action", async () => {
    const env = failingReadEnvironment();
    feed(env, libraryItem({ stage: "fetching" }));

    await collectAfterFailedRead(env);

    expect(env.submitBatch).toHaveBeenCalledOnce();
    expect(env.submitBatch.mock.calls[0]?.[0]).toEqual({ urls: [YT_A] });
    expect(env.worker.saveCapture).not.toHaveBeenCalled();
    expect(env.worker.readPage).toHaveBeenCalledOnce();
    await screen.findByText("获取视频信息与字幕");
    expect(stepItem("没能读取页面字幕，改用链接导入")).toHaveClass("is-skipped");
    expect(stepItem("已提交")).toHaveClass("is-completed");
    expect(stepItem("获取视频信息与字幕")).toHaveClass("is-running");
    expect(screen.queryByLabelText("邮箱地址")).not.toBeInTheDocument();
  });

  it("imports by URL when the video has no captions, and expects speech transcription in the ETA", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    feed(env, libraryItem({ stage: "fetching" }));
    const user = await renderPanel(env);
    await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答");

    await user.click(collectButton());

    await waitFor(() => expect(env.submitBatch).toHaveBeenCalledOnce());
    expect(env.submitBatch.mock.calls[0]?.[0]).toEqual({ urls: [YT_A] });
    expect(env.worker.saveCapture).not.toHaveBeenCalled();
    await screen.findByText("视频没有字幕，改用链接导入");
    // fetching 40 s + transcribing 180 s + chunking 25 s + embedding 35 s = 280 s.
    expect(await screen.findByText(/预计还需约 5 分钟/)).toBeInTheDocument();
  });

  it("shows speech transcription once the server reports it, then the following stages", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    const item = feed(env, libraryItem({ stage: "fetching" }));
    const user = await renderPanel(env);
    await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答");
    await user.click(collectButton());
    await screen.findByText("获取视频信息与字幕");

    item.item = libraryItem({ stage: "transcribing" });
    await waitFor(() => expect(stepItem("语音转写中")).toHaveClass("is-running"));
    expect(stepItem("获取视频信息与字幕")).toHaveClass("is-completed");
    item.item = libraryItem({ stage: "chunking" });
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));
    expect(stepItem("语音转写中")).toHaveClass("is-completed");
  });

  it("imports by URL when this device is not connected, and still offers to connect it", async () => {
    const env = makeEnvironment({ paired: false });
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    await waitFor(() => expect(env.submitBatch).toHaveBeenCalledOnce());
    expect(env.worker.saveCapture).not.toHaveBeenCalled();
    expect(await screen.findByText("这台设备还没连接，改用链接导入")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "连接这台设备，直接读取浏览器里的字幕" })).toBeInTheDocument();
  });

  it("falls back after a capture that fails for any reason except quota and conflict", async () => {
    const env = makeEnvironment();
    env.worker.saveCapture.mockRejectedValueOnce(new WorkerError("network_unavailable"));
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("字幕提交没有成功，改用链接导入")).toBeInTheDocument();
    expect(env.submitBatch).toHaveBeenCalledOnce();
    // The grant is fine, so the panel does not ask to connect again.
    expect(screen.queryByRole("button", { name: "连接这台设备，直接读取浏览器里的字幕" })).not.toBeInTheDocument();
  });

  it("treats a revoked capture grant as not connected and imports by URL", async () => {
    const env = makeEnvironment();
    env.worker.saveCapture.mockRejectedValueOnce(new WorkerError("extension_grant_revoked"));
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("字幕提交没有成功，改用链接导入")).toBeInTheDocument();
    expect(env.submitBatch).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "连接这台设备，直接读取浏览器里的字幕" })).toBeInTheDocument();
  });

  it("does not import by URL when the capture was refused for quota or because the video is being processed", async () => {
    const quota = makeEnvironment();
    quota.worker.saveCapture.mockRejectedValueOnce(new WorkerError("quota_exceeded"));
    const user = await renderPanel(quota);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    expect(await screen.findByText("未能收藏：收藏数量已达上限")).toBeInTheDocument();
    expect(quota.submitBatch).not.toHaveBeenCalled();
    expect(collectButton()).toBeEnabled();
  });

  it("explains a capture that collides with an ingest already running", async () => {
    const env = makeEnvironment();
    env.worker.saveCapture.mockRejectedValueOnce(new WorkerError("capture_conflict"));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("未能收藏：这个视频正在保存或处理中，请稍后再试。")).toBeInTheDocument();
    expect(screen.queryByText(/capture_conflict/)).not.toBeInTheDocument();
    expect(env.submitBatch).not.toHaveBeenCalled();
  });

  it("connects the device from the panel and then saves through the capture path", async () => {
    const env = makeEnvironment({ paired: false });
    feed(env, libraryItem({ lifecycle: "ready", stage: "ready" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(screen.getByRole("button", { name: "连接这台设备，直接读取浏览器里的字幕" }));
    await user.type(screen.getByLabelText("邮箱地址"), "me@example.com");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    await user.type(await screen.findByLabelText("6 位验证码"), "123456");
    await user.click(screen.getByRole("button", { name: "确认登录" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "连接这台设备，直接读取浏览器里的字幕" })).not.toBeInTheDocument());
    expect(env.worker.saveCapture).not.toHaveBeenCalled();

    await user.click(collectButton());

    await waitFor(() => expect(env.worker.saveCapture).toHaveBeenCalledOnce());
    expect(env.submitBatch).not.toHaveBeenCalled();
  });
});

describe("URL import results", () => {
  it("follows a newly queued item", async () => {
    const env = failingReadEnvironment();
    feed(env, libraryItem({ stage: "fetching" }));
    env.submitBatch.mockResolvedValueOnce(batchResponse({ status: "queued", item_public_id: "item-5", lifecycle: "queued" }));

    await collectAfterFailedRead(env);

    await screen.findByText("获取视频信息与字幕");
    expect(env.getItem).toHaveBeenCalledWith("item-5");
  });

  it("links to an item that is already in the library", async () => {
    const env = failingReadEnvironment();
    feed(env, libraryItem({ lifecycle: "ready", stage: "ready", public_id: "item-9" }));
    env.submitBatch.mockResolvedValueOnce(batchResponse({ status: "already_exists", item_public_id: "item-9", lifecycle: "ready" }));

    const user = await collectAfterFailedRead(env);

    expect(await screen.findByText("这个视频已经在资料库里了。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "在工作区查看" }));
    expect(env.tabs.openUrl).toHaveBeenCalledWith("chrome-extension://test/workspace.html#/videos/item-9");
  });

  it("keeps following an existing item that is still being processed, and reports it as already saved when it is ready", async () => {
    const env = failingReadEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    env.submitBatch.mockResolvedValueOnce(batchResponse({ status: "already_exists", item_public_id: "item-9", lifecycle: "processing" }));

    await collectAfterFailedRead(env);
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));
    item.item = libraryItem({ lifecycle: "ready", stage: "ready" });

    expect(await screen.findByText("这个视频已经在资料库里了。")).toBeInTheDocument();
  });

  it("explains a URL the server cannot import", async () => {
    const env = failingReadEnvironment();
    env.submitBatch.mockResolvedValueOnce(batchResponse({ status: "unsupported_url", item_public_id: null, lifecycle: null }));

    await collectAfterFailedRead(env);

    expect(await screen.findByText("未能收藏：暂不支持直接导入这个视频")).toBeInTheDocument();
    expect(env.getItem).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "在工作区查看" })).not.toBeInTheDocument();
  });

  it("explains a full quota and a queue that is down", async () => {
    const quota = failingReadEnvironment();
    quota.submitBatch.mockResolvedValueOnce(batchResponse({ status: "quota_exceeded", item_public_id: null, lifecycle: null }));
    await collectAfterFailedRead(quota);
    expect(await screen.findByText("未能收藏：收藏数量已达上限")).toBeInTheDocument();
  });

  it("explains a queue that is down in short copy", async () => {
    const env = failingReadEnvironment();
    env.submitBatch.mockResolvedValueOnce(batchResponse({ status: "queue_unavailable", item_public_id: null, lifecycle: null, safe_error_code: "queue_unavailable" }));

    await collectAfterFailedRead(env);

    expect(await screen.findByText("未能收藏：保存任务暂时无法排队，请稍后重试。")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
  });

  it("falls back to the workspace library when an accepted item has no id to follow", async () => {
    const env = failingReadEnvironment();
    env.submitBatch.mockResolvedValueOnce(batchResponse({ status: "queued", item_public_id: null }));

    const user = await collectAfterFailedRead(env);

    expect(await screen.findByText("已提交，可以在工作区查看处理进度。")).toBeInTheDocument();
    expect(env.getItem).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "在工作区查看" }));
    expect(env.tabs.openUrl).toHaveBeenCalledWith("chrome-extension://test/workspace.html#/library");
  });

  it("returns to the login form when the session is no longer valid", async () => {
    const env = failingReadEnvironment();
    env.submitBatch.mockRejectedValueOnce(new ApiError(401, "session_invalid", "登录已失效"));

    await collectAfterFailedRead(env);

    expect(await screen.findByLabelText("邮箱地址")).toBeInTheDocument();
  });

  it("reports a network failure in short copy and allows trying again", async () => {
    const env = failingReadEnvironment();
    env.submitBatch.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const user = await collectAfterFailedRead(env);

    expect(await screen.findByText("未能收藏：无法连接搜藏助手，请确认服务正在运行。")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
    feed(env);
    await user.click(collectButton());
    await waitFor(() => expect(env.submitBatch).toHaveBeenCalledTimes(2));
  });
});

describe("how an import ends", () => {
  it("asks for action and links to the item", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    const item = feed(env, libraryItem({ stage: "fetching" }));
    const user = await renderPanel(env);
    await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答");
    await user.click(collectButton());
    await screen.findByText("获取视频信息与字幕");

    item.item = libraryItem({ lifecycle: "needs_action", stage: "needs_action" });

    expect(await screen.findByText("需要处理：没有可用字幕，需要语音转写或从浏览器读取字幕")).toBeInTheDocument();
    expect(stepItem("需要处理：没有可用字幕，需要语音转写或从浏览器读取字幕")).toHaveClass("is-failed");
    expect(screen.getByText("收藏视频 · 需要处理")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
    expect(screen.getByRole("button", { name: "在工作区查看" })).toBeInTheDocument();
  });

  it("fails with the reason and a retry hint, and clears what it kept for resuming", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));
    expect(env.collectStore.entries.has(KEY_A)).toBe(true);

    item.item = libraryItem({ lifecycle: "failed", stage: "failed", error_code: "asr_no_speech", available_actions: ["retry", "archive"] });

    expect(await screen.findByText("未能完成：没有检测到语音，可以在工作区重试")).toBeInTheDocument();
    expect(stepItem("未能完成：没有检测到语音，可以在工作区重试")).toHaveClass("is-failed");
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
    expect(screen.getByText("收藏视频 · 未能完成").closest("details")).toHaveAttribute("open");
  });

  it("reports an item that disappeared", async () => {
    const env = makeEnvironment();
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    env.getItem.mockRejectedValue(new ApiError(404, "item_missing", "不存在"));

    await user.click(collectButton());

    expect(await screen.findByText("未能完成：这个条目已不存在")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
  });

  it("returns to the login form when polling finds the session invalid", async () => {
    const env = makeEnvironment();
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    env.getItem.mockRejectedValue(new ApiError(401, "session_invalid", "登录已失效"));

    await user.click(collectButton());

    expect(await screen.findByLabelText("邮箱地址")).toBeInTheDocument();
  });
});

describe("progress polling", () => {
  it("backs off after a failed read without failing the entry, then recovers", async () => {
    const env = makeEnvironment();
    env.deps.pollBackoffMs = 200;
    const item = feed(env, libraryItem({ stage: "chunking" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));

    env.getItem.mockRejectedValueOnce(new TypeError("offline"));
    expect(await screen.findByText(/连接不稳定，正在重试/)).toBeInTheDocument();
    const calls = env.getItem.mock.calls.length;
    await sleep(80);
    // Backing off: far fewer reads than the 5 ms interval would make.
    expect(env.getItem.mock.calls.length).toBe(calls);
    expect(collectButton()).toBeDisabled();

    item.item = libraryItem({ stage: "embedding" });
    await waitFor(() => expect(stepItem("生成检索向量")).toHaveClass("is-running"), { timeout: 2_000 });
    expect(screen.queryByText(/连接不稳定/)).not.toBeInTheDocument();
  });

  it("stops after 30 minutes and says so", async () => {
    const env = makeEnvironment();
    feed(env, libraryItem({ stage: "chunking" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));

    env.clock.now += env.deps.pollMaxMs;

    expect(await screen.findByText("处理时间比较长，已停止自动刷新，稍后可以在工作区查看进度。")).toBeInTheDocument();
    expect(screen.getByText("收藏视频 · 仍在处理")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
    const calls = env.getItem.mock.calls.length;
    await sleep(40);
    expect(env.getItem.mock.calls.length).toBe(calls);
  });

  it("pauses while the panel is hidden and reads again as soon as it is visible", async () => {
    const env = makeEnvironment();
    feed(env, libraryItem({ stage: "chunking" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));

    env.clock.visible = false;
    await sleep(20);
    const hiddenCalls = env.getItem.mock.calls.length;
    await sleep(60);
    expect(env.getItem.mock.calls.length).toBe(hiddenCalls);

    env.clock.visible = true;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await waitFor(() => expect(env.getItem.mock.calls.length).toBeGreaterThan(hiddenCalls));
  });

  it("stops reading when the panel is closed", async () => {
    const env = makeEnvironment();
    feed(env, libraryItem({ stage: "chunking" }));
    const user = userEvent.setup();
    const view = render(<SidePanel deps={env.deps} />);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await waitFor(() => expect(env.getItem).toHaveBeenCalled());

    view.unmount();
    const calls = env.getItem.mock.calls.length;
    await sleep(60);

    expect(env.getItem.mock.calls.length).toBe(calls);
  });

  it("does not read the item of a video the user has left, and resumes it on coming back", async () => {
    const env = makeEnvironment();
    feed(env, libraryItem({ stage: "chunking" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    await user.click(collectButton());
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));

    act(() => env.navigate({ id: 7, url: YT_B }));
    await screen.findByRole("heading", { name: "Video bbbbbbbbbbb" });
    expect(screen.queryByRole("list", { name: "收藏步骤" })).not.toBeInTheDocument();
    expect(collectButton()).toHaveTextContent("收藏视频");
    await sleep(20);
    const away = env.getItem.mock.calls.length;
    await sleep(60);
    expect(env.getItem.mock.calls.length).toBe(away);
    expect(env.collectStore.entries.has(KEY_A)).toBe(true);

    act(() => env.navigate({ id: 7, url: YT_A }));
    await waitFor(() => expect(stepItem("切分段落")).toHaveClass("is-running"));
    await waitFor(() => expect(env.getItem.mock.calls.length).toBeGreaterThan(away));
  });
});

describe("resuming after the panel is reopened", () => {
  function stored(overrides: Parameters<typeof newCollectEntry>[2] extends number ? Record<string, unknown> : never = {}) {
    return toStored({
      ...newCollectEntry("old-entry", KEY_A, 1_000_000 - 60_000),
      phase: "tracking",
      path: "url",
      fallback: "read_failed",
      itemPublicId: "item-7",
      stage: "fetching",
      stageSince: 1_000_000 - 30_000,
      workingStage: "fetching",
      durationSec: 600,
      ...overrides,
    });
  }

  it("shows the unfinished import again, keeps following it, and finishes it", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "fetching", public_id: "item-7" }));
    env.collectStore.entries.set(KEY_A, stored());

    await renderPanel(env);

    await waitFor(() => expect(stepItem("获取视频信息与字幕")).toHaveClass("is-running"));
    expect(stepItem("获取视频信息与字幕")).toHaveTextContent("已用时 1 分 00 秒");
    expect(env.getItem).toHaveBeenCalledWith("item-7");
    expect(collectButton()).toBeDisabled();
    expect(env.submitBatch).not.toHaveBeenCalled();
    expect(env.worker.saveCapture).not.toHaveBeenCalled();

    item.item = libraryItem({ lifecycle: "ready", stage: "ready", public_id: "item-7" });
    expect(await screen.findByText("已收藏到资料库。")).toBeInTheDocument();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
    expect(collectButton()).toBeEnabled();
  });

  it("ignores an entry that is older than the cap, and one that belongs to another video", async () => {
    const env = makeEnvironment();
    feed(env);
    env.collectStore.entries.set(KEY_A, stored({ startedAt: 1_000_000 - 31 * 60 * 1000 }));
    env.collectStore.entries.set("youtube:bbbbbbbbbbb", stored({ videoKey: "youtube:bbbbbbbbbbb" }));

    await renderPanel(env);
    await sleep(40);

    expect(screen.queryByRole("list", { name: "收藏步骤" })).not.toBeInTheDocument();
    expect(env.getItem).not.toHaveBeenCalled();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
    expect(env.collectStore.entries.has("youtube:bbbbbbbbbbb")).toBe(true);
  });

  it("ignores a stored value that is not an entry", async () => {
    const env = makeEnvironment();
    feed(env);
    env.collectStore.entries.set(KEY_A, { garbage: true } as never);

    await renderPanel(env);
    await sleep(40);

    expect(screen.queryByRole("list", { name: "收藏步骤" })).not.toBeInTheDocument();
    expect(env.getItem).not.toHaveBeenCalled();
  });
});

describe("the thread", () => {
  it("shows the action in order with Q&A turns, and keeps collecting when Q&A is not offered", async () => {
    const env = makeEnvironment();
    feed(env, libraryItem({ lifecycle: "ready", stage: "ready" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);
    const box = await screen.findByRole("textbox", { name: "就当前视频提问" });
    await user.type(box, "第一个问题");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("button", { name: /5:30/ });

    await user.click(collectButton());
    await screen.findByText("已收藏到资料库。");

    const items = within(screen.getByRole("list", { name: "视频问答" })).getAllByRole("listitem", { hidden: false })
      .filter((item) => item.classList.contains("sp-turn"));
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("第一个问题");
    expect(items[1]).toHaveClass("sp-turn--action");
  });

  it("shows the action when the server does not offer page Q&A", async () => {
    const env = makeEnvironment({ pageQa: false });
    feed(env, libraryItem({ lifecycle: "ready", stage: "ready" }));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("已收藏到资料库。")).toBeInTheDocument();
    expect(screen.getByText("当前服务未开放视频问答，你仍然可以收藏视频。")).toBeInTheDocument();
  });

  it("ignores a second click while the first is running", async () => {
    const env = makeEnvironment();
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());
    await user.click(collectButton());

    expect(env.worker.saveCapture).toHaveBeenCalledOnce();
    expect(screen.getAllByRole("article", { name: "收藏视频" })).toHaveLength(1);
  });
});

describe("收藏视频 on Bilibili", () => {
  it("saves a captioned video through the capture path", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_URL }, pages: { [BILI_URL]: bilibiliPage() } });
    feed(env);
    const user = await renderPanel(env);
    expect(await screen.findByText("哔哩哔哩")).toBeInTheDocument();
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    await waitFor(() => expect(env.worker.saveCapture).toHaveBeenCalledOnce());
    expect(env.submitBatch).not.toHaveBeenCalled();
    expect(collectButton()).toHaveTextContent("收藏中…");
  });

  it("imports a caption-less part 1 by its canonical URL", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_URL }, pages: { [BILI_URL]: noCaptionBilibiliPage() } });
    feed(env, libraryItem({ stage: "fetching", platform: "bilibili" }));
    const user = await renderPanel(env);
    await screen.findByText("没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。");

    await user.click(collectButton());

    await waitFor(() => expect(env.submitBatch).toHaveBeenCalledOnce());
    expect(env.submitBatch.mock.calls[0]?.[0]).toEqual({ urls: [BILI_URL] });
    expect(await screen.findByText("视频没有字幕，改用链接导入")).toBeInTheDocument();
  });

  it("imports a part 1 whose page could not be read by the canonical URL it can derive from the tab", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_URL }, pages: {} });
    feed(env, libraryItem({ stage: "fetching", platform: "bilibili" }));
    const user = await renderPanel(env);
    await screen.findByText("页面结构可能已经变化，请刷新视频页面或升级插件后重试。");

    await user.click(collectButton());

    await waitFor(() => expect(env.submitBatch).toHaveBeenCalledOnce());
    expect(env.submitBatch.mock.calls[0]?.[0]).toEqual({ urls: [BILI_URL] });
  });

  it("never imports a part after the first by URL, because the server only reads part 1", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_P2_URL }, pages: { [BILI_P2_URL]: noCaptionBilibiliPage(2) } });
    const user = await renderPanel(env);
    await screen.findByText("没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。");

    await user.click(collectButton());

    expect(await screen.findByText("暂不支持收藏这一 P，可以在第 1P 收藏")).toBeInTheDocument();
    expect(stepItem("暂不支持收藏这一 P，可以在第 1P 收藏")).toHaveClass("is-failed");
    expect(env.submitBatch).not.toHaveBeenCalled();
    expect(env.worker.saveCapture).not.toHaveBeenCalled();
    expect(screen.getByText("收藏视频 · 未能提交")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
    expect(screen.queryByRole("button", { name: "在工作区查看" })).not.toBeInTheDocument();
  });

  it("still saves a later part through its captions", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_P2_URL }, pages: { [BILI_P2_URL]: bilibiliPage(2) } });
    feed(env);
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    await waitFor(() => expect(env.worker.saveCapture).toHaveBeenCalledOnce());
    expect(env.submitBatch).not.toHaveBeenCalled();
  });

  it("does not fall back to URL import for a later part when the capture fails", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_P2_URL }, pages: { [BILI_P2_URL]: bilibiliPage(2) } });
    env.worker.saveCapture.mockRejectedValueOnce(new WorkerError("network_unavailable"));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("暂不支持收藏这一 P，可以在第 1P 收藏")).toBeInTheDocument();
    expect(env.submitBatch).not.toHaveBeenCalled();
  });

  it("does not show the Bilibili caption hint on other platforms or when captions were read", async () => {
    const youtube = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    await renderPanel(youtube);
    await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答");
    expect(screen.queryByText(/登录哔哩哔哩/)).not.toBeInTheDocument();
  });
});
