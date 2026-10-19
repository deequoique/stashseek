import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import type { IngestReason, LibraryItem } from "../api/contracts";
import {
  BILI_URL,
  bilibiliPage,
  type FakeEnvironment,
  libraryItem,
  makeEnvironment,
  noCaptionBilibiliPage,
  noCaptionPage,
  YT_A,
} from "../test/sidepanelEnvironment";
import { newCollectEntry, toStored } from "./collectModel";
import { SidePanel } from "./SidePanel";
import { WorkerError } from "./workerApi";

const KEY_A = "youtube:aaaaaaaaaaa";
const CAPTION_LINE = "字幕可用 · 官方字幕 · 2 条";
const WORKSPACE_SEARCH = "chrome-extension://test/workspace.html#/library?focus=search";

const NO_SPEECH: IngestReason = { code: "asr_no_speech", category: "no_speech", retry_useful: false };
const FETCH_FAILED: IngestReason = { code: "transient_fetch_failed", category: "fetch_failed", retry_useful: true };
const LOGIN_REQUIRED: IngestReason = { code: "bilibili_login_required", category: "login_required", retry_useful: false };

const sleep = (ms: number) => act(async () => { await new Promise((resolve) => { setTimeout(resolve, ms); }); });
const collectButton = () => screen.getByRole("button", { name: /^收藏(视频|中…)$/ });
const rows = () => within(screen.getByRole("list", { name: "整理步骤" }));
const row = (text: string) => rows().getByText(text).closest("li") as HTMLElement;
const card = () => within(screen.getByRole("region", { name: "收藏还在，整理没有完成" }));

function feed(env: FakeEnvironment, initial: LibraryItem) {
  const state = { item: initial };
  env.getItem.mockImplementation(async () => state.item);
  return state;
}

const failedItem = (reason: IngestReason, overrides: Partial<LibraryItem> = {}) => libraryItem({
  lifecycle: "failed",
  stage: "failed",
  error_code: reason.code,
  reason,
  stages: [{ key: "body", status: "failed", reason }],
  body_searchable: false,
  available_actions: ["edit_why_saved", "archive", "open_source", "retry"],
  ...overrides,
});

const loginRequiredItem = (overrides: Partial<LibraryItem> = {}) => libraryItem({
  lifecycle: "needs_action",
  stage: "needs_action",
  reason: LOGIN_REQUIRED,
  stages: [{ key: "body", status: "failed", reason: LOGIN_REQUIRED }],
  body_searchable: false,
  platform: "bilibili",
  title: "Bilibili BV1xx411c7mD",
  available_actions: ["edit_why_saved", "archive", "open_source"],
  ...overrides,
});

async function renderPanel(env: FakeEnvironment) {
  const user = userEvent.setup();
  render(<SidePanel deps={env.deps} />);
  await screen.findByRole("heading", { level: 1 });
  return user;
}

/** Collects the YouTube video on screen and waits until the item is followed. */
async function collectAndFollow(env: FakeEnvironment) {
  const user = await renderPanel(env);
  await screen.findByText(CAPTION_LINE);
  await user.click(collectButton());
  await screen.findByRole("list", { name: "整理步骤" });
  return user;
}

