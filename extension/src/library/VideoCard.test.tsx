import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { LibraryItem } from "../api/contracts";
import { VideoCard } from "./VideoCard";

const baseItem: LibraryItem = {
  public_id: "video-public",
  platform: "youtube",
  kind: "video",
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  title: "一段值得反复看的访谈",
  author: "Notebook Studio",
  published_at: null,
  duration_sec: 754,
  lang: "zh",
  description: null,
  tags: [],
  chapters: [],
  cover_url: "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
  saved_at: "2026-08-07T10:00:00Z",
  why_saved: "学习提问方式",
  text_source: "youtube_captions",
  lifecycle: "ready",
  error_code: null,
  available_actions: ["archive", "edit_why_saved", "open_source"],
  latest_dispatch_public_id: "dispatch-public",
};

describe("video card", () => {
  it("shows readable metadata and a server-derived lifecycle", () => {
    const { container } = render(<MemoryRouter><VideoCard item={baseItem} /></MemoryRouter>);

    expect(screen.getByRole("link", { name: "一段值得反复看的访谈，查看详情" })).toHaveAttribute(
      "href",
      "/videos/video-public",
    );
    expect(screen.getByText("12:34")).toBeInTheDocument();
    expect(screen.getByText("可检索")).toBeInTheDocument();
    expect(screen.getByText("学习提问方式")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /编辑《一段值得反复看的访谈》的保存说明和收藏夹/ })).toHaveAttribute(
      "href",
      "/videos/video-public#saved-context",
    );
    expect(container.querySelector("img")).toHaveAttribute("width", "960");
    expect(container.querySelector("img")).toHaveAttribute("height", "540");
  });

  it("uses the shared route transition when opening a video detail", async () => {
    const startViewTransition = vi.fn((update: () => void) => update());
    Object.defineProperty(document, "startViewTransition", {
      configurable: true,
      value: startViewTransition,
    });
    const user = userEvent.setup();

    try {
      render(
        <MemoryRouter initialEntries={["/library"]}>
          <Routes>
            <Route path="/library" element={<VideoCard item={baseItem} />} />
            <Route path="/videos/:id" element={<h1>视频详情</h1>} />
          </Routes>
        </MemoryRouter>,
      );

      await user.click(screen.getByRole("link", { name: "一段值得反复看的访谈，查看详情" }));

      expect(startViewTransition).toHaveBeenCalledOnce();
      expect(screen.getByRole("heading", { name: "视频详情" })).toBeInTheDocument();
    } finally {
      Reflect.deleteProperty(document, "startViewTransition");
    }
  });

  it("never invents metadata for a newly queued item", () => {
    render(
      <MemoryRouter>
        <VideoCard item={{ ...baseItem, title: null, author: null, cover_url: null, duration_sec: null, lifecycle: "queued" }} />
      </MemoryRouter>,
    );

    expect(screen.getByText("视频信息尚未准备好")).toBeInTheDocument();
    expect(screen.getByText("排队中")).toBeInTheDocument();
    expect(screen.getByText("暂无封面")).toBeInTheDocument();
    expect(screen.queryByText("YT")).not.toBeInTheDocument();
    expect(screen.queryByText("未知作者")).not.toBeInTheDocument();
  });

  it("uses the same retry action wording as the detail page", () => {
    render(
      <MemoryRouter>
        <VideoCard item={{ ...baseItem, lifecycle: "failed", available_actions: ["retry"] }} />
      </MemoryRouter>,
    );

    expect(screen.getByText("打开详情后可重新整理")).toBeInTheDocument();
    expect(screen.queryByText(/安全重试/)).not.toBeInTheDocument();
  });

  it("says why an unfinished item cannot be searched by its content, and marks it", () => {
    render(
      <MemoryRouter>
        <VideoCard
          item={{
            ...baseItem,
            lifecycle: "failed",
            error_code: "asr_no_speech",
            reason: { code: "asr_no_speech", category: "no_speech", retry_useful: false },
            stages: [{ key: "body", status: "failed", reason: { code: "asr_no_speech", category: "no_speech", retry_useful: false } }],
            body_searchable: false,
            available_actions: ["retry", "archive", "edit_why_saved", "open_source"],
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("整理失败")).toBeInTheDocument();
    expect(screen.getByText("没有字幕，也没有识别出语音")).toBeInTheDocument();
    expect(screen.getByText("正文不可检索")).toBeInTheDocument();
    expect(screen.getByText("打开详情后可重新整理")).toBeInTheDocument();
  });

  it("labels an item that needs action as a failed one and keeps its reason and badge", () => {
    render(
      <MemoryRouter>
        <VideoCard
          item={{
            ...baseItem,
            lifecycle: "needs_action",
            reason: { code: "bilibili_login_required", category: "login_required", retry_useful: false },
            body_searchable: false,
            available_actions: ["archive", "edit_why_saved", "open_source"],
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("整理失败")).toBeInTheDocument();
    expect(screen.getByText("这个视频的字幕需要登录 B站 才能读取")).toBeInTheDocument();
    expect(screen.getByText("正文不可检索")).toBeInTheDocument();
    // The server offers no retry for this reason, so the card does not suggest one.
    expect(screen.queryByText("打开详情后可重新整理")).not.toBeInTheDocument();
  });

  it("explains a failure on a server that sends no reason, and still marks the body", () => {
    render(<MemoryRouter><VideoCard item={{ ...baseItem, lifecycle: "needs_action", available_actions: ["archive"] }} /></MemoryRouter>);

    expect(screen.getByText("没有字幕，暂时无法语音转写")).toBeInTheDocument();
    expect(screen.getByText("正文不可检索")).toBeInTheDocument();
  });

  it("marks neither a searchable item nor one that is still being prepared", () => {
    const { rerender } = render(<MemoryRouter><VideoCard item={{ ...baseItem, body_searchable: true }} /></MemoryRouter>);
    expect(screen.queryByText("正文不可检索")).not.toBeInTheDocument();

    for (const lifecycle of ["queued", "processing"] as const) {
      rerender(
        <MemoryRouter>
          <VideoCard item={{ ...baseItem, lifecycle, body_searchable: false, stages: [{ key: "body", status: lifecycle === "queued" ? "pending" : "running" }], available_actions: ["archive"] }} />
        </MemoryRouter>,
      );
      expect(screen.queryByText("正文不可检索")).not.toBeInTheDocument();
    }
  });

  it("keeps a ready item with failed later stages searchable and unmarked", () => {
    render(
      <MemoryRouter>
        <VideoCard
          item={{
            ...baseItem,
            body_searchable: true,
            stages: [{ key: "body", status: "done" }, { key: "classify", status: "failed" }, { key: "summary", status: "failed" }],
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("可检索")).toBeInTheDocument();
    expect(screen.queryByText("正文不可检索")).not.toBeInTheDocument();
    expect(screen.queryByText("打开详情后可重新整理")).not.toBeInTheDocument();
  });

  it("marks an archived item whose body failed, and shows a lifecycle it does not know as processing", () => {
    const { rerender } = render(
      <MemoryRouter>
        <VideoCard
          item={{ ...baseItem, lifecycle: "archived", body_searchable: false, stages: [{ key: "body", status: "failed" }], available_actions: ["restore"] }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("已归档")).toBeInTheDocument();
    expect(screen.getByText("正文不可检索")).toBeInTheDocument();

    const future: LibraryItem = JSON.parse(JSON.stringify({ ...baseItem, lifecycle: "teleporting" }));
    rerender(<MemoryRouter><VideoCard item={future} /></MemoryRouter>);
    expect(screen.getByText("整理中")).toBeInTheDocument();
  });

  it("separates collection tags from the human save reason", () => {
    render(
      <MemoryRouter>
        <VideoCard item={{ ...baseItem, why_saved: "准备用户访谈 #产品调研 #AI_入门" }} />
      </MemoryRouter>,
    );

    const collections = screen.getByLabelText("所属收藏夹");
    expect(within(collections).getByText("#产品调研")).toBeInTheDocument();
    expect(within(collections).getByText("#AI_入门")).toBeInTheDocument();
    expect(screen.getByText("准备用户访谈")).toBeInTheDocument();
    expect(screen.queryByText("准备用户访谈 #产品调研 #AI_入门")).not.toBeInTheDocument();
  });
});
