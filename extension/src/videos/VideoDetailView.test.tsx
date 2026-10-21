import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LibraryItem, TranscriptPage } from "../api/contracts";
import { VideoDetailView } from "./VideoDetailView";

const item: LibraryItem = {
  public_id: "video-public",
  platform: "youtube",
  kind: "video",
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  title: "如何把信息变成理解",
  author: "Notebook Studio",
  published_at: "2026-08-01T00:00:00Z",
  duration_sec: 620,
  lang: "zh",
  description: "原视频作者提供的简介，不是 AI 摘要。",
  tags: [],
  chapters: [{ title: "重新定义记录", start_sec: 0 }, { title: "建立联系", start_sec: 185 }],
  cover_url: null,
  saved_at: "2026-08-07T10:00:00Z",
  why_saved: "整理知识管理方法",
  text_source: "youtube_captions",
  lifecycle: "ready",
  error_code: null,
  available_actions: ["archive", "edit_why_saved", "open_source"],
  latest_dispatch_public_id: "dispatch-public",
};

const transcript: TranscriptPage = {
  blocks: [
    { ordinal: 0, start_sec: 0, end_sec: 10, text: "记录不是终点，理解才是。", source_url: "https://youtu.be/x?t=0" },
    { ordinal: 1, start_sec: 10, end_sec: 22, text: "先把材料放进自己的问题里。", source_url: "https://youtu.be/x?t=10" },
  ],
  next_cursor: "next-page",
};