describe("retrying from the panel", () => {
  async function failedPanel(reason: IngestReason = NO_SPEECH) {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    const user = await collectAndFollow(env);
    item.item = failedItem(reason);
    await screen.findByRole("heading", { name: "收藏还在，整理没有完成" });
    return { env, item, user };
  }

  it("runs ingestion again on the same item and follows it until it is searchable", async () => {
    const { env, item, user } = await failedPanel();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
    env.retryItem.mockResolvedValueOnce(libraryItem({ lifecycle: "queued", stage: "queued" }));
    item.item = libraryItem({ lifecycle: "queued", stage: "queued" });

    await user.click(card().getByRole("button", { name: "重新整理" }));

    expect(env.retryItem).toHaveBeenCalledOnce();
    expect(env.retryItem).toHaveBeenCalledWith("item-1");
    expect(env.submitBatch).not.toHaveBeenCalled();
    expect(env.worker.saveCapture).toHaveBeenCalledOnce();
    expect(await screen.findByRole("heading", { name: "已经保存，不必等在这里" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "收藏还在，整理没有完成" })).not.toBeInTheDocument();
    expect(collectButton()).toBeDisabled();
    expect(env.collectStore.entries.get(KEY_A)).toMatchObject({ itemPublicId: "item-1", phase: "tracking" });

    item.item = libraryItem({ stage: "chunking" });
    await waitFor(() => expect(row("建立检索索引")).toHaveClass("is-running"));
    item.item = libraryItem({ lifecycle: "ready", stage: "ready", body_searchable: true });
    expect(await screen.findByText("已整理，可检索")).toBeInTheDocument();
    expect(screen.getByText("已收藏到资料库。")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
  });

  it("shows the real reason when the second run fails as well", async () => {
    const { env, item, user } = await failedPanel(NO_SPEECH);
    env.retryItem.mockResolvedValueOnce(libraryItem({ lifecycle: "queued", stage: "queued" }));
    item.item = libraryItem({ lifecycle: "queued", stage: "queued" });
    await user.click(card().getByRole("button", { name: "重新整理" }));
    await screen.findByRole("heading", { name: "已经保存，不必等在这里" });

    item.item = failedItem(FETCH_FAILED);

    expect(await screen.findByText("没能获取视频内容，可能是平台暂时限制了访问，正文暂不可检索。")).toBeInTheDocument();
    expect(screen.queryByText("没有字幕，也没有识别出语音，正文暂不可检索。")).not.toBeInTheDocument();
  });

  it("shows a refusal for lack of quota and keeps the item as it was, ready to try again", async () => {
    const { env, user } = await failedPanel();
    env.retryItem.mockRejectedValueOnce(new ApiError(429, "quota_exceeded", "已达到当前保存额度，请稍后重试"));

    await user.click(card().getByRole("button", { name: "重新整理" }));

    expect(await card().findByRole("alert")).toHaveTextContent("处理额度不足，请稍后再试。");
    expect(screen.getByText("没有字幕，也没有识别出语音，正文暂不可检索。")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "重新整理" })).toBeEnabled();
    expect(collectButton()).toBeEnabled();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
  });

  it("says a retry that is not allowed or fails did not go through", async () => {
    const { env, user } = await failedPanel();
    env.retryItem.mockRejectedValueOnce(new ApiError(409, "retry_unavailable", "当前状态不能重试"));
    await user.click(card().getByRole("button", { name: "重新整理" }));
    expect(await card().findByRole("alert")).toHaveTextContent("操作未完成，请稍后重试。");

    env.retryItem.mockRejectedValueOnce(new TypeError("offline"));
    await user.click(card().getByRole("button", { name: "重新整理" }));
    await waitFor(() => expect(env.retryItem).toHaveBeenCalledTimes(2));
    expect(card().getByRole("alert")).toHaveTextContent("操作未完成，请稍后重试。");
  });

  it("returns to the login form when the retry finds the session invalid", async () => {
    const { env, user } = await failedPanel();
    env.retryItem.mockRejectedValueOnce(new ApiError(401, "session_invalid", "登录已失效"));

    await user.click(card().getByRole("button", { name: "重新整理" }));

    expect(await screen.findByLabelText("邮箱地址")).toBeInTheDocument();
  });

  it("disables the button while the request is in flight, so a double click sends one request", async () => {
    const { env, item, user } = await failedPanel();
    let release: (item: LibraryItem) => void = () => undefined;
    env.retryItem.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));

    await user.click(card().getByRole("button", { name: "重新整理" }));
    expect(card().getByRole("button", { name: "重新整理" })).toBeDisabled();
    expect(collectButton()).toBeDisabled();
    await user.click(card().getByRole("button", { name: "重新整理" }));
    expect(env.retryItem).toHaveBeenCalledOnce();

    item.item = libraryItem({ lifecycle: "queued", stage: "queued" });
    await act(async () => { release(libraryItem({ lifecycle: "queued", stage: "queued" })); });
    expect(await screen.findByRole("heading", { name: "已经保存，不必等在这里" })).toBeInTheDocument();
  });

  it("restarts the 30 minute cap for the new run", async () => {
    const { env, item, user } = await failedPanel();
    env.clock.now += 20 * 60 * 1000;
    env.retryItem.mockResolvedValueOnce(libraryItem({ lifecycle: "queued", stage: "queued" }));
    item.item = libraryItem({ lifecycle: "queued", stage: "queued" });
    await user.click(card().getByRole("button", { name: "重新整理" }));
    await screen.findByRole("heading", { name: "已经保存，不必等在这里" });

    env.clock.now += 15 * 60 * 1000;
    await sleep(30);

    // 35 minutes after the first click, but 15 after the retry: still being followed.
    expect(screen.queryByText(/已停止自动刷新/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "已经保存，不必等在这里" })).toBeInTheDocument();
  });

  it("shows a run the server could not queue as the failure it is", async () => {
    const { env, user } = await failedPanel();
    env.retryItem.mockResolvedValueOnce(failedItem({ code: "queue_unavailable", category: "error", retry_useful: true }));

    await user.click(card().getByRole("button", { name: "重新整理" }));

    expect(await screen.findByText("整理时出错了，正文暂不可检索。")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "重新整理" })).toBeEnabled();
  });
});

