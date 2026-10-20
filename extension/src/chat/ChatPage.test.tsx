import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Capabilities, ConversationHistoryPage, ConversationResponse, ConversationTurns } from "../api/contracts";
import { ApiError, StreamingUnavailableError } from "../api/client";
import type { ConversationStreamEvent } from "../api/contracts";
import { ChatPage } from "./ChatPage";

const history: ConversationHistoryPage = {
  items: [{
    thread_id: "thread-1",
    conversation_id: "conversation-1",
    title: "用户访谈应该怎样做？",
    preview: "先明确你要验证的假设。",
    updated_at: "2026-08-10T10:00:00Z",
  }],
  next_cursor: null,
};

const turns: ConversationTurns = {
  thread_id: "thread-1",
  conversation_id: "conversation-1",
  turns: [{
    user_text: "用户访谈应该怎样做？",
    assistant_text: "先明确你要验证的假设。\n\n来源：\n- 访谈方法\n  - [S42] https://example.test/video — 从开放问题开始。",
    status: "ok",
    error_code: null,
    citations: [{
      title: "访谈方法",
      excerpt: "从开放问题开始。",
      url: "https://example.test/video",
      start_sec: 65,
    }],
    action_results: [],
    created_at: "2026-08-10T10:00:00Z",
  }],
};

const multipleTurns: ConversationTurns = {
  ...turns,
  turns: [
    ...turns.turns!,
    { user_text: "最后一个问题", assistant_text: "最后一个回答", status: "not_found", error_code: null, citations: [], action_results: [], created_at: "2026-10-15T08:00:00Z" },
  ],
};

const capabilities: Capabilities = {
  supported_platforms: ["youtube"],
  browser_companion: true,
  web_login_channels: ["email"],
  external_login_providers: [],
  save_enabled: false,
  max_save_batch_size: 10,
  bilibili_parts_enabled: false,
  bilibili_favorites_enabled: false,
  transcript_pagination: true,
  archive: false,
  summary_generation: false,
  chat: true,
};