describe("video detail", () => {
  it("uses server-owned part source links for detail, chapters and transcript local time", () => {
    const source = "/api/v1/library/items/video-public/source?t=0";
    render(
      <VideoDetailView
        item={{ ...item, platform: "bilibili", url: source }}
        transcriptPages={[{ blocks: [{ ordinal: 0, start_sec: 12, end_sec: 15, text: "P2 字幕", source_url: source.replace("t=0", "t=12") }], next_cursor: null }]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.getByRole("link", { name: "在 B站 查看" })).toHaveAttribute("href", source);
    expect(screen.getByRole("link", { name: /建立联系/ })).toHaveAttribute("href", source.replace("t=0", "t=185"));
    expect(screen.getByRole("link", { name: "从 0:12 播放" })).toHaveAttribute("href", source.replace("t=0", "t=12"));
  });

  it("retains an honest legacy Bilibili P while changing only local chapter time", () => {
    render(
      <VideoDetailView
        item={{ ...item, platform: "bilibili", url: "https://www.bilibili.com/video/BV1xx411c7mD?p=2&tracking=x" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.getByRole("link", { name: /建立联系/ })).toHaveAttribute("href", "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=185");
  });

  it("keeps a long title complete while opting into compact hero typography", () => {
    const longTitle = "How to practice effectively...for just about anything";
    render(
      <VideoDetailView
        item={{ ...item, title: longTitle, cover_url: "https://i.ytimg.com/vi/example/hqdefault.jpg" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    const heading = screen.getByRole("heading", { name: longTitle });
    expect(heading).toHaveTextContent(longTitle);
    expect(heading).toHaveAttribute("data-title-density", "compact");
  });

  it("shows chapters and original transcript without pretending description is a summary", () => {
    const { container } = render(
      <VideoDetailView
        item={{ ...item, cover_url: "https://i.ytimg.com/vi/example/hqdefault.jpg" }}
        transcriptPages={[transcript]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "章节" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "字幕节选" })).toBeInTheDocument();
    expect(screen.getByText("根据原视频字幕整理")).toBeInTheDocument();
    expect(screen.getByText("点击时间跳到原视频对应位置")).toBeInTheDocument();
    expect(screen.queryByText(/搜索切片/)).not.toBeInTheDocument();
    expect(screen.getByText("记录不是终点，理解才是。")).toBeInTheDocument();
    expect(screen.getByText("原视频作者提供的简介，不是 AI 摘要。")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /摘要/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续加载字幕" })).toBeInTheDocument();
    expect(screen.getByText("中文")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("width", "960");
    expect(container.querySelector("img")).toHaveAttribute("height", "540");
    expect(container.querySelector("img")).toHaveAttribute("fetchpriority", "high");
  });

  it("makes overflowing chapters keyboard-scrollable", () => {
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("list", { name: "视频章节" })).toHaveAttribute("tabindex", "0");
  });

  it("keeps source-provided transcript timestamps inside a keyboard-scrollable reader", () => {
    const sentenceTranscript: TranscriptPage = {
      blocks: [
        {
          ordinal: 0,
          start_sec: 60,
          end_sec: 70,
          text: "先问最近一次真实经历。再追问当时采用了什么办法！",
          source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=60",
        },
      ],
      next_cursor: null,
    };
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[sentenceTranscript]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    const reader = screen.getByRole("region", { name: "字幕节选，可滚动查看" });
    expect(reader).toHaveAttribute("tabindex", "0");
    expect(within(reader).getAllByRole("listitem")).toHaveLength(1);
    expect(within(reader).getByText("先问最近一次真实经历。再追问当时采用了什么办法！")).toBeInTheDocument();
    expect(within(reader).getByRole("link", { name: "从 1:00 播放" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=60",
    );
    expect(screen.queryByRole("link", { name: "从 1:05 播放" })).not.toBeInTheDocument();
    expect(screen.getByText("节选 1 段")).toBeInTheDocument();
  });

  it("renders an existing non-empty backend summary without generating one", () => {
    render(
      <VideoDetailView
        item={{ ...item, summary: "这是此前已经存储的摘要。" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "摘要" })).toBeInTheDocument();
    expect(screen.getByText("这是此前已经存储的摘要。")).toBeInTheDocument();
  });

  it("exposes retry only when the backend lists it", () => {
    const { rerender } = render(
      <VideoDetailView
        item={{ ...item, lifecycle: "failed", available_actions: ["retry", "archive"] }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "重新整理" })).toBeInTheDocument();

    rerender(
      <VideoDetailView
        item={{ ...item, lifecycle: "processing", available_actions: ["archive"] }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
  });

  it("distinguishes transcript and action failures from genuinely empty content", () => {
    const retryTranscript = vi.fn();
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        transcriptError
        actionError
        onLoadMore={vi.fn()}
        onRetryTranscript={retryTranscript}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert", { name: "视频操作失败" })).toHaveTextContent(
      "操作未完成",
    );
    expect(screen.getByRole("alert", { name: "字幕加载失败" })).toHaveTextContent(
      "字幕暂时无法加载",
    );
    expect(screen.queryByText("这段视频没有可显示的字幕。")).not.toBeInTheDocument();
    screen.getByRole("button", { name: "重新加载字幕" }).click();
    expect(retryTranscript).toHaveBeenCalledOnce();
  });

  it("shows a loading state before deciding that a ready transcript is empty", () => {
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        transcriptInitialPending
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByText("正在加载字幕…")).toBeInTheDocument();
    expect(screen.queryByText("这段视频没有可显示的字幕。")).not.toBeInTheDocument();
  });

  it("closes why-saved editing only after the update succeeds", async () => {
    const user = userEvent.setup();
    const updateWhySaved = vi
      .fn()
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce(undefined);
    render(
      <VideoDetailView
        item={item}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={updateWhySaved}
      />,
    );

    await user.click(screen.getByRole("button", { name: "编辑说明和收藏夹" }));
    const input = screen.getByRole("textbox", { name: "保存说明" });
    await user.clear(input);
    await user.type(input, "新的保存原因");
    await user.click(screen.getByRole("button", { name: "保存说明和收藏夹" }));
    expect(await screen.findByRole("textbox", { name: "保存说明" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存说明和收藏夹" }));
    expect(screen.queryByRole("textbox", { name: "保存说明" })).not.toBeInTheDocument();
    expect(updateWhySaved).toHaveBeenCalledTimes(2);
  });

  it("shows collection tags separately and lets the saved context editor add a collection", async () => {
    const user = userEvent.setup();
    const updateWhySaved = vi.fn().mockResolvedValue(undefined);
    render(
      <VideoDetailView
        item={{ ...item, why_saved: "整理知识管理方法 #产品调研" }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={updateWhySaved}
      />,
    );

    expect(screen.getByLabelText("所属收藏夹")).toHaveTextContent("#产品调研");
    expect(screen.getByText("整理知识管理方法")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "编辑说明和收藏夹" }));
    const reason = screen.getByRole("textbox", { name: "保存说明" });
    expect(reason).toHaveValue("整理知识管理方法");
    await user.clear(reason);
    await user.type(reason, "新的保存说明");
    await user.type(screen.getByRole("textbox", { name: "新收藏夹" }), "AI_入门");
    await user.click(screen.getByRole("button", { name: "添加" }));
    expect(screen.getByRole("list", { name: "正在编辑的收藏夹" })).toHaveTextContent("#AI_入门");
    await user.click(screen.getByRole("button", { name: "保存说明和收藏夹" }));

    expect(updateWhySaved).toHaveBeenCalledWith("新的保存说明 #产品调研 #AI_入门");
  });

  describe("an item whose body is not searchable", () => {
    const NO_SPEECH = { code: "asr_no_speech", category: "no_speech", retry_useful: false } as const;
    const LOGIN_REQUIRED = { code: "bilibili_login_required", category: "login_required", retry_useful: false } as const;
    const failed: LibraryItem = {
      ...item,
      lifecycle: "failed",
      error_code: "asr_no_speech",
      reason: NO_SPEECH,
      stages: [{ key: "body", status: "failed", reason: NO_SPEECH }],
      body_searchable: false,
      available_actions: ["retry", "archive", "edit_why_saved", "open_source"],
    };

    function renderFailed(overrides: Partial<LibraryItem> = {}, props: Partial<Parameters<typeof VideoDetailView>[0]> = {}) {
      const handlers = { onRetry: vi.fn(), onSearchTitles: vi.fn() };
      render(
        <VideoDetailView
          item={{ ...failed, ...overrides }}
          transcriptPages={[]}
          onLoadMore={vi.fn()}
          onArchive={vi.fn()}
          onRestore={vi.fn()}
          onUpdateWhySaved={vi.fn()}
          {...handlers}
          {...props}
        />,
      );
      return handlers;
    }

    it("puts the unfinished screen at the top, with the real reason and a mark on the status", () => {
      renderFailed();

      const screenTop = screen.getByRole("region", { name: "收藏还在，整理没有完成" });
      expect(within(screenTop).getByText("如何把信息变成理解")).toBeInTheDocument();
      expect(within(screenTop).getByText("YouTube · 链接已保存")).toBeInTheDocument();
      expect(within(screenTop).getByRole("status")).toHaveTextContent("没有字幕，也没有识别出语音，正文暂不可检索。");
      expect(within(screenTop).getByText("再试一次大概率还是同样的结果。")).toBeInTheDocument();
      expect(screen.getByText("整理失败")).toBeInTheDocument();
      expect(screen.getByText("正文不可检索，可按标题 / 备注查找")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "在 YouTube 查看" })).toHaveAttribute("href", item.url);
      expect(screen.getByText("整理知识管理方法")).toBeInTheDocument();
    });

    it("keeps the page title first: the unfinished screen comes after the h1 and its heading is an h2", () => {
      renderFailed();

      const pageTitle = screen.getByRole("heading", { level: 1 });
      const cardHeading = screen.getByRole("heading", { level: 2, name: "收藏还在，整理没有完成" });
      expect(pageTitle).toHaveTextContent("如何把信息变成理解");
      expect(pageTitle.compareDocumentPosition(cardHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      // Still above the other sections of the page.
      const savedContext = screen.getByRole("heading", { level: 2, name: "为什么保存" });
      expect(cardHeading.compareDocumentPosition(savedContext) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("retries from the screen's main button, the one button of that name", async () => {
      const user = userEvent.setup();
      const handlers = renderFailed();

      await user.click(screen.getByRole("button", { name: "重新整理" }));

      expect(handlers.onRetry).toHaveBeenCalledOnce();
    });

    it("searches titles and notes from 「仅查标题 / 备注」, and leaves the button out when it cannot", async () => {
      const user = userEvent.setup();
      const handlers = renderFailed();
      await user.click(screen.getByRole("button", { name: "仅查标题 / 备注" }));
      expect(handlers.onSearchTitles).toHaveBeenCalledOnce();
    });

    it("has no search button without a way to search", () => {
      renderFailed({}, { onSearchTitles: undefined });
      expect(screen.queryByRole("button", { name: "仅查标题 / 备注" })).not.toBeInTheDocument();
    });

    it("disables the main button while an action is in flight", () => {
      renderFailed({}, { actionPending: true });
      expect(screen.getByRole("button", { name: "重新整理" })).toBeDisabled();
    });

    it("sends a login-required Bilibili item back to the video page instead of offering a retry", () => {
      renderFailed({
        lifecycle: "needs_action",
        platform: "bilibili",
        url: "https://www.bilibili.com/video/BV1xx411c7mD",
        error_code: null,
        reason: LOGIN_REQUIRED,
        stages: [{ key: "body", status: "failed", reason: LOGIN_REQUIRED }],
        available_actions: ["archive", "edit_why_saved", "open_source"],
      });

      const screenTop = screen.getByRole("region", { name: "收藏还在，整理没有完成" });
      expect(within(screenTop).getByText("B站 · 链接已保存")).toBeInTheDocument();
      expect(within(screenTop).getByRole("status")).toHaveTextContent("这个视频的字幕需要登录 B站 才能读取，正文暂不可检索。");
      expect(within(screenTop).getByText("请先登录 B站，再在视频页重新收藏。")).toBeInTheDocument();
      expect(within(screenTop).getByRole("link", { name: "打开视频页" })).toHaveAttribute("href", "https://www.bilibili.com/video/BV1xx411c7mD");
      expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
      expect(screen.queryByText("再试一次大概率还是同样的结果。")).not.toBeInTheDocument();
    });

    it("shows an item that needs action the same way, with a retry", async () => {
      const user = userEvent.setup();
      const handlers = renderFailed({
        lifecycle: "needs_action",
        error_code: null,
        reason: { code: "asr_disabled", category: "no_transcription", retry_useful: true },
        stages: [{ key: "body", status: "failed", reason: { code: "asr_disabled", category: "no_transcription", retry_useful: true } }],
      });

      expect(screen.getByRole("status")).toHaveTextContent("没有字幕，暂时无法语音转写，正文暂不可检索。");
      await user.click(screen.getByRole("button", { name: "重新整理" }));
      expect(handlers.onRetry).toHaveBeenCalledOnce();
    });

    it("offers no retry when the server lists none", () => {
      renderFailed({ available_actions: ["archive", "edit_why_saved", "open_source"] });

      expect(screen.getByRole("region", { name: "收藏还在，整理没有完成" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
    });

    it("explains a server that sends no reason with a generic one", () => {
      const legacy: LibraryItem = { ...failed, error_code: "ingestion_failed" };
      delete legacy.reason;
      delete legacy.stages;
      delete legacy.body_searchable;
      render(
        <VideoDetailView item={legacy} transcriptPages={[]} onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
      );

      expect(screen.getByRole("status")).toHaveTextContent("整理时出错了，正文暂不可检索。");
      expect(screen.getByText("正文不可检索，可按标题 / 备注查找")).toBeInTheDocument();
    });

    it("tells a refusal for lack of quota from any other failure, inside the unfinished screen", () => {
      const { unmount } = render(
        <VideoDetailView item={failed} transcriptPages={[]} actionError actionQuotaExceeded onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
      );
      expect(screen.getByRole("alert")).toHaveTextContent("处理额度不足，请稍后再试。");
      expect(within(screen.getByRole("region", { name: "收藏还在，整理没有完成" })).getByRole("alert")).toBeInTheDocument();
      unmount();

      render(
        <VideoDetailView item={failed} transcriptPages={[]} actionError onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
      );
      expect(screen.getByRole("alert")).toHaveTextContent("操作未完成，请稍后重试。");
    });

    it("tells a refusal for lack of quota from any other failure, in the hero of an item that is fine", () => {
      render(
        <VideoDetailView item={item} transcriptPages={[]} actionError actionQuotaExceeded onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
      );
      expect(screen.getByRole("alert", { name: "视频操作失败" })).toHaveTextContent("处理额度不足，请稍后再试。");
    });

    it("never shows the rate-limit copy the server could not produce", () => {
      for (const code of ["youtube_rate_limited", "bilibili_rate_limited"]) {
        const { unmount } = render(
          <VideoDetailView item={{ ...failed, error_code: code }} transcriptPages={[]} onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
        );
        expect(screen.queryByText(/服务器暂时无法访问/)).not.toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "浏览器伴侣" })).not.toBeInTheDocument();
        unmount();
      }
    });

    it("does not show the unfinished screen or the mark for a searchable item, or one still being prepared", () => {
      const { rerender } = render(
        <VideoDetailView item={{ ...item, body_searchable: true }} transcriptPages={[]} onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
      );
      expect(screen.queryByRole("region", { name: "收藏还在，整理没有完成" })).not.toBeInTheDocument();
      expect(screen.queryByText(/正文不可检索/)).not.toBeInTheDocument();
      expect(screen.getByText("可检索")).toBeInTheDocument();

      rerender(
        <VideoDetailView
          item={{ ...item, lifecycle: "processing", body_searchable: false, stages: [{ key: "body", status: "running", detail: "transcribing" }], available_actions: ["archive"] }}
          transcriptPages={[]}
          onLoadMore={vi.fn()}
          onArchive={vi.fn()}
          onRestore={vi.fn()}
          onRetry={vi.fn()}
          onUpdateWhySaved={vi.fn()}
        />,
      );
      expect(screen.getByText("整理中")).toBeInTheDocument();
      expect(screen.queryByText(/正文不可检索/)).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "收藏还在，整理没有完成" })).not.toBeInTheDocument();
    });
  });

  describe("stages after the body", () => {
    const withStages = (...later: Array<{ key: string; status: "pending" | "running" | "done" | "failed" | "skipped" }>): LibraryItem => ({
      ...item,
      body_searchable: true,
      stages: [{ key: "body", status: "done" }, ...later],
    });
    function renderItem(value: LibraryItem) {
      render(
        <VideoDetailView item={value} transcriptPages={[]} onLoadMore={vi.fn()} onArchive={vi.fn()} onRestore={vi.fn()} onRetry={vi.fn()} onUpdateWhySaved={vi.fn()} />,
      );
    }

    it("shows no region until the server lists a stage", () => {
      renderItem(withStages());
      expect(screen.queryByRole("heading", { name: "分类与主题" })).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "内容要点" })).not.toBeInTheDocument();
    });

    it("keeps a partly successful item searchable, with the placeholder of each stage that failed", () => {
      renderItem(withStages({ key: "classify", status: "failed" }, { key: "summary", status: "failed" }));

      expect(screen.getByText("可检索")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "分类与主题" })).toBeInTheDocument();
      expect(screen.getByText("未分类")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "内容要点" })).toBeInTheDocument();
      expect(screen.getByText("暂无内容要点")).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "收藏还在，整理没有完成" })).not.toBeInTheDocument();
      expect(screen.queryByText(/正文不可检索/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "在 YouTube 查看" })).toBeInTheDocument();
    });

    it("says a stage that has not finished is in progress, and a movie stage shows its placeholder", () => {
      renderItem(withStages({ key: "classify", status: "running" }, { key: "summary", status: "pending" }, { key: "movie", status: "failed" }));

      expect(screen.getAllByText("整理中…")).toHaveLength(2);
      expect(screen.getByRole("heading", { name: "电影识别" })).toBeInTheDocument();
      expect(screen.getByText("未识别")).toBeInTheDocument();
    });

    it("leaves out skipped stages and stages it does not know", () => {
      renderItem(withStages({ key: "classify", status: "skipped" }, { key: "translation", status: "failed" }));

      expect(screen.queryByRole("heading", { name: "分类与主题" })).not.toBeInTheDocument();
      expect(screen.queryByText(/translation/)).not.toBeInTheDocument();
      expect(screen.queryByText("未分类")).not.toBeInTheDocument();
    });
  });

  it("maps language codes and cover placeholders to user-facing copy", () => {
    const { rerender } = render(
      <VideoDetailView
        item={{ ...item, lang: "zh-Hans", cover_url: null }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );

    expect(screen.getByText("简体中文")).toBeInTheDocument();
    expect(screen.getByText("暂无封面")).toBeInTheDocument();
    expect(screen.queryByText(/ZH-HANS|YT/)).not.toBeInTheDocument();

    rerender(
      <VideoDetailView
        item={{ ...item, lang: "x-private", cover_url: null }}
        transcriptPages={[]}
        onLoadMore={vi.fn()}
        onArchive={vi.fn()}
        onRestore={vi.fn()}
        onRetry={vi.fn()}
        onUpdateWhySaved={vi.fn()}
      />,
    );
    expect(screen.queryByText(/X-PRIVATE/)).not.toBeInTheDocument();
  });
});
