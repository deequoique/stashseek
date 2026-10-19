import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ApiError, ConversationStreamError } from "../api/client";
import {
  ANSWER,
  BILI_P2_URL,
  BILI_URL,
  bilibiliPage,
  makeEnvironment,
  noCaptionBilibiliPage,
  noCaptionPage,
  YT_A,
  YT_B,
  youtubePage,
  type FakeEnvironment,
} from "../test/sidepanelEnvironment";
import { SidePanel } from "./SidePanel";
import { WorkerError } from "./workerApi";

async function renderReady(env: FakeEnvironment) {
  const user = userEvent.setup();
  render(<SidePanel deps={env.deps} />);
  await screen.findByRole("heading", { level: 1 });
  return user;
}

async function ask(user: ReturnType<typeof userEvent.setup>, question: string) {
  const box = await screen.findByRole("textbox", { name: "就当前视频提问" });
  await waitFor(() => expect(box).toBeEnabled());
  await user.type(box, question);
  await user.click(screen.getByRole("button", { name: "发送" }));
}

describe("side panel startup and login", () => {
  it("asks the worker for status first, then verifies the session and the Q&A capability", async () => {
    const env = makeEnvironment();
    await renderReady(env);

    expect(env.worker.status).toHaveBeenCalledOnce();
    expect(env.getSession).toHaveBeenCalledOnce();
    expect(env.getCapabilities).toHaveBeenCalledOnce();
    expect(env.worker.status.mock.invocationCallOrder[0]).toBeLessThan(env.getSession.mock.invocationCallOrder[0] as number);
  });

  it("shows the email login when the session is not valid and reaches the page view after the code", async () => {
    const env = makeEnvironment({ paired: false });
    env.getSession.mockRejectedValueOnce(new ApiError(401, "session_invalid", "登录已失效"));
    const user = userEvent.setup();
    render(<SidePanel deps={env.deps} />);

    await user.type(await screen.findByLabelText("邮箱地址"), "me@example.com");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    expect(env.worker.startLogin).toHaveBeenCalledWith("me@example.com");

    await user.type(await screen.findByLabelText("6 位验证码"), "123456");
    await user.click(screen.getByRole("button", { name: "确认登录" }));
    expect(env.worker.finishLogin).toHaveBeenCalledWith("me@example.com", "123456");

    expect(await screen.findByRole("heading", { name: "Video aaaaaaaaaaa" })).toBeInTheDocument();
    expect(env.getSession).toHaveBeenCalledTimes(2);
  });

  it("explains a rejected code and lets the user start over after an expired pairing", async () => {
    const env = makeEnvironment();
    env.getSession.mockRejectedValueOnce(new ApiError(401, "session_invalid", "登录已失效"));
    env.worker.finishLogin.mockRejectedValueOnce(new WorkerError("verification_failed"));
    const user = userEvent.setup();
    render(<SidePanel deps={env.deps} />);

    await user.type(await screen.findByLabelText("邮箱地址"), "me@example.com");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    await user.type(await screen.findByLabelText("6 位验证码"), "000000");
    await user.click(screen.getByRole("button", { name: "确认登录" }));
    expect(await screen.findByText("验证码无效或已过期，请重试。")).toBeInTheDocument();

    env.worker.finishLogin.mockRejectedValueOnce(new WorkerError("extension_pairing_expired"));
    await user.click(screen.getByRole("button", { name: "确认登录" }));
    expect(await screen.findByText("配对已过期，请重新连接。")).toBeInTheDocument();
    expect(screen.getByLabelText("邮箱地址")).toBeInTheDocument();
  });

  it("keeps a connection failure separate from login and offers a retry", async () => {
    const env = makeEnvironment();
    env.getSession.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    render(<SidePanel deps={env.deps} />);

    expect(await screen.findByText("无法连接搜藏助手，请确认服务正在运行。")).toBeInTheDocument();
    expect(screen.queryByLabelText("邮箱地址")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("heading", { name: "Video aaaaaaaaaaa" })).toBeInTheDocument();
  });

  it("logs out through the worker and returns to the login form", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);

    await user.click(screen.getByRole("button", { name: "退出登录" }));

    expect(env.worker.disconnect).toHaveBeenCalledOnce();
    expect(await screen.findByLabelText("邮箱地址")).toBeInTheDocument();
  });
});