describe("a Bilibili video whose captions need a login", () => {
  async function loginPanel() {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_URL }, pages: { [BILI_URL]: noCaptionBilibiliPage() }, transcriptionPlatforms: ["bilibili", "youtube"] });
    const item = feed(env, libraryItem({ stage: "fetching", platform: "bilibili" }));
    const user = await renderPanel(env);
    await screen.findByText("没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。");
    await user.click(collectButton());
    await screen.findByRole("list", { name: "整理步骤" });
    item.item = loginRequiredItem();
    await screen.findByRole("heading", { name: "收藏还在，整理没有完成" });
    return { env, item, user };
  }

  it("asks the user to log in and collect again, instead of offering a server retry", async () => {
    await loginPanel();

    expect(card().getByText("Bilibili BV1xx411c7mD")).toBeInTheDocument();
    expect(card().getByText("B站 · 链接已保存")).toBeInTheDocument();
    expect(card().getByRole("status")).toHaveTextContent("这个视频的字幕需要登录 B站 才能读取，正文暂不可检索。");
    expect(card().getByText("请先登录 B站，再在视频页重新收藏。")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "重新收藏" })).toBeEnabled();
    expect(card().getByRole("button", { name: "仅查标题 / 备注" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
    expect(screen.queryByText("再试一次大概率还是同样的结果。")).not.toBeInTheDocument();
  });

  it("collects the video again from the page, which re-runs the same item with the browser's captions", async () => {
    const { env, item, user } = await loginPanel();
    env.worker.readPage.mockResolvedValue(bilibiliPage());
    item.item = libraryItem({ stage: "chunking", platform: "bilibili" });

    await user.click(card().getByRole("button", { name: "重新收藏" }));

    await waitFor(() => expect(env.worker.saveCapture).toHaveBeenCalledOnce());
    // The page is read again: the first read may have happened before the user logged in.
    expect(env.worker.readPage).toHaveBeenCalledTimes(2);
    expect(env.submitBatch).toHaveBeenCalledTimes(1);
    expect(env.retryItem).not.toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: "已经保存，不必等在这里" })).toBeInTheDocument();
  });

  it("leaves no live buttons on the earlier block once the video is collected again", async () => {
    const { env, item, user } = await loginPanel();
    env.worker.readPage.mockResolvedValue(bilibiliPage());
    item.item = libraryItem({ stage: "chunking", platform: "bilibili" });

    await user.click(card().getByRole("button", { name: "重新收藏" }));
    await screen.findByRole("heading", { name: "已经保存，不必等在这里" });

    expect(screen.getAllByRole("article", { name: "收藏视频" })).toHaveLength(2);
    // The earlier block still says what happened, but only the latest block acts.
    expect(card().getByText("请先登录 B站，再在视频页重新收藏。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新收藏" })).not.toBeInTheDocument();
    expect(card().getByRole("button", { name: "仅查标题 / 备注" })).toBeEnabled();
  });

  it("only searches titles when the user does not want to collect again", async () => {
    const { env, user } = await loginPanel();

    await user.click(card().getByRole("button", { name: "仅查标题 / 备注" }));

    expect(env.tabs.openUrl).toHaveBeenCalledWith(WORKSPACE_SEARCH);
    expect(env.worker.saveCapture).not.toHaveBeenCalled();
  });
});