function renderPage(props: Partial<Parameters<typeof ChatPage>[0]> = {}, providedClient?: QueryClient) {
  const client = providedClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ChatPage
          loadCapabilities={vi.fn().mockResolvedValue(capabilities)}
          fetchHistory={vi.fn().mockResolvedValue(history)}
          fetchTurns={vi.fn().mockResolvedValue(turns)}
          reset={vi.fn().mockResolvedValue({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null })}
          send={vi.fn().mockResolvedValue({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null })}
          createId={() => "new-id"}
          {...props}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("AI search chat page", () => {
  const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, writable: true, value: vi.fn() });
  });
  afterEach(() => {
    if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScrollIntoView);
    else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
    vi.unstubAllGlobals();
  });

  it("renders freshly preloaded history and transcript without another blocking request", async () => {
    const client = new QueryClient();
    client.setQueryData(["capabilities"], capabilities);
    client.setQueryData(["conversations"], history);
    client.setQueryData(["conversation", "thread-1"], turns);
    const loadCapabilities = vi.fn();
    const fetchHistory = vi.fn();
    const fetchTurns = vi.fn();
    renderPage({ loadCapabilities, fetchHistory, fetchTurns }, client);

    expect(await screen.findByRole("link", { name: /访谈方法/ })).toBeInTheDocument();
    expect(screen.queryByText("正在加载历史对话…")).not.toBeInTheDocument();
    expect(screen.queryByText("正在准备检索工作区…")).not.toBeInTheDocument();
    expect(loadCapabilities).not.toHaveBeenCalled();
    expect(fetchHistory).not.toHaveBeenCalled();
    expect(fetchTurns).not.toHaveBeenCalled();
  });

  it("bootstraps an empty history into an enabled composer without showing a history error", async () => {
    const user = userEvent.setup();
    const emptyHistory: ConversationHistoryPage = { items: [], next_cursor: null };
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    const send = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue(emptyHistory),
      fetchTurns: vi.fn().mockResolvedValue({ thread_id: "thread-new", conversation_id: "new-id", turns: [] }),
      reset,
      send,
    });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(reset).toHaveBeenCalledWith("new-id"));
    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.queryByText("历史对话加载失败。")).not.toBeInTheDocument();

    await user.click(input);
    await user.type(input, "空历史时也能提问吗？");
    expect(input).toHaveValue("空历史时也能提问吗？");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));
    expect(send).toHaveBeenCalledWith("new-id", {
      message_id: "new-id",
      text: "空历史时也能提问吗？",
    });
  });

  it("shows the history error only when the history request actually fails", async () => {
    const view = renderPage({ fetchHistory: vi.fn().mockRejectedValue(new Error("network unavailable")) });

    expect(await screen.findByText("历史对话加载失败。", {}, { timeout: 2_500 })).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    await waitFor(() => expect(view.container.querySelector(".chat-conversation")).toHaveAttribute("aria-busy", "false"));
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
    expect(within(screen.getByRole("region", { name: "历史检索列表" })).getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("offers keyboard access to separate history and conversation regions with a reachable composer", async () => {
    const user = userEvent.setup();
    const view = renderPage({ fetchTurns: vi.fn().mockResolvedValue(multipleTurns) });
    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    const historyRegion = screen.getByRole("region", { name: "历史检索列表" });
    const transcriptRegion = screen.getByRole("region", { name: "对话内容滚动区域" });
    const composer = screen.getByRole("form", { name: "向资料库提问" });
    expect(screen.getByRole("link", { name: "← 返回资料库" })).toHaveAttribute("href", "/library");
    expect(historyRegion).toHaveAttribute("tabindex", "0");
    expect(transcriptRegion).toHaveAttribute("tabindex", "0");
    expect(historyRegion).not.toContainElement(screen.getByRole("button", { name: "新建检索" }));
    expect(transcriptRegion).not.toContainElement(composer);
    expect(composer).toContainElement(input);
    expect(within(composer).getByRole("button", { name: /发送问题/ })).toBeInTheDocument();
    historyRegion.focus();
    await user.tab();
    expect(screen.getByRole("button", { name: /^用户访谈应该怎样做？/ })).toHaveFocus();
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    scrollIntoView.mockClear();
    await user.keyboard("{Enter}");
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(view.container.querySelector(".chat-turn:last-child .chat-message--user"));
    transcriptRegion.focus();
    expect(transcriptRegion).toHaveFocus();
  });

  it.each(["cached", "async"])("opens %s history at its last question and retains every earlier turn", async (source) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let releaseTurns: (value: ConversationTurns) => void = () => undefined;
    const fetchTurns = vi.fn().mockImplementation(() => new Promise<ConversationTurns>((resolve) => { releaseTurns = resolve; }));
    if (source === "cached") {
      client.setQueryData(["capabilities"], capabilities);
      client.setQueryData(["conversations"], history);
      client.setQueryData(["conversation", "thread-1"], multipleTurns);
    }
    const view = renderPage({ fetchTurns }, client);
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    if (source === "async") {
      await waitFor(() => expect(fetchTurns).toHaveBeenCalledOnce());
      expect(scrollIntoView).not.toHaveBeenCalled();
      act(() => releaseTurns(multipleTurns));
    }

    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledOnce());
    const questions = view.container.querySelectorAll(".chat-question-text");
    expect([...questions].map((question) => question.textContent)).toEqual(multipleTurns.turns!.map((turn) => turn.user_text));
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(questions[1].closest("article"));
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "instant", block: "start", inline: "nearest" });
    expect(questions[1].closest("li")).toHaveClass("chat-turn--current");
  });

  it("reopens A → B → A and the selected row without pulling back on background refetch", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const bTurns = { ...multipleTurns, thread_id: "thread-2", conversation_id: "conversation-2", turns: [{ ...multipleTurns.turns![1], user_text: "B 的最后一个问题" }] };
    const fetchTurns = vi.fn().mockImplementation((threadId: string) => Promise.resolve(threadId === "thread-1" ? multipleTurns : bTurns));
    const view = renderPage({
      fetchTurns,
      fetchHistory: vi.fn().mockResolvedValue({ ...history, items: [...history.items!, { ...history.items![0], thread_id: "thread-2", conversation_id: "conversation-2", title: "对话 B" }] }),
    }, client);
    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    const transcript = view.container.querySelector<HTMLElement>(".chat-transcript")!;
    for (const title of [/^对话 B/, /^用户访谈应该怎样做？/, /^用户访谈应该怎样做？/]) {
      scrollIntoView.mockClear();
      await user.click(screen.getByRole("button", { name: title }));
      await waitFor(() => expect(input).toBeEnabled());
      expect(scrollIntoView).toHaveBeenCalledOnce();
      expect(scrollIntoView.mock.contexts.at(-1)).toBe(transcript.querySelector(".chat-turn:last-child .chat-message--user"));
    }
    scrollIntoView.mockClear();
    const refreshedTurns = { ...multipleTurns, turns: [...multipleTurns.turns!, { ...multipleTurns.turns![1], user_text: "后台新增的问题", created_at: "2026-10-15T09:00:00Z" }] };
    fetchTurns.mockResolvedValueOnce(refreshedTurns);
    await act(async () => { await client.refetchQueries({ queryKey: ["conversation", "thread-1"], exact: true }); });
    expect(scrollIntoView).not.toHaveBeenCalled();
    transcript.scrollTop = 100;
    fireEvent.scroll(transcript);
    fetchTurns.mockResolvedValueOnce(refreshedTurns);
    await act(async () => { await client.refetchQueries({ queryKey: ["conversation", "thread-1"], exact: true }); });
    await waitFor(() => expect([...transcript.querySelectorAll(".chat-question-text")].map((question) => question.textContent)).toEqual(refreshedTurns.turns!.map((turn) => turn.user_text)));
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it.each(["empty", "error"])("does not position an %s transcript", async (status) => {
    const fetchTurns = status === "empty"
      ? vi.fn().mockResolvedValue({ ...turns, turns: [] })
      : vi.fn().mockRejectedValue(new Error("unavailable"));
    renderPage({ fetchTurns });
    if (status === "empty") expect(await screen.findByText("你想从这些视频里找回什么？")).toBeInTheDocument();
    else expect(await screen.findByText("无法加载这段对话。", {}, { timeout: 2_500 })).toBeInTheDocument();
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it("does not reopen old history while a new conversation is being created", async () => {
    const user = userEvent.setup();
    renderPage({ reset: vi.fn().mockImplementation(() => new Promise<ConversationResponse>(() => undefined)) });
    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    scrollIntoView.mockClear();
    await user.click(screen.getByRole("button", { name: "新建检索" }));
    expect(input).toBeDisabled();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it.each(["success", "failure"])("preserves selected B when pending send A ends in %s", async (outcome) => {
    const user = userEvent.setup();
    let finish: (value: ConversationResponse) => void = () => undefined;
    let fail: (reason: Error) => void = () => undefined;
    const send = vi.fn().mockImplementation(() => new Promise<ConversationResponse>((resolve, reject) => { finish = resolve; fail = reject; }));
    const completedA = { ...turns, turns: [...turns.turns!, { ...turns.turns![0], user_text: "A 的新问题", assistant_text: "A 的新回答", created_at: "2026-10-15T08:00:00Z" }] };
    const bTurns = { ...turns, thread_id: "thread-2", conversation_id: "conversation-2", turns: [{ ...turns.turns![0], user_text: "B 的最后一个问题" }] };
    const fetchTurns = vi.fn().mockImplementation((threadId: string) => Promise.resolve(threadId === "thread-2" ? bTurns : send.mock.calls.length ? completedA : turns));
    const view = renderPage({
      send, fetchTurns,
      fetchHistory: vi.fn().mockResolvedValue({ ...history, items: [...history.items!, { ...history.items![0], thread_id: "thread-2", conversation_id: "conversation-2", title: "对话 B" }] }),
    });
    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "A 的新问题{Enter}");
    await user.click(screen.getByRole("button", { name: /^对话 B/ }));
    await screen.findByText("B 的最后一个问题");
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    const bQuestion = view.container.querySelector(".chat-turn--current .chat-message--user");
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(bQuestion);
    expect(within(screen.getByRole("list", { name: "对话内容" })).queryByText("A 的新问题")).not.toBeInTheDocument();
    const minHeight = bQuestion!.parentElement!.style.minHeight;
    scrollIntoView.mockClear();
    if (outcome === "success") finish({ status: "ok", text: "A 的新回答", thread_id: "thread-1", citations: turns.turns![0].citations, action_results: [], error_code: null });
    else fail(new Error("send failed"));
    await waitFor(() => expect(input).toBeEnabled());
    expect(view.container.querySelector(".chat-turn--current .chat-message--user")).toBe(bQuestion);
    expect(bQuestion!.parentElement).toHaveStyle({ minHeight });
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("selects the latest persisted conversation and renders its safe citations", async () => {
    const fetchTurns = vi.fn().mockResolvedValue(turns);
    renderPage({ fetchTurns });

    await waitFor(() => expect(fetchTurns).toHaveBeenCalledWith("thread-1"));
    await waitFor(() => expect(screen.getAllByText("先明确你要验证的假设。")).toHaveLength(2));
    expect(screen.queryByText(/来源：/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\[S42\]/)).not.toBeInTheDocument();
    // No marker in the text: the citation stays reachable as an "另见" chip.
    const chip = screen.getByRole("link", { name: "引用 1：《访谈方法》 1:05" });
    expect(chip).toHaveAttribute("href", "https://example.test/video");
    expect(chip.closest(".chat-cite-more")).not.toBeNull();
    expect(screen.getByRole("tooltip")).toHaveTextContent("从开放问题开始。");
    expect(screen.queryByText("展开字幕依据")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "回答来源" })).not.toBeInTheDocument();
  });

  it("renders a compact plain-text sidebar preview without Markdown markers", async () => {
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue({
        items: [{
          ...history.items![0],
          preview: "## 演讲大纲\n\n- **开场**：介绍 `樟宜机场`。 [S42] [^3]",
        }],
        next_cursor: null,
      }),
    });

    expect(await screen.findByText("演讲大纲 开场：介绍 樟宜机场。")).toBeInTheDocument();
    expect(screen.queryByText(/##|\*\*|`|\[S42\]|\[\^3\]/)).not.toBeInTheDocument();
  });

  it("sends a question through the selected durable conversation and prevents duplicate submit", async () => {
    const user = userEvent.setup();
    let resolveSend: (value: ConversationResponse) => void;
    const send = vi.fn<
      (conversationId: string, input: { message_id: string; text: string }) => Promise<ConversationResponse>
    >().mockImplementation(() => new Promise((resolve) => { resolveSend = resolve; }));
    renderPage({ send });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "怎样做有效访谈？");
    const submit = screen.getByRole("button", { name: /发送问题/ });
    await user.click(submit);

    expect(send).toHaveBeenCalledWith("conversation-1", {
      message_id: "new-id",
      text: "怎样做有效访谈？",
    });
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(send).toHaveBeenCalledOnce();
    resolveSend!({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("sends on Enter and blocks empty or busy keyboard and form submissions", async () => {
    const user = userEvent.setup();
    let resolveSend: (value: ConversationResponse) => void;
    const send = vi.fn().mockImplementation(() => new Promise<ConversationResponse>((resolve) => { resolveSend = resolve; }));
    renderPage({ send });
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    scrollIntoView.mockClear();
    expect(input).toHaveAccessibleDescription("Enter 发送，Shift+Enter 换行 · 回答附原文依据");
    await user.type(input, "   {Enter}");
    expect(send).not.toHaveBeenCalled();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(input).toHaveValue("   ");

    await user.type(input, "怎样做有效访谈？{Enter}");
    expect(send).toHaveBeenCalledWith("conversation-1", {
      message_id: "new-id",
      text: "怎样做有效访谈？",
    });
    expect(input).toBeDisabled();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "instant", block: "start", inline: "nearest" });
    const scrollCount = scrollIntoView.mock.calls.length;
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.submit(input.closest("form")!);
    expect(send).toHaveBeenCalledOnce();
    expect(scrollIntoView).toHaveBeenCalledTimes(scrollCount);
    resolveSend!({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("inserts a newline on Shift+Enter before sending the multiline question", async () => {
    const user = userEvent.setup();
    const send = vi.fn().mockResolvedValue({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    renderPage({ send });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    vi.mocked(HTMLElement.prototype.scrollIntoView).mockClear();
    await user.type(input, "访谈如何开场？{Shift>}{Enter}{/Shift}如何追问？");
    expect(input).toHaveValue("访谈如何开场？\n如何追问？");
    expect(send).not.toHaveBeenCalled();
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(send).toHaveBeenCalledWith("conversation-1", {
      message_id: "new-id",
      text: "访谈如何开场？\n如何追问？",
    });
  });

  it.each([
    { isComposing: true },
    { keyCode: 229 },
  ])("keeps IME confirmation Enter from sending (%j)", async (compositionEvent) => {
    const user = userEvent.setup();
    const send = vi.fn().mockResolvedValue({ status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    renderPage({ send });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    vi.mocked(HTMLElement.prototype.scrollIntoView).mockClear();
    await user.type(input, "用户访谈");
    fireEvent.keyDown(input, { key: "Enter", ...compositionEvent });
    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("用户访谈");
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(send).toHaveBeenCalledOnce();
  });

  it("ignores repeated Enter keydowns", async () => {
    const user = userEvent.setup();
    const send = vi.fn();
    renderPage({ send });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    vi.mocked(HTMLElement.prototype.scrollIntoView).mockClear();
    await user.type(input, "用户访谈");
    fireEvent.keyDown(input, { key: "Enter", repeat: true });
    expect(send).not.toHaveBeenCalled();
    expect(input).toHaveValue("用户访谈");
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it.each(["transcript", "new conversation"])("blocks direct form submission while loading the %s", async (loading) => {
    const send = vi.fn();
    const fetchTurns = vi.fn().mockImplementation(() => new Promise<ConversationTurns>(() => undefined));
    const reset = vi.fn().mockImplementation(() => new Promise<ConversationResponse>(() => undefined));
    renderPage({
      send,
      fetchTurns,
      reset,
      fetchHistory: vi.fn().mockResolvedValue(loading === "transcript" ? history : { items: [], next_cursor: null }),
    });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(loading === "transcript" ? fetchTurns : reset).toHaveBeenCalledOnce());
    expect(input).toBeDisabled();
    fireEvent.change(input, { target: { value: "资料库有哪些访谈方法？" } });
    fireEvent.submit(input.closest("form")!);
    expect(send).not.toHaveBeenCalled();
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it.each([
    { width: 1280, transcriptHeight: 420, expectedHeight: 380 },
    { width: 390, transcriptHeight: 240, expectedHeight: 200 },
  ])("uses the bounded transcript for question spacing at $width px", async ({ width, transcriptHeight, expectedHeight }) => {
    const user = userEvent.setup();
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    vi.stubGlobal("innerWidth", width);
    vi.stubGlobal("innerHeight", 800);
    const view = renderPage({
      fetchHistory: vi.fn().mockResolvedValue({
        ...history,
        items: [...history.items!, { ...history.items![0], thread_id: "thread-2", conversation_id: "conversation-2", title: "更早的问题" }],
      }),
      send: vi.fn().mockImplementation(() => new Promise<ConversationResponse>(() => undefined)),
    });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    const transcript = view.container.querySelector<HTMLElement>(".chat-transcript")!;
    Object.defineProperty(transcript, "clientHeight", { configurable: true, value: transcriptHeight });
    transcript.style.paddingTop = "20px";
    transcript.style.paddingBottom = "20px";
    expect(scrollIntoView).toHaveBeenCalledOnce();
    scrollIntoView.mockClear();
    await user.click(screen.getByRole("button", { name: /^更早的问题/ }));
    await waitFor(() => expect(input).toBeEnabled());
    expect(scrollIntoView).toHaveBeenCalledOnce();
    scrollIntoView.mockClear();

    scrollIntoView.mockImplementation(function (this: HTMLElement) {
      expect(this).toHaveClass("chat-message--user");
      expect(this).toHaveTextContent("最新的提问");
      expect(this.parentElement).toHaveClass("chat-turn--current");
      expect(this.parentElement).toHaveStyle({ minHeight: `${expectedHeight}px` });
    });
    await user.type(input, "最新的提问{Enter}");
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "instant", block: "start", inline: "nearest" });
  });

  it("keeps a repeated question at the top while its answer streams and hands off to history", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    let emit: (event: ConversationStreamEvent) => void = () => undefined;
    let finish: (response: ConversationResponse) => void = () => undefined;
    let releaseTranscript: (value: ConversationTurns) => void = () => undefined;
    const response: ConversationResponse = { status: "ok", text: "最终回答", citations: [], action_results: [], thread_id: "thread-1", error_code: null };
    const fetchTurns = vi.fn().mockResolvedValueOnce(turns).mockImplementation(() => new Promise<ConversationTurns>((resolve) => { releaseTranscript = resolve; }));
    const sendStream = vi.fn((_conversationId: string, _input: { message_id: string; text: string }, onEvent: (event: ConversationStreamEvent) => void) => {
      emit = onEvent;
      return new Promise<ConversationResponse>((resolve) => { finish = resolve; });
    });
    const view = renderPage({ fetchTurns, sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    const question = turns.turns![0].user_text;
    await user.type(input, `${question}{Enter}`);
    expect(scrollIntoView).toHaveBeenCalled();
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(view.container.querySelector(".chat-turn--current .chat-message--user"));
    const minHeight = view.container.querySelector<HTMLElement>(".chat-turn--current")!.style.minHeight;
    scrollIntoView.mockClear();
    await user.click(screen.getByRole("button", { name: /^用户访谈应该怎样做？/ }));
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(view.container.querySelector(".chat-turn--current .chat-message--user"));
    scrollIntoView.mockClear();

    act(() => emit({ type: "text_delta", request_id: "stream", message_id: "new-id", sequence: 1, text: "正在生成的回答" }));
    expect(screen.getByText("正在生成的回答")).toBeInTheDocument();
    expect(scrollIntoView).not.toHaveBeenCalled();
    act(() => emit({ type: "completed", request_id: "stream", message_id: "new-id", sequence: 2, response }));
    expect(screen.getByText("最终回答")).toBeInTheDocument();
    expect(scrollIntoView).not.toHaveBeenCalled();
    finish(response);
    await waitFor(() => expect(fetchTurns).toHaveBeenCalledTimes(2));
    scrollIntoView.mockClear();

    act(() => releaseTranscript({
      ...turns,
      turns: [...turns.turns!, { user_text: question, assistant_text: "最终回答", citations: [], action_results: [], status: "ok", error_code: null, created_at: "2026-10-15T08:00:00Z" }],
    }));
    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.getAllByText("最终回答")).toHaveLength(1);
    expect(scrollIntoView).toHaveBeenCalledOnce();
    const durableTurn = view.container.querySelectorAll(".chat-turn")[1];
    expect(scrollIntoView.mock.contexts.at(-1)).toBe(durableTurn.querySelector(".chat-message--user"));
    expect(durableTurn).toHaveStyle({ minHeight });
  });

  it.each(["transcript", "page"])("leaves manual scrolling free during the final handoff in the %s", async (surface) => {
    const user = userEvent.setup();
    const scrollIntoView = vi.mocked(HTMLElement.prototype.scrollIntoView);
    let finish: (response: ConversationResponse) => void = () => undefined;
    const send = vi.fn().mockImplementation(() => new Promise<ConversationResponse>((resolve) => { finish = resolve; }));
    const fetchTurns = vi.fn().mockResolvedValueOnce(turns).mockResolvedValue({
      ...turns,
      turns: [...turns.turns!, { user_text: "最新的提问", assistant_text: "最终回答", citations: [], action_results: [], status: "ok", error_code: null, created_at: "2026-10-15T08:00:00Z" }],
    });
    const view = renderPage({ send, fetchTurns });
    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    const transcript = view.container.querySelector<HTMLElement>(".chat-transcript")!;
    scrollIntoView.mockImplementation(() => {
      transcript.scrollTop = 500;
      vi.stubGlobal("scrollY", 500);
    });
    await user.type(input, "最新的提问{Enter}");
    expect(scrollIntoView).toHaveBeenCalled();
    scrollIntoView.mockClear();

    if (surface === "transcript") {
      transcript.scrollTop = 100;
      fireEvent.scroll(transcript);
    } else {
      vi.stubGlobal("scrollY", 100);
      fireEvent.scroll(window);
    }
    finish({ status: "ok", text: "最终回答", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    await waitFor(() => expect(input).toBeEnabled());
    expect(screen.getByText("最终回答")).toBeInTheDocument();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("describes a server timeout without claiming the network failed", async () => {
    const user = userEvent.setup();
    renderPage({
      send: vi.fn().mockRejectedValue(new ApiError(504, "request_failed", "请求无法完成")),
    });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "拟定四分钟演讲大纲");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("回答生成超时，请重新发送。");
    expect(screen.queryByText(/检查网络/)).not.toBeInTheDocument();
  });

  it("creates a fresh server conversation from the sidebar control", async () => {
    const user = userEvent.setup();
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({ reset });

    await user.click(await screen.findByRole("button", { name: "新建检索" }));
    expect(reset).toHaveBeenCalledWith("new-id");
  });

  it("keeps the terminal answer visible until the transcript refetch converges", async () => {
    const user = userEvent.setup();
    let fetchTurnsCall = 0;
    let releaseTranscript: (value: ConversationTurns) => void = () => undefined;
    const persistedTurns: ConversationTurns = {
      thread_id: "thread-1",
      conversation_id: "conversation-1",
      turns: [
        ...turns.turns!,
        {
          user_text: "流式问题",
          assistant_text: "最终安全答案",
          status: "ok",
          error_code: null,
          citations: [],
          action_results: [],
          created_at: "2026-08-10T10:01:00Z",
        },
      ],
    };
    const fetchTurns = vi.fn().mockImplementation(() => {
      fetchTurnsCall += 1;
      if (fetchTurnsCall === 1) return Promise.resolve(turns);
      return new Promise<ConversationTurns>((resolve) => { releaseTranscript = resolve; });
    });
    const sendStream = vi.fn(async (
      _conversationId: string,
      _input: { message_id: string; text: string },
      onEvent: (event: ConversationStreamEvent) => void,
    ) => {
      onEvent({
        type: "completed",
        request_id: "request-handoff",
        message_id: "new-id",
        sequence: 1,
        activity: "completed",
        response: {
          status: "ok",
          text: "最终安全答案",
          citations: [],
          action_results: [],
          thread_id: "thread-1",
          error_code: null,
        },
      });
      return {
        status: "ok",
        text: "最终安全答案",
        citations: [],
        action_results: [],
        thread_id: "thread-1",
        error_code: null,
      };
    });
    renderPage({ fetchTurns, sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "流式问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(await screen.findByText("最终安全答案")).toBeInTheDocument();
    await waitFor(() => expect(fetchTurnsCall).toBe(2));
    expect(screen.getAllByText("最终安全答案")).toHaveLength(1);

    releaseTranscript(persistedTurns);
    await waitFor(() => expect(screen.getAllByText("最终安全答案")).toHaveLength(1));
    expect(screen.queryByText("回答已生成，但对话记录同步较慢")).not.toBeInTheDocument();
  });

  it("does not mistake an older identical turn for the pending durable turn", async () => {
    const user = userEvent.setup();
    const repeatedTurns: ConversationTurns = {
      thread_id: "thread-1",
      conversation_id: "conversation-1",
      turns: [{
        user_text: "重复问题",
        assistant_text: "重复答案",
        status: "ok",
        error_code: null,
        citations: [],
        action_results: [],
        created_at: "2026-08-10T10:00:00Z",
      }],
    };
    const convergedTurns: ConversationTurns = {
      ...repeatedTurns,
      turns: [
        ...repeatedTurns.turns!,
        {
          user_text: "重复问题",
          assistant_text: "重复答案\n\n来源：\n- 新来源",
          status: "ok",
          error_code: null,
          citations: [],
          action_results: [],
          created_at: "2026-08-10T10:01:00Z",
        },
      ],
    };
    const prefixCollisionTurns: ConversationTurns = {
      ...repeatedTurns,
      turns: [
        ...repeatedTurns.turns!,
        {
          user_text: "重复问题",
          assistant_text: "重复答案但不是同一份终态",
          status: "ok",
          error_code: null,
          citations: [],
          action_results: [],
          created_at: "2026-08-10T10:00:30Z",
        },
      ],
    };
    const fetchTurns = vi.fn()
      .mockResolvedValueOnce(repeatedTurns)
      .mockResolvedValueOnce(prefixCollisionTurns)
      .mockResolvedValueOnce(convergedTurns);
    const sendStream = vi.fn(async (
      _conversationId: string,
      _input: { message_id: string; text: string },
      onEvent: (event: ConversationStreamEvent) => void,
    ) => {
      const response: ConversationResponse = {
        status: "ok",
        text: "重复答案",
        citations: [],
        action_results: [],
        thread_id: "thread-1",
        error_code: null,
      };
      onEvent({
        type: "completed",
        request_id: "request-repeat",
        message_id: "new-id",
        sequence: 1,
        activity: "completed",
        response,
      });
      return response;
    });
    renderPage({ fetchTurns, sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "重复问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(await screen.findByText(/回答已生成，但对话记录同步较慢/)).toBeInTheDocument();
    expect(screen.getAllByText("重复答案")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "重试同步" }));

    await waitFor(() => expect(screen.queryByText(/回答已生成，但对话记录同步较慢/)).not.toBeInTheDocument());
    expect(screen.getAllByText("重复答案")).toHaveLength(2);
  });

  it("enables the composer after reset without waiting for sidebar convergence", async () => {
    const user = userEvent.setup();
    let historyCall = 0;
    let releaseHistory: (value: ConversationHistoryPage) => void = () => undefined;
    const fetchHistory = vi.fn().mockImplementation(() => {
      historyCall += 1;
      if (historyCall === 1) return Promise.resolve(history);
      return new Promise<ConversationHistoryPage>((resolve) => { releaseHistory = resolve; });
    });
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({ fetchHistory, reset });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "新建检索" }));
    await waitFor(() => expect(reset).toHaveBeenCalledWith("new-id"));
    await waitFor(() => expect(input).toBeEnabled());
    await waitFor(() => expect(historyCall).toBe(2));

    releaseHistory(history);
  });

  it("fills the composer from a concrete example question", async () => {
    const user = userEvent.setup();
    renderPage({
      fetchTurns: vi.fn().mockResolvedValue({
        thread_id: "thread-1",
        conversation_id: "conversation-1",
        turns: [],
      }),
    });

    const suggestion = await screen.findByRole("button", {
      name: /这个观点在哪个视频的什么位置/,
    });
    await user.click(suggestion);

    expect(screen.getByRole("textbox", { name: "向资料库提问" })).toHaveValue(
      "这个观点在哪个视频的什么位置？",
    );
  });

  it("does not load conversations when this deployment has chat disabled", async () => {
    const fetchHistory = vi.fn().mockResolvedValue(history);
    renderPage({
      loadCapabilities: vi.fn().mockResolvedValue({ ...capabilities, chat: false }),
      fetchHistory,
    });

    expect(await screen.findByRole("heading", { name: "尚未开放 AI 智能检索" })).toBeInTheDocument();
    expect(fetchHistory).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "新建检索" })).toBeDisabled();
  });

  it("renders controlled Agent activity and text deltas while an SSE turn is pending", async () => {
    const user = userEvent.setup();
    let resolveStream: (value: ConversationResponse) => void = () => undefined;
    let releaseSecondDelta: () => void = () => undefined;
    let releaseCompletion: () => void = () => undefined;
    const sendStream = vi.fn(
      async (
        _conversationId: string,
        _input: { message_id: string; text: string },
        onEvent: (event: ConversationStreamEvent) => void,
      ) => {
        onEvent({
          type: "started",
          request_id: "request-1",
          message_id: "new-id",
          sequence: 1,
          activity: "preparing",
          text: null,
          response: null,
          error_code: null,
          message: null,
        });
        onEvent({
          type: "activity",
          request_id: "request-1",
          message_id: "new-id",
          sequence: 2,
          activity: "retrieving",
          text: null,
          response: null,
          error_code: null,
          message: null,
        });
        onEvent({
          type: "text_delta",
          request_id: "request-1",
          message_id: "new-id",
          sequence: 3,
          activity: null,
          text: "流式片段",
          response: null,
          error_code: null,
          message: null,
        });
        await new Promise<void>((resolve) => { releaseSecondDelta = resolve; });
        onEvent({
          type: "text_delta",
          request_id: "request-1",
          message_id: "new-id",
          sequence: 4,
          activity: null,
          text: "第二段",
          response: null,
          error_code: null,
          message: null,
        });
        await new Promise<void>((resolve) => { releaseCompletion = resolve; });
        onEvent({
          type: "completed",
          request_id: "request-1",
          message_id: "new-id",
          sequence: 5,
          activity: "completed",
          text: null,
          response: {
            status: "ok",
            text: "最终安全答案",
            citations: [],
            action_results: [],
            thread_id: "thread-1",
            error_code: null,
          },
          error_code: null,
          message: "回答已完成",
        });
        return new Promise<ConversationResponse>((resolve) => { resolveStream = resolve; });
      },
    );
    renderPage({ sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "流式问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(await screen.findByText("正在整理答案…")).toBeInTheDocument();
    expect(screen.getByText("流式片段")).toBeInTheDocument();
    releaseSecondDelta();
    expect(await screen.findByText("流式片段第二段")).toBeInTheDocument();
    releaseCompletion();
    expect(await screen.findByText("最终安全答案")).toBeInTheDocument();
    expect(sendStream).toHaveBeenCalledOnce();
    resolveStream({ status: "ok", text: "流式片段", citations: [], action_results: [], thread_id: "thread-1", error_code: null });
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("renders real execution steps and Todo progress before folding under the answer", async () => {
    const user = userEvent.setup();
    let releaseAnswer: () => void = () => undefined;
    let releaseCompletion: () => void = () => undefined;
    const final: ConversationResponse = {
      status: "ok",
      text: "最终安全答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    const sendStream = vi.fn(async (
      _conversationId: string,
      _input: { message_id: string; text: string },
      onEvent: (event: ConversationStreamEvent) => void,
    ) => {
      onEvent({
        type: "started", request_id: "request-steps", message_id: "new-id", sequence: 1,
        activity: "preparing",
      });
      onEvent({
        type: "step_started", request_id: "request-steps", message_id: "new-id", sequence: 2,
        step_id: "step-1", step_code: "searching_library",
      });
      onEvent({
        type: "step_completed", request_id: "request-steps", message_id: "new-id", sequence: 3,
        step_id: "step-1", step_code: "searching_library", step_outcome: "completed", result_count: 5,
      });
      onEvent({
        type: "plan_updated", request_id: "request-steps", message_id: "new-id", sequence: 4,
        plan: [
          { id: "find", title: "检索相关视频", status: "completed" },
          { id: "answer", title: "整理回答", status: "in_progress" },
        ],
      });
      await new Promise<void>((resolve) => { releaseAnswer = resolve; });
      onEvent({
        type: "text_delta", request_id: "request-steps", message_id: "new-id", sequence: 5,
        text: "正文开始",
      });
      await new Promise<void>((resolve) => { releaseCompletion = resolve; });
      onEvent({
        type: "completed", request_id: "request-steps", message_id: "new-id", sequence: 6,
        activity: "completed", response: final,
      });
      return final;
    });
    renderPage({ sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "展示执行过程");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(await screen.findAllByText("找到 5 个相关片段")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "本轮计划" })).toBeInTheDocument();
    expect(screen.getByText("检索相关视频")).toBeInTheDocument();
    expect(screen.getByText("整理回答")).toBeInTheDocument();
    const timeline = screen.getByText(/执行过程 · 1 个步骤/).closest("details");
    expect(timeline).toHaveAttribute("open");

    releaseAnswer();
    expect(await screen.findByText("正文开始")).toBeInTheDocument();
    await waitFor(() => expect(timeline).not.toHaveAttribute("open"));
    releaseCompletion();
  });

  it("flushes same-turn SSE activity and text before the terminal event", async () => {
    const user = userEvent.setup();
    let activityWasPaintable = false;
    let deltaWasPaintable = false;
    const sendStream = vi.fn(
      async (
        _conversationId: string,
        _input: { message_id: string; text: string },
        onEvent: (event: ConversationStreamEvent) => void,
      ) => {
        onEvent({
          type: "started",
          request_id: "request-same-turn",
          message_id: "new-id",
          sequence: 1,
          activity: "preparing",
          text: null,
          response: null,
          error_code: null,
          message: null,
        });
        onEvent({
          type: "activity",
          request_id: "request-same-turn",
          message_id: "new-id",
          sequence: 2,
          activity: "retrieving",
          text: null,
          response: null,
          error_code: null,
          message: null,
        });
        activityWasPaintable = document.body.textContent?.includes("正在检索资料库…") ?? false;
        onEvent({
          type: "text_delta",
          request_id: "request-same-turn",
          message_id: "new-id",
          sequence: 3,
          activity: null,
          text: "第一段",
          response: null,
          error_code: null,
          message: null,
        });
        deltaWasPaintable = document.body.textContent?.includes("第一段") ?? false;
        onEvent({
          type: "completed",
          request_id: "request-same-turn",
          message_id: "new-id",
          sequence: 4,
          activity: "completed",
          text: null,
          response: {
            status: "ok",
            text: "最终安全答案",
            citations: [],
            action_results: [],
            thread_id: "thread-1",
            error_code: null,
          },
          error_code: null,
          message: "回答已完成",
        });
        return {
          status: "ok",
          text: "最终安全答案",
          citations: [],
          action_results: [],
          thread_id: "thread-1",
          error_code: null,
        };
      },
    );
    renderPage({ sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "同一批事件的问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(activityWasPaintable).toBe(true);
    expect(deltaWasPaintable).toBe(true);
    expect(sendStream).toHaveBeenCalledOnce();
  });

  it("uses the JSON path once when the server explicitly disables streaming", async () => {
    const user = userEvent.setup();
    const sendStream = vi.fn().mockRejectedValue(new StreamingUnavailableError());
    const send = vi.fn().mockResolvedValue({
      status: "ok",
      text: "兼容模式答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    });
    renderPage({ sendStream, send });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "兼容模式问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    await waitFor(() => expect(send).toHaveBeenCalledOnce());
    expect(sendStream).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledWith("conversation-1", {
      message_id: "new-id",
      text: "兼容模式问题",
    });
  });

  it("renders citation-first sections as inline chips while streaming", async () => {
    const user = userEvent.setup();
    let release: () => void = () => undefined;
    const sendStream = vi.fn(async (
      _conversationId: string,
      _input: { message_id: string; text: string },
      onEvent: (event: ConversationStreamEvent) => void,
    ) => {
      onEvent({
        type: "started", request_id: "request-section", message_id: "new-id", sequence: 1,
        activity: "preparing", text: null, response: null, error_code: null, message: null,
      });
      onEvent({
        type: "section_started", request_id: "request-section", message_id: "new-id", sequence: 2,
        section_id: "section-1", status: "grounded", citation_ids: [11],
        citations: [{ title: "来源视频", excerpt: "字幕内容", url: "https://example.test/video", start_sec: 11 }],
        activity: null, text: null, response: null, error_code: null, message: null,
      });
      onEvent({
        type: "text_delta", request_id: "request-section", message_id: "new-id", sequence: 3,
        section_id: "section-1", text: "分段答案[^1]", activity: null, response: null,
        error_code: null, message: null,
      });
      await new Promise<void>((resolve) => { release = resolve; });
      onEvent({
        type: "section_completed", request_id: "request-section", message_id: "new-id", sequence: 4,
        section_id: "section-1", status: "grounded", activity: null, text: null, response: null,
        error_code: null, message: null,
      });
      onEvent({
        type: "completed", request_id: "request-section", message_id: "new-id", sequence: 5,
        activity: "completed", text: null,
        response: {
          status: "ok", text: "分段答案[^1]", citations: [{ title: "来源视频", excerpt: "字幕内容", url: "https://example.test/video", start_sec: 11 }],
          action_results: [], thread_id: "thread-1", error_code: null,
        }, error_code: null, message: "回答已完成",
      });
      return { status: "ok", text: "分段答案[^1]", citations: [], action_results: [], thread_id: "thread-1", error_code: null };
    });
    renderPage({ sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "分段问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));
    expect(await screen.findByText("分段答案")).toBeInTheDocument();
    const pendingSection = screen.getByRole("region", { name: "正在生成回答部分" });
    const chip = within(pendingSection).getByRole("link", { name: "引用 1：《来源视频》 0:11" });
    expect(chip).toHaveAttribute("href", "https://example.test/video");
    expect(within(pendingSection).getByRole("tooltip")).toHaveTextContent("字幕内容");
    expect(screen.queryByText("展开字幕依据")).not.toBeInTheDocument();
    release();
  });
});

describe("page Q&A threads in the workspace", () => {
  const pageItem = {
    thread_id: "thread-page",
    conversation_id: `page-${"b".repeat(32)}`,
    title: "刚才说的是什么？",
    preview: "他在讲数据库索引。",
    updated_at: "2026-10-15T10:00:00Z",
    page_qa: true,
  };
  const pageTurns: ConversationTurns = {
    thread_id: "thread-page",
    conversation_id: pageItem.conversation_id,
    page_qa: true,
    turns: [{
      user_text: "刚才说的是什么？",
      assistant_text: "他在讲数据库索引。[S12]\n\n来源：\n- 数据库课程\n  - [S12] https://www.youtube.com/watch?v=abcdefghijk&t=330s — 索引加速查询。",
      status: "ok",
      error_code: null,
      citations: [{
        title: "数据库课程",
        excerpt: "索引加速查询。",
        url: "https://www.youtube.com/watch?v=abcdefghijk&t=330s",
        start_sec: 330,
      }],
      action_results: [],
      created_at: "2026-10-15T10:00:00Z",
    }],
  };

  it("shows a selected page thread read-only with a way back to the video", async () => {
    const user = userEvent.setup();
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue({ items: [history.items![0], pageItem], next_cursor: null }),
      fetchTurns: vi.fn().mockImplementation(async (threadId: string) => (threadId === "thread-page" ? pageTurns : turns)),
    });

    await user.click(await screen.findByRole("button", { name: /^刚才说的是什么？/ }));

    expect(await screen.findByText("这是视频页问答记录，回到视频页可以继续提问。")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "向资料库提问" })).not.toBeInTheDocument();
    expect(screen.getByText("视频页回答")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开这个视频/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=abcdefghijk&t=330s",
    );
    expect(screen.getByRole("link", { name: /数据库课程/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=abcdefghijk&t=330s",
    );
  });

  it("badges page threads in the history list and opens on the newest library conversation", async () => {
    const fetchTurns = vi.fn().mockResolvedValue(turns);
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue({ items: [pageItem, history.items![0]], next_cursor: null }),
      fetchTurns,
    });

    await waitFor(() => expect(fetchTurns).toHaveBeenCalledWith("thread-1"));
    expect(fetchTurns).not.toHaveBeenCalledWith("thread-page");
    expect(screen.getByText("视频页")).toBeInTheDocument();
    expect(await screen.findByRole("textbox", { name: "向资料库提问" })).toBeInTheDocument();
    expect(screen.queryByText("这是视频页问答记录，回到视频页可以继续提问。")).not.toBeInTheDocument();
  });

  it("starts a library conversation when the history holds only page threads", async () => {
    const reset = vi.fn().mockResolvedValue({
      status: "ok", text: "", citations: [], action_results: [], thread_id: "thread-new", error_code: null,
    });
    renderPage({
      fetchHistory: vi.fn().mockResolvedValue({ items: [pageItem], next_cursor: null }),
      fetchTurns: vi.fn().mockResolvedValue({ thread_id: "thread-new", conversation_id: "new-id", turns: [] }),
      reset,
    });

    await waitFor(() => expect(reset).toHaveBeenCalledWith("new-id"));
    expect(await screen.findByRole("textbox", { name: "向资料库提问" })).toBeEnabled();
  });
});

describe("inline citation chips in the workspace chat", () => {
  const citedCitations = [
    { title: "访谈方法", excerpt: "从开放问题开始。", url: "https://example.test/video", start_sec: 65 },
    { title: "调研计划", excerpt: "先写下假设。", url: "https://example.test/plan", start_sec: 3 },
    { title: "标题视频", excerpt: "标题与简介正文", url: "https://example.test/title", start_sec: null },
  ];
  const citedTurns: ConversationTurns = {
    thread_id: "thread-1",
    conversation_id: "conversation-1",
    turns: [{
      user_text: "怎样做访谈？",
      assistant_text: "先问开放问题[^1]，再写假设[^2][^1]。\n\n来源：\n- 访谈方法\n  - [^1] https://example.test/video — 从开放问题开始。",
      status: "ok",
      error_code: null,
      citations: citedCitations,
      action_results: [],
      created_at: "2026-08-10T10:00:00Z",
    }],
  };
  const emptyTurns: ConversationTurns = { thread_id: "thread-1", conversation_id: "conversation-1", turns: [] };
  const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  let styleElement: HTMLStyleElement | null = null;

  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, writable: true, value: vi.fn() });
    // jsdom does not load the app stylesheet; install the real one so the
    // collapse rule is exercised rather than mirrored in the test.
    styleElement = document.createElement("style");
    styleElement.textContent = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    document.head.appendChild(styleElement);
  });
  afterEach(() => {
    styleElement?.remove();
    if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScrollIntoView);
    else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
    vi.unstubAllGlobals();
  });

  it("places a chip at each marker, lists unmarked citations once, and drops the evidence list", async () => {
    renderPage({ fetchTurns: vi.fn().mockResolvedValue(citedTurns) });

    const first = await screen.findAllByRole("link", { name: "引用 1：《访谈方法》 1:05" });
    // Citation 1 is marked twice; the source block is stripped, not rendered.
    expect(first).toHaveLength(2);
    expect(first.every((link) => link.closest(".chat-cite-more") === null)).toBe(true);
    // A chip never splits across lines, including on a narrow phone viewport.
    const wrap = window.getComputedStyle(first[0]!.closest(".chat-cite-wrap") as HTMLElement);
    expect(wrap.whiteSpace).toBe("nowrap");
    expect(wrap.display).toBe("inline-block");
    const second = screen.getByRole("link", { name: "引用 2：《调研计划》 0:03" });
    expect(second).toHaveAttribute("href", "https://example.test/plan");
    // Citation 3 is never marked, so it appears only in the "另见" row.
    const more = document.querySelector(".chat-cite-more");
    expect(more).toHaveTextContent("另见");
    const alsoSee = within(more as HTMLElement).getAllByRole("link");
    expect(alsoSee).toHaveLength(1);
    expect(alsoSee[0]).toHaveAccessibleName("引用 3：《标题视频》 标题与简介");
    expect(alsoSee[0]).toHaveAttribute("href", "https://example.test/title");
    expect(screen.queryByText(/来源：/)).not.toBeInTheDocument();
    expect(screen.queryByText("展开字幕依据")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "回答来源" })).not.toBeInTheDocument();
    expect(screen.queryByText(/可跳转原视频，字幕默认折叠/)).not.toBeInTheDocument();
  });

  it("omits the 另见 row when every citation is marked", async () => {
    renderPage({
      fetchTurns: vi.fn().mockResolvedValue({
        ...citedTurns,
        turns: [{ ...citedTurns.turns![0]!, assistant_text: "甲[^1] 乙[^2] 丙[^3]", citations: citedCitations }],
      }),
    });

    expect(await screen.findByRole("link", { name: /引用 3：/ })).toBeInTheDocument();
    expect(document.querySelector(".chat-cite-more")).toBeNull();
  });

  it("collapses and restores the chips with the 引用 N toggle, keeping [n] in the text", async () => {
    const user = userEvent.setup();
    renderPage({ fetchTurns: vi.fn().mockResolvedValue(citedTurns) });

    const toggle = await screen.findByRole("button", { name: "引用 3" });
    const article = toggle.closest("article") as HTMLElement;
    const chip = within(article).getAllByRole("link", { name: "引用 2：《调研计划》 0:03" })[0]!;
    // Pressed means "citations are shown".
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveAttribute("title", "隐藏引用按钮");
    expect(article).not.toHaveAttribute("data-cites-hidden");
    expect(chip).toBeVisible();
    expect(chip).not.toHaveAttribute("aria-hidden");
    expect(chip).not.toHaveAttribute("tabindex");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAttribute("title", "显示引用按钮");
    expect(article).toHaveAttribute("data-cites-hidden", "true");
    // Visually collapsed to nothing, yet still in the text so a copy keeps "[n]".
    expect(window.getComputedStyle(chip).fontSize).toMatch(/^0(px)?$/);
    expect(chip.textContent).toBe("[2]");
    expect(document.body.textContent).toContain("[1]");
    expect(document.body.textContent).toContain("[2]");
    expect(article).toHaveTextContent("先问开放问题");
    // Out of the accessibility tree and the tab order.
    expect(chip).toHaveAttribute("aria-hidden", "true");
    expect(chip).toHaveAttribute("tabindex", "-1");
    expect(within(article).queryByRole("link")).toBeNull();
    const more = article.querySelector(".chat-cite-more") as HTMLElement;
    expect(more).toHaveAttribute("aria-hidden", "true");
    expect(more).toHaveTextContent("[3]");
    expect(window.getComputedStyle(more).fontSize).toMatch(/^0(px)?$/);
    expect(window.getComputedStyle(article.querySelector(".chat-cite__tip") as HTMLElement).display).toBe("none");
    for (let step = 0; step < 6; step += 1) {
      await user.tab();
      expect(document.activeElement?.classList.contains("chat-cite")).toBe(false);
    }

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(article).not.toHaveAttribute("data-cites-hidden");
    expect(chip).toBeVisible();
    expect(chip).not.toHaveAttribute("aria-hidden");
    expect(chip).not.toHaveAttribute("tabindex");
    expect(window.getComputedStyle(chip).fontSize).not.toMatch(/^0(px)?$/);
    await user.tab();
    expect(document.activeElement?.classList.contains("chat-cite")).toBe(true);
  });

  it("collapses each answer independently", async () => {
    const user = userEvent.setup();
    renderPage({
      fetchTurns: vi.fn().mockResolvedValue({
        ...citedTurns,
        turns: [
          citedTurns.turns![0]!,
          { ...citedTurns.turns![0]!, user_text: "第二个问题", created_at: "2026-08-10T11:00:00Z" },
        ],
      }),
    });

    const toggles = await screen.findAllByRole("button", { name: "引用 3" });
    expect(toggles).toHaveLength(2);
    await user.click(toggles[0]!);
    expect(toggles[0]!.closest("article")).toHaveAttribute("data-cites-hidden", "true");
    expect(toggles[1]!.closest("article")).not.toHaveAttribute("data-cites-hidden");
  });

  it("shows no toggle or chips for an answer without citations", async () => {
    renderPage({
      fetchTurns: vi.fn().mockResolvedValue({
        ...turns,
        turns: [{
          user_text: "没有答案的问题", assistant_text: "没有依据。[^1]", status: "not_found", error_code: null,
          citations: [], action_results: [], created_at: "2026-10-15T08:00:00Z",
        }],
      }),
    });

    expect(await screen.findByText("没有在当前资料库中找到足够依据。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^引用/ })).not.toBeInTheDocument();
    expect(document.querySelector(".chat-cite, .chat-cite-more")).toBeNull();
  });

  it("numbers streamed sections over their concatenated citations and keeps the numbers at completion", async () => {
    const user = userEvent.setup();
    let releaseSecond: () => void = () => undefined;
    let releaseComplete: () => void = () => undefined;
    const base = { request_id: "request-two", message_id: "new-id", activity: null, text: null, response: null, error_code: null, message: null };
    const first = [citedCitations[0]!, citedCitations[1]!];
    const second = [citedCitations[2]!];
    const sendStream = vi.fn(async (
      _conversationId: string,
      _input: { message_id: string; text: string },
      onEvent: (event: ConversationStreamEvent) => void,
    ) => {
      onEvent({ ...base, type: "section_started", sequence: 1, section_id: "section-1", status: "grounded", citation_ids: [1, 2], citations: first });
      onEvent({ ...base, type: "text_delta", sequence: 2, section_id: "section-1", text: "第一段[^1][^2]" });
      onEvent({ ...base, type: "section_completed", sequence: 3, section_id: "section-1", status: "grounded" });
      await new Promise<void>((resolve) => { releaseSecond = resolve; });
      onEvent({ ...base, type: "section_started", sequence: 4, section_id: "section-2", status: "grounded", citation_ids: [3], citations: second });
      onEvent({ ...base, type: "text_delta", sequence: 5, section_id: "section-2", text: "第二段[^3]" });
      await new Promise<void>((resolve) => { releaseComplete = resolve; });
      onEvent({ ...base, type: "section_completed", sequence: 6, section_id: "section-2", status: "grounded" });
      onEvent({
        ...base, type: "completed", sequence: 7, activity: "completed",
        response: {
          status: "ok", text: "第一段[^1][^2]\n\n第二段[^3]\n\n来源：\n- 标题视频", citations: citedCitations,
          action_results: [], thread_id: "thread-1", error_code: null,
        },
      });
      return new Promise<ConversationResponse>(() => undefined);
    });
    renderPage({ sendStream, fetchTurns: vi.fn().mockResolvedValue(emptyTurns) });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "两段问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    expect(await screen.findByRole("link", { name: "引用 2：《调研计划》 0:03" })).toHaveAttribute("href", "https://example.test/plan");
    expect(screen.getByRole("button", { name: "引用 2" })).toHaveAttribute("aria-pressed", "true");
    releaseSecond();
    // The third citation belongs to the second section, so the toggle counts all three.
    const third = await screen.findByRole("link", { name: "引用 3：《标题视频》 标题与简介" });
    expect(third).toHaveAttribute("href", "https://example.test/title");
    expect(third.closest("section")).toHaveAccessibleName("正在生成回答部分");
    expect(screen.getByRole("button", { name: "引用 3" })).toBeInTheDocument();
    releaseComplete();

    await waitFor(() => expect(screen.queryByRole("region", { name: "正在生成回答部分" })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "引用 1：《访谈方法》 1:05" })).toHaveAttribute("href", "https://example.test/video");
    expect(screen.getByRole("link", { name: "引用 3：《标题视频》 标题与简介" })).toHaveAttribute("href", "https://example.test/title");
    expect(screen.queryByText(/来源：/)).not.toBeInTheDocument();
    expect(document.querySelector(".chat-cite-more")).toBeNull();
  });

  it("keeps a collapsed streamed answer collapsed after it becomes durable history", async () => {
    const user = userEvent.setup();
    const answer = "最终答案[^1]";
    const durable: ConversationTurns = {
      thread_id: "thread-1",
      conversation_id: "conversation-1",
      turns: [{
        user_text: "流式问题", assistant_text: answer, status: "ok", error_code: null,
        citations: [citedCitations[0]!], action_results: [], created_at: "2026-08-10T10:01:00Z",
      }],
    };
    let fetchCount = 0;
    const fetchTurns = vi.fn().mockImplementation(() => {
      fetchCount += 1;
      return Promise.resolve(fetchCount === 1 ? emptyTurns : durable);
    });
    const response: ConversationResponse = {
      status: "ok", text: answer, citations: [citedCitations[0]!], action_results: [], thread_id: "thread-1", error_code: null,
    };
    let releaseSend: () => void = () => undefined;
    const sendStream = vi.fn(async (
      _conversationId: string,
      _input: { message_id: string; text: string },
      onEvent: (event: ConversationStreamEvent) => void,
    ) => {
      onEvent({
        type: "completed", request_id: "request-adopt", message_id: "new-id", sequence: 1, activity: "completed",
        text: null, response, error_code: null, message: null,
      });
      await new Promise<void>((resolve) => { releaseSend = resolve; });
      return response;
    });
    renderPage({ fetchTurns, sendStream });

    const input = await screen.findByRole("textbox", { name: "向资料库提问" });
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, "流式问题");
    await user.click(screen.getByRole("button", { name: /发送问题/ }));

    const pendingToggle = await screen.findByRole("button", { name: "引用 1" });
    await user.click(pendingToggle);
    expect(pendingToggle.closest("article")).toHaveAttribute("data-cites-hidden", "true");
    releaseSend();

    await waitFor(() => expect(fetchCount).toBe(2));
    await waitFor(() => expect(screen.queryByText("资料库助手")).not.toBeInTheDocument());
    const articles = Array.from(document.querySelectorAll("article.chat-message--assistant"));
    const last = articles[articles.length - 1] as HTMLElement;
    expect(within(last).getByRole("button", { name: "引用 1" })).toHaveAttribute("aria-pressed", "false");
    expect(last).toHaveAttribute("data-cites-hidden", "true");
  });
});