describe("side panel page states", () => {
  it("tells the user to open a supported video on any other page and reads nothing", async () => {
    const env = makeEnvironment({ tab: { id: 3, url: "https://example.com/" } });
    await renderReady(env);

    expect(screen.getByRole("heading", { name: "打开 YouTube 或哔哩哔哩视频后提问" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "就当前视频提问" })).not.toBeInTheDocument();
    expect(env.worker.readPage).not.toHaveBeenCalled();
  });

  it("treats a tab whose URL Chrome withholds as unsupported", async () => {
    const env = makeEnvironment({ tab: { id: 3, url: null } });
    await renderReady(env);

    expect(screen.getByRole("heading", { name: "打开 YouTube 或哔哩哔哩视频后提问" })).toBeInTheDocument();
  });

  it("shows the title and caption status before any question, reading without submitting", async () => {
    const env = makeEnvironment();
    await renderReady(env);

    expect(await screen.findByRole("heading", { name: "Video aaaaaaaaaaa" })).toBeInTheDocument();
    expect(screen.getByText("字幕可用 · 官方字幕 · 2 条")).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledWith(7);
    expect(env.worker.saveCapture).not.toHaveBeenCalled();
    expect(env.streamQuestion).not.toHaveBeenCalled();
  });

  it("allows a metadata-only question on a caption-less YouTube video", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    const user = await renderReady(env);

    expect(await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答")).toBeInTheDocument();
    await ask(user, "它讲什么？");
    expect(env.streamQuestion).toHaveBeenCalledOnce();
  });

  it("points a caption-less video at 「收藏视频」 when the server transcribes its platform", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage() }, transcriptionPlatforms: ["youtube", "bilibili"] });
    const user = await renderReady(env);

    expect(await screen.findByText(
      "该视频没有字幕，提问将仅基于标题和简介回答。收藏后会自动语音转写，转写完成后可以按内容搜索。",
    )).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收藏视频" })).toBeEnabled();
    // The panel still answers from the browser's captions only, so it never promises to answer from the transcript.
    expect(screen.queryByText(/回到侧栏/)).not.toBeInTheDocument();
    expect(screen.queryByText(/转写完成后可以(提问|问)/)).not.toBeInTheDocument();
    // Questions on the title and description are still allowed.
    await ask(user, "它讲什么？");
    expect(env.streamQuestion).toHaveBeenCalledOnce();
  });

  it("points a caption-less Bilibili video at 「收藏视频」 as well, next to the login hint", async () => {
    const env = makeEnvironment({
      tab: { id: 7, url: BILI_URL },
      pages: { [BILI_URL]: noCaptionBilibiliPage() },
      transcriptionPlatforms: ["bilibili"],
    });
    await renderReady(env);

    expect(await screen.findByText(/收藏后会自动语音转写，转写完成后可以按内容搜索。/)).toBeInTheDocument();
    expect(screen.getByText("没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。")).toBeInTheDocument();
  });

  it("keeps the original line when the server does not transcribe, or not this platform", async () => {
    const off = makeEnvironment({ pages: { [YT_A]: noCaptionPage() }, transcriptionPlatforms: [] });
    const { unmount } = render(<SidePanel deps={off.deps} />);
    expect(await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答")).toBeInTheDocument();
    expect(screen.queryByText(/自动语音转写/)).not.toBeInTheDocument();
    unmount();

    const legacy = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    const second = render(<SidePanel deps={legacy.deps} />);
    expect(await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答")).toBeInTheDocument();
    second.unmount();

    const other = makeEnvironment({ pages: { [YT_A]: noCaptionPage() }, transcriptionPlatforms: ["bilibili"] });
    render(<SidePanel deps={other.deps} />);
    expect(await screen.findByText("该视频没有字幕，提问将仅基于标题和简介回答")).toBeInTheDocument();
    expect(screen.queryByText(/自动语音转写/)).not.toBeInTheDocument();
  });

  it("does not offer the transcription hint on a video that has captions", async () => {
    const env = makeEnvironment({ transcriptionPlatforms: ["youtube"] });
    await renderReady(env);

    expect(await screen.findByText("字幕可用 · 官方字幕 · 2 条")).toBeInTheDocument();
    expect(screen.queryByText(/自动语音转写/)).not.toBeInTheDocument();
  });

  it("offers a manual re-read when the page cannot be read", async () => {
    const env = makeEnvironment();
    env.worker.readPage.mockRejectedValueOnce(new WorkerError("caption_fetch_failed"));
    const user = await renderReady(env);

    expect(await screen.findByText("页面字幕暂时读取失败，请刷新视频页面后重试。")).toBeInTheDocument();
    // A failed read does not block collecting: the video is then imported by its URL.
    expect(screen.getByRole("button", { name: "收藏视频" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "重新读取" }));

    expect(await screen.findByText("字幕可用 · 官方字幕 · 2 条")).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(2);
  });

  it("re-reads a page that still describes the previous video, then accepts the right one", async () => {
    const env = makeEnvironment();
    env.worker.readPage.mockResolvedValueOnce(youtubePage("bbbbbbbbbbb"));
    await renderReady(env);

    expect(await screen.findByRole("heading", { name: "Video aaaaaaaaaaa" })).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(2);
  });

  it("re-reads a Bilibili page that still describes the previous video or another part", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_P2_URL }, pages: { [BILI_P2_URL]: bilibiliPage(2) } });
    env.worker.readPage
      .mockResolvedValueOnce(bilibiliPage(1, { platform_id: "BV1yy411c7mE" }))
      .mockResolvedValueOnce(bilibiliPage(1));
    await renderReady(env);

    expect(await screen.findByRole("heading", { name: "Bilibili BV1xx411c7mD" })).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(3);
  });

  it("allows a metadata-only question on a caption-less Bilibili video, and hints at AI captions", async () => {
    const env = makeEnvironment({ tab: { id: 7, url: BILI_URL }, pages: { [BILI_URL]: noCaptionBilibiliPage() } });
    const user = await renderReady(env);

    expect(await screen.findByText("没有读取到字幕：登录哔哩哔哩后可能会有 AI 字幕。")).toBeInTheDocument();
    await ask(user, "它讲什么？");
    expect(env.streamQuestion).toHaveBeenCalledOnce();
    expect(env.streamQuestion.mock.calls[0]?.[1].page.platform).toBe("bilibili");
  });

  it("retries a read while the YouTube player is not ready, then accepts the page", async () => {
    const env = makeEnvironment();
    env.worker.readPage.mockRejectedValueOnce(new WorkerError("caption_player_not_ready"));
    await renderReady(env);

    expect(await screen.findByText("字幕可用 · 官方字幕 · 2 条")).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(2);
  });

  it("asks the user to play the video when the player stays unready, and re-reads on demand", async () => {
    const env = makeEnvironment();
    for (let attempt = 0; attempt < 4; attempt += 1) {
      env.worker.readPage.mockRejectedValueOnce(new WorkerError("caption_player_not_ready"));
    }
    const user = await renderReady(env);

    expect(await screen.findByText("播放器还没准备好：请先播放视频几秒，再点「重新读取」。")).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(4);
    await user.click(screen.getByRole("button", { name: "重新读取" }));

    expect(await screen.findByText("字幕可用 · 官方字幕 · 2 条")).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(5);
  });

  it("hides the Q&A but keeps collecting when the server does not offer page Q&A", async () => {
    const env = makeEnvironment({ pageQa: false });
    await renderReady(env);

    expect(await screen.findByText("当前服务未开放视频问答，你仍然可以收藏视频。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "就当前视频提问" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收藏视频" })).toBeInTheDocument();
  });

  it("opens the workspace from the panel", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);

    await user.click(screen.getByRole("button", { name: "打开工作区" }));

    expect(env.worker.openWorkspace).toHaveBeenCalledOnce();
  });
});