describe("a ready body that is waiting for later stages", () => {
  const stages = (...later: Array<[string, "pending" | "running" | "done" | "failed"]>): LibraryItem["stages"] => [
    { key: "body", status: "done" },
    ...later.map(([key, status]) => ({ key, status })),
  ];
  const readyWith = (...later: Array<[string, "pending" | "running" | "done" | "failed"]>) =>
    libraryItem({ lifecycle: "ready", stage: "ready", body_searchable: true, stages: stages(...later) });

  it("says the body is searchable at once and keeps following until the later stages finish", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    await collectAndFollow(env);

    item.item = readyWith(["classify", "running"], ["summary", "pending"]);

    await waitFor(() => expect(row("分类与主题")).toHaveClass("is-running"));
    expect(screen.getByText("已整理，可检索")).toBeInTheDocument();
    expect(row("读取字幕与内容")).toHaveClass("is-done");
    expect(row("内容要点")).toHaveClass("is-pending");
    expect(screen.queryByText("已收藏到资料库。")).not.toBeInTheDocument();
    expect(collectButton()).toBeDisabled();
    const calls = env.getItem.mock.calls.length;
    await sleep(40);
    expect(env.getItem.mock.calls.length).toBeGreaterThan(calls);

    item.item = readyWith(["classify", "done"], ["summary", "running"]);
    await waitFor(() => expect(row("内容要点")).toHaveClass("is-running"));
    expect(env.collectStore.entries.get(KEY_A)?.stages.map((stage) => `${stage.key}:${stage.status}`)).toEqual(["body:done", "classify:done", "summary:running"]);

    item.item = readyWith(["classify", "done"], ["summary", "done"]);
    expect(await screen.findByText("已收藏到资料库。")).toBeInTheDocument();
    expect(collectButton()).toBeEnabled();
    expect(env.collectStore.entries.has(KEY_A)).toBe(false);
    const settled = env.getItem.mock.calls.length;
    await sleep(40);
    expect(env.getItem.mock.calls.length).toBe(settled);
  });

  it("ends when a later stage fails, because the item is still usable", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    await collectAndFollow(env);

    item.item = readyWith(["classify", "failed"], ["summary", "failed"]);

    expect(await screen.findByText("已收藏到资料库。")).toBeInTheDocument();
    expect(row("分类与主题 · 未分类")).toHaveClass("is-failed");
    expect(row("内容要点 · 暂无内容要点")).toHaveClass("is-failed");
    expect(screen.getByText("已整理，可检索")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "收藏还在，整理没有完成" })).not.toBeInTheDocument();
  });

  it("ignores a skipped stage and a stage this build does not know", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    await collectAndFollow(env);

    item.item = libraryItem({
      lifecycle: "ready", stage: "ready", body_searchable: true,
      stages: [{ key: "body", status: "done" }, { key: "movie", status: "skipped" }, { key: "translation", status: "running" }],
    });

    expect(await screen.findByText("已收藏到资料库。")).toBeInTheDocument();
    expect(rows().getAllByRole("listitem")).toHaveLength(2);
  });
});

describe("a server this panel is newer or older than", () => {
  it("derives the body stage from the lifecycle when the server sends no stages", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "fetching" }));
    await collectAndFollow(env);
    await waitFor(() => expect(row("读取字幕与内容")).toHaveClass("is-running"));

    item.item = libraryItem({ stage: "chunking" });
    await waitFor(() => expect(row("建立检索索引")).toHaveClass("is-running"));
    item.item = libraryItem({ lifecycle: "ready", stage: "ready" });
    expect(await screen.findByText("已整理，可检索")).toBeInTheDocument();
  });

  it("explains an unfinished item that has no reason with a generic one", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    await collectAndFollow(env);

    item.item = libraryItem({ lifecycle: "failed", stage: "failed", error_code: "ingestion_failed", available_actions: ["retry"] });

    expect(await screen.findByText("整理时出错了，正文暂不可检索。")).toBeInTheDocument();
    expect(card().getByRole("button", { name: "重新整理" })).toBeInTheDocument();
  });

  it("keeps showing progress, without crashing, for a lifecycle and a stage it does not know", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    await collectAndFollow(env);

    // A newer server may add values; the types are closed, the wire is not.
    const future: LibraryItem = JSON.parse(JSON.stringify(libraryItem({ stage: "chunking" })).replace('"processing"', '"teleporting"').replace('"chunking"', '"warp"'));
    expect(future.lifecycle).toBe("teleporting");
    item.item = future;

    await sleep(60);

    expect(screen.getByRole("heading", { name: "已经保存，不必等在这里" })).toBeInTheDocument();
    expect(row("读取字幕与内容")).toHaveClass("is-running");
    expect(collectButton()).toBeDisabled();
  });

  it("keeps a reason category it does not know as a plain error", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    await collectAndFollow(env);

    const future: IngestReason = JSON.parse('{"code":"asr_no_speech","category":"mystery","retry_useful":false}');
    item.item = failedItem(future);

    expect(await screen.findByText("整理时出错了，正文暂不可检索。")).toBeInTheDocument();
  });
});

