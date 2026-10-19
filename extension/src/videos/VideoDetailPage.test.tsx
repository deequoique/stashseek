import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getLibraryItem, retryItem } from "../api/client";
import type { LibraryItem } from "../api/contracts";
import { VideoDetailPage } from "./VideoDetailPage";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  getLibraryItem: vi.fn(),
  getTranscript: vi.fn(),
  retryItem: vi.fn(),
  archiveItem: vi.fn(),
  restoreItem: vi.fn(),
  updateWhySaved: vi.fn(),
}));

const NO_SPEECH = { code: "asr_no_speech", category: "no_speech", retry_useful: false } as const;

const failed: LibraryItem = {
  public_id: "video-public",
  platform: "youtube",
  kind: "video",
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  title: "没有语音的视频",
  author: "Notebook Studio",
  published_at: null,
  duration_sec: 620,
  lang: "zh",
  description: null,
  tags: [],
  chapters: [],
  cover_url: null,
  saved_at: "2026-08-07T10:00:00Z",
  why_saved: null,
  text_source: "none",
  lifecycle: "failed",
  error_code: "asr_no_speech",
  available_actions: ["retry", "archive", "edit_why_saved", "open_source"],
  latest_dispatch_public_id: "dispatch-public",
  reason: NO_SPEECH,
  stages: [{ key: "body", status: "failed", reason: NO_SPEECH }],
  body_searchable: false,
};

function LibraryProbe() {
  const location = useLocation();
  return <p>资料库 {location.pathname}{location.search}</p>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/videos/video-public"]}>
        <Routes>
          <Route path="/videos/:id" element={<VideoDetailPage />} />
          <Route path="/library" element={<LibraryProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getLibraryItem).mockReset().mockResolvedValue(failed);
  vi.mocked(retryItem).mockReset();
});

describe("video detail page", () => {
  it("opens the library search from 「仅查标题 / 备注」", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "仅查标题 / 备注" }));

    expect(await screen.findByText("资料库 /library?focus=search")).toBeInTheDocument();
  });

  it("runs ingestion again and shows the item as queued once the server accepts", async () => {
    const user = userEvent.setup();
    vi.mocked(retryItem).mockResolvedValue({ ...failed, lifecycle: "queued", error_code: null, reason: null, available_actions: ["archive"], stages: [{ key: "body", status: "pending", detail: "queued" }] });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "重新整理" }));

    await waitFor(() => expect(screen.queryByRole("region", { name: "收藏还在，整理没有完成" })).not.toBeInTheDocument());
    expect(retryItem).toHaveBeenCalledWith("video-public");
    expect(screen.getByText("排队中")).toBeInTheDocument();
  });

  it("says so when the retry is refused for lack of quota, and keeps the item as it was", async () => {
    const user = userEvent.setup();
    vi.mocked(retryItem).mockRejectedValue(new ApiError(429, "quota_exceeded", "已达到当前保存额度，请稍后重试"));
    renderPage();

    await user.click(await screen.findByRole("button", { name: "重新整理" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("处理额度不足，请稍后再试。");
    expect(screen.getByRole("region", { name: "收藏还在，整理没有完成" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新整理" })).toBeEnabled();
  });

  it("keeps the generic message for any other refusal", async () => {
    const user = userEvent.setup();
    vi.mocked(retryItem).mockRejectedValue(new ApiError(409, "retry_unavailable", "当前状态不能重试"));
    renderPage();

    await user.click(await screen.findByRole("button", { name: "重新整理" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("操作未完成，请稍后重试。");
  });
});