describe("side panel Q&A", () => {
  it("sends the playback position and the page already read, then renders the answer and citation chips", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);

    await ask(user, "这个视频讲了什么？");

    await waitFor(() => expect(env.streamQuestion).toHaveBeenCalledOnce());
    expect(env.worker.playback).toHaveBeenCalledWith(7);
    const [conversationId, body, , signal] = env.streamQuestion.mock.calls[0] as Parameters<typeof env.streamQuestion>;
    expect(conversationId).toBe(`page-${"1".padStart(32, "0")}`);
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(body).toMatchObject({
      message_id: "message-1",
      question: "这个视频讲了什么？",
      playback_sec: 42,
      page: {
        platform: "youtube",
        platform_id: "aaaaaaaaaaa",
        canonical_url: YT_A,
        page_url: YT_A,
        metadata: { title: "Video aaaaaaaaaaa", description: "About the video" },
        caption: { status: "available", source: "official_cc", language: "en", cues: [{ start_sec: 1, end_sec: 4, text: "hello world" }, { start_sec: 4, end_sec: 9, text: "second cue" }] },
      },
    });
    expect(Object.keys(body).sort()).toEqual(["message_id", "page", "playback_sec", "question"]);

    const answer = await screen.findByText("这是答案");
    expect(answer).toBeInTheDocument();
    expect(screen.queryByText(/\[S3\]/)).not.toBeInTheDocument();
    expect(screen.queryByText(/来源：/)).not.toBeInTheDocument();
    const chip = screen.getByRole("button", { name: /5:30/ });
    expect(chip).toHaveTextContent("5:30Video aaaaaaaaaaa");
    expect(screen.getByText("摘录文字").closest("details")).not.toHaveAttribute("open");
  });

  it("sends a null position when the player cannot be read", async () => {
    const env = makeEnvironment();
    env.worker.playback.mockRejectedValueOnce(new WorkerError("unsupported_page"));
    const user = await renderReady(env);

    await ask(user, "问题");

    await waitFor(() => expect(env.streamQuestion).toHaveBeenCalledOnce());
    expect(env.streamQuestion.mock.calls[0]?.[1].playback_sec).toBeNull();
    expect(await screen.findByText("没有读到播放位置")).toBeInTheDocument();
  });

  it("shows the client-side steps above the answer and folds them away once the answer is done", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);

    await ask(user, "这个视频讲了什么？");
    await screen.findByRole("button", { name: /5:30/ });

    const language = new Intl.DisplayNames(["zh-CN"], { type: "language" }).of("en") as string;
    const steps = screen.getByRole("list", { name: "回答步骤" });
    expect(steps).toHaveTextContent(`读取视频字幕（2 段 · ${language}）`);
    expect(steps).toHaveTextContent("播放位置 0:42");
    expect(steps).toHaveTextContent("生成回答");
    expect(steps.querySelectorAll("li.is-completed")).toHaveLength(3);
    expect(steps.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("回答过程 · 3 个步骤")).toBeInTheDocument();
  });

  it("keeps 生成回答 running until the first text arrives, then marks it done while the answer streams", async () => {
    const env = makeEnvironment();
    let finish: (value: typeof ANSWER) => void = () => undefined;
    let deltas: ((text: string) => void) | undefined;
    env.streamQuestion.mockImplementationOnce((_id, _body, handlers) => new Promise((resolve) => {
      deltas = handlers?.onTextDelta;
      finish = resolve;
    }));
    const user = await renderReady(env);

    await ask(user, "慢一点");
    await screen.findByText("播放位置 0:42");
    const generating = () => within(screen.getByRole("list", { name: "回答步骤" })).getByText("生成回答").closest("li") as HTMLElement;
    expect(generating()).toHaveClass("is-running");

    act(() => deltas?.("开始回答"));
    await waitFor(() => expect(generating()).toHaveClass("is-completed"));
    expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();

    await act(async () => { finish(ANSWER); });
    await screen.findByRole("button", { name: /5:30/ });
  });

  it("marks 生成回答 failed when the stream fails", async () => {
    const env = makeEnvironment();
    env.streamQuestion.mockRejectedValueOnce(new ConversationStreamError("timeout", "超时"));
    const user = await renderReady(env);

    await ask(user, "会失败的问题");
    await screen.findByText("回答超时，请稍后重试。");
    expect(within(screen.getByRole("list", { name: "回答步骤" })).getByText("生成回答").closest("li")).toHaveClass("is-failed");
  });

  it("says so in the first step when a YouTube video has no captions", async () => {
    const env = makeEnvironment({ pages: { [YT_A]: noCaptionPage() } });
    const user = await renderReady(env);

    await ask(user, "它讲什么？");

    expect(await screen.findByText("该视频没有字幕，使用标题和简介")).toBeInTheDocument();
  });

  it("reuses the captions and the thread for follow-up questions", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);

    await ask(user, "第一个问题");
    await screen.findByRole("button", { name: /5:30/ });
    await ask(user, "第二个问题");
    await waitFor(() => expect(env.streamQuestion).toHaveBeenCalledTimes(2));

    expect(env.worker.readPage).toHaveBeenCalledOnce();
    const first = env.streamQuestion.mock.calls[0];
    const second = env.streamQuestion.mock.calls[1];
    expect(second?.[0]).toBe(first?.[0]);
    expect(second?.[1].message_id).not.toBe(first?.[1].message_id);
    expect(second?.[1].page.caption).toEqual(first?.[1].page.caption);
  });

  it("starts a new thread and re-reads after SPA navigation to another video", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);
    await ask(user, "旧视频的问题");
    await screen.findByRole("button", { name: /5:30/ });

    act(() => env.navigate({ id: 7, url: YT_B }));

    expect(await screen.findByRole("heading", { name: "Video bbbbbbbbbbb" })).toBeInTheDocument();
    expect(screen.queryByText("旧视频的问题")).not.toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledTimes(2);
    await ask(user, "新视频的问题");
    await waitFor(() => expect(env.streamQuestion).toHaveBeenCalledTimes(2));
    const [oldThread, newThread] = env.streamQuestion.mock.calls.map((call) => call[0]);
    expect(newThread).not.toBe(oldThread);
    expect(newThread).toMatch(/^page-[0-9a-f]{32}$/);
    expect(env.streamQuestion.mock.calls[1]?.[1].page.platform_id).toBe("bbbbbbbbbbb");
  });

  it("keeps the thread when the user leaves for another tab and comes back to the same video", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);
    await ask(user, "留下来的问题");
    await screen.findByRole("button", { name: /5:30/ });

    act(() => env.navigate({ id: 9, url: "https://example.com/" }));
    expect(await screen.findByRole("heading", { name: "打开 YouTube 或哔哩哔哩视频后提问" })).toBeInTheDocument();
    act(() => env.navigate({ id: 7, url: YT_A }));

    expect(await screen.findByText("留下来的问题")).toBeInTheDocument();
    expect(env.worker.readPage).toHaveBeenCalledOnce();
  });

  it("maps a rate limit to short copy and lets the user retry with a fresh message id", async () => {
    const env = makeEnvironment();
    env.streamQuestion.mockRejectedValueOnce(new ApiError(429, "rate_limited", "太频繁"));
    const user = await renderReady(env);

    await ask(user, "问得太快");
    expect(await screen.findByText(/提问太频繁了，请稍后再试。/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByRole("button", { name: /5:30/ });
    expect(env.streamQuestion).toHaveBeenCalledTimes(2);
    expect(env.streamQuestion.mock.calls[1]?.[1].message_id).not.toBe(env.streamQuestion.mock.calls[0]?.[1].message_id);
    expect(screen.getAllByText("问得太快")).toHaveLength(1);
  });

  it("shows a stream failure without the partial text that was never validated", async () => {
    const env = makeEnvironment();
    env.streamQuestion.mockImplementationOnce(async (_id, _body, handlers) => {
      handlers?.onTextDelta?.("没验证过的半截回答");
      throw new ConversationStreamError("answer_unavailable", "暂时无法生成可靠回答");
    });
    const user = await renderReady(env);

    await ask(user, "问题");

    expect(await screen.findByText(/暂时无法生成可靠回答，请换个问法或稍后重试。/)).toBeInTheDocument();
    expect(screen.queryByText("没验证过的半截回答")).not.toBeInTheDocument();
  });

  it("returns to the login form when the session is no longer valid", async () => {
    const env = makeEnvironment();
    env.streamQuestion.mockRejectedValueOnce(new ApiError(401, "session_invalid", "登录已失效"));
    const user = await renderReady(env);

    await ask(user, "问题");

    expect(await screen.findByLabelText("邮箱地址")).toBeInTheDocument();
  });

  it("stops a running answer", async () => {
    const env = makeEnvironment();
    env.streamQuestion.mockImplementationOnce((_id, _body, _handlers, signal) => new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new ConversationStreamError("cancelled", "请求已取消")));
    }));
    const user = await renderReady(env);

    await ask(user, "慢问题");
    await user.click(await screen.findByRole("button", { name: "停止" }));

    expect(await screen.findByText("已停止。")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "发送" })).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "回答步骤" })).getByText("生成回答").closest("li")).toHaveClass("is-skipped");
  });
});