describe("what a caption-less video is told", () => {
  it("says the video will be transcribed when the server transcribes its platform", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage("youtube") }, transcriptionPlatforms: ["youtube", "bilibili"] });
    feed(env, libraryItem({ stage: "fetching" }));
    const user = await renderPanel(env);
    await screen.findByText(/收藏后会自动语音转写/);

    await user.click(collectButton());

    await waitFor(() => expect(env.submitBatch).toHaveBeenCalledOnce());
    expect(await screen.findByText("页面没有字幕，收藏后会自动语音转写")).toBeInTheDocument();
    expect(screen.queryByText("视频没有字幕，改用链接导入")).not.toBeInTheDocument();
  });

  it("keeps the original line when the server does not transcribe", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage("youtube") }, transcriptionPlatforms: [] });
    feed(env, libraryItem({ stage: "fetching" }));
    const user = await renderPanel(env);
    await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答");

    await user.click(collectButton());

    expect(await screen.findByText("视频没有字幕，改用链接导入")).toBeInTheDocument();
    expect(screen.queryByText(/自动语音转写/)).not.toBeInTheDocument();
  });

  it("does not promise transcription on a platform the server does not transcribe", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage("youtube") }, transcriptionPlatforms: ["bilibili"] });
    feed(env, libraryItem({ stage: "fetching" }));
    const user = await renderPanel(env);
    await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答");

    await user.click(collectButton());

    expect(await screen.findByText("视频没有字幕，改用链接导入")).toBeInTheDocument();
  });
});

describe("resuming the ingest status after the panel is reopened", () => {
  it("draws the stages it stored before the first read comes back, then follows the item", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    env.collectStore.entries.set(KEY_A, toStored({
      ...newCollectEntry("old-entry", KEY_A, 1_000_000 - 60_000),
      phase: "tracking",
      path: "url",
      fallback: "no_captions",
      itemPublicId: "item-7",
      stage: "ready",
      stageSince: 1_000_000 - 30_000,
      bodySearchable: true,
      itemTitle: "Video aaaaaaaaaaa",
      itemPlatform: "youtube",
      stages: [{ key: "body", status: "done" }, { key: "classify", status: "running" }],
    }));
    item.item = libraryItem({
      lifecycle: "ready", stage: "ready", public_id: "item-7", body_searchable: true,
      stages: [{ key: "body", status: "done" }, { key: "classify", status: "running" }],
    });

    await renderPanel(env);

    await waitFor(() => expect(row("分类与主题")).toHaveClass("is-running"));
    expect(row("读取字幕与内容")).toHaveClass("is-done");
    expect(screen.getByText("已整理，可检索")).toBeInTheDocument();
    expect(collectButton()).toBeDisabled();

    item.item = libraryItem({ lifecycle: "ready", stage: "ready", public_id: "item-7", stages: [{ key: "body", status: "done" }, { key: "classify", status: "done" }] });
    expect(await screen.findByText("已收藏到资料库。")).toBeInTheDocument();
  });

  it("shows a retried run again after a reload", async () => {
    const env = makeEnvironment();
    const item = feed(env, libraryItem({ stage: "chunking" }));
    const user = await collectAndFollow(env);
    item.item = failedItem(NO_SPEECH);
    await screen.findByRole("heading", { name: "收藏还在，整理没有完成" });
    env.retryItem.mockResolvedValueOnce(libraryItem({ lifecycle: "queued", stage: "queued" }));
    item.item = libraryItem({ lifecycle: "queued", stage: "queued" });

    await user.click(card().getByRole("button", { name: "重新整理" }));
    await screen.findByRole("heading", { name: "已经保存，不必等在这里" });

    expect(env.collectStore.entries.get(KEY_A)).toMatchObject({ phase: "tracking", itemPublicId: "item-1", reason: null });
  });
});

describe("a failed save that never reached the server", () => {
  it("keeps the step list for a rejected capture and offers no ingest buttons", async () => {
    const env = makeEnvironment();
    env.worker.saveCapture.mockRejectedValueOnce(new WorkerError("quota_exceeded"));
    const user = await renderPanel(env);
    await screen.findByText(CAPTION_LINE);

    await user.click(collectButton());

    expect(await screen.findByText("未能收藏：收藏数量已达上限")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "整理步骤" })).not.toBeInTheDocument();
  });

  it("does not offer 「仅查标题 / 备注」 for a video that was never saved", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_URL }, pages: { [BILI_URL]: bilibiliPage(1, { caption: { status: "unavailable", source: null, language: null, cues: [] } }) } });
    env.submitBatch.mockResolvedValueOnce({ results: [{ input_index: 0, result_id: "r", status: "unsupported_url", item_public_id: null, lifecycle: null }] });
    const user = await renderPanel(env);
    await screen.findByText("没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。");

    await user.click(collectButton());

    expect(await screen.findByText("未能收藏：暂不支持直接导入这个视频")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "仅查标题 / 备注" })).not.toBeInTheDocument();
  });
});