describe("side panel citations", () => {
  it("seeks the player when the tab still shows the video", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);
    await ask(user, "问题");

    await user.click(await screen.findByRole("button", { name: /5:30/ }));

    await waitFor(() => expect(env.worker.seek).toHaveBeenCalledWith(7, 330));
    expect(env.tabs.openUrl).not.toHaveBeenCalled();
  });

  it("opens the link in a new tab when the active tab no longer shows that video", async () => {
    const env = makeEnvironment();
    const user = await renderReady(env);
    await ask(user, "问题");
    const chip = await screen.findByRole("button", { name: /5:30/ });

    env.setActiveSilently({ id: 7, url: YT_B });
    await user.click(chip);

    await waitFor(() => expect(env.tabs.openUrl).toHaveBeenCalledWith("https://www.youtube.com/watch?v=aaaaaaaaaaa&t=330s"));
    expect(env.worker.seek).not.toHaveBeenCalled();
  });

  it("falls back to a new tab, with a notice, when the player refuses the seek", async () => {
    const env = makeEnvironment();
    env.worker.seek.mockRejectedValueOnce(new WorkerError("seek_failed"));
    const user = await renderReady(env);
    await ask(user, "问题");

    await user.click(await screen.findByRole("button", { name: /5:30/ }));

    expect(await screen.findByText("没能在页面里跳转，已改为在新标签页打开。")).toBeInTheDocument();
    expect(env.tabs.openUrl).toHaveBeenCalledOnce();
  });

  it("never opens a non-https citation link", async () => {
    const env = makeEnvironment();
    env.streamQuestion.mockResolvedValueOnce({
      ...ANSWER,
      citations: [{ title: "x", excerpt: "y", url: "javascript:alert(1)", start_sec: null }],
    });
    const user = await renderReady(env);
    await ask(user, "问题");

    await user.click(await screen.findByRole("button", { name: "打开x" }));

    expect(env.tabs.openUrl).not.toHaveBeenCalled();
    expect(env.worker.seek).not.toHaveBeenCalled();
  });
});

describe("side panel privacy policy link", () => {
  it("shows the link on the login screen, pointing at the API origin and opening a new tab", async () => {
    const env = makeEnvironment({ paired: false });
    env.getSession.mockRejectedValueOnce(new ApiError(401, "session_invalid", "登录已失效"));
    render(<SidePanel deps={env.deps} />);

    const link = await screen.findByRole("link", { name: "隐私政策" });

    expect(link).toHaveAttribute("href", "https://api.test/privacy");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("shows the link in the footer of the signed-in panel", async () => {
    const env = makeEnvironment();
    await renderReady(env);

    const footerLink = within(screen.getByRole("contentinfo")).getByRole("link", { name: "隐私政策" });

    expect(footerLink).toHaveAttribute("href", "https://api.test/privacy");
    expect(footerLink).toHaveAttribute("target", "_blank");
  });

  it("omits the link instead of breaking the panel when no API origin can be resolved", async () => {
    const env = makeEnvironment();
    env.deps.privacyUrl = () => { throw new Error("extension_api_origin_invalid"); };
    await renderReady(env);

    expect(screen.queryByRole("link", { name: "隐私政策" })).toBeNull();
    expect(screen.getByRole("button", { name: "打开工作区" })).toBeInTheDocument();
  });
});
