import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { BilibiliFavoriteVideo, BilibiliFavoritesPreview, BilibiliPartsPreview } from "../api/contracts";
import { FavoritesPicker } from "./FavoritesPicker";

const source = "https://space.bilibili.com/400561576/favlist?fid=4157547776&ftype=create";
function video(index = 1): BilibiliFavoriteVideo {
  const bvid = index === 1 ? "BV1xx411c7mD" : "BV1xx411c7mE";
  return { entry_key: `video-${index}`, bvid, canonical_url: `https://www.bilibili.com/video/${bvid}`,
    title: `父视频 ${index}`, available: true, unavailable_reason: null };
}
function favorites(items = [video()], index = 1, hasMore = false): BilibiliFavoritesPreview {
  return { collection_id: "4157547776", title: "study", reported_total: 8, page: index,
    page_size: 20, has_more: hasMore, capped: false, items };
}
function parts(count = 2, bvid = "BV1xx411c7mD", legacy = false): BilibiliPartsPreview {
  return { bvid, title: "父视频", page: 1, page_size: 20, total_parts: count, has_more: false,
    parts: Array.from({ length: count }, (_, index) => ({ part_key: `${bvid}:${index + 1}`,
      part_ref: `ref-${bvid}-${index + 1}`, page: index + 1, title: `课程 ${index + 1}`,
      selectable: true, has_unverified_legacy_item: legacy && index === 0 })) };
}
function mount(props: Partial<Parameters<typeof FavoritesPicker>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const preview = vi.fn().mockResolvedValue(favorites());
  const loadParts = vi.fn().mockResolvedValue(parts());
  const submit = vi.fn().mockResolvedValue({ results: [] });
  const submitted = vi.fn();
  const view = render(<QueryClientProvider client={client}><FavoritesPicker whySaved="#课程"
    onSubmitted={submitted} previewFavorites={preview} previewParts={loadParts} submitParts={submit} {...props} />
  </QueryClientProvider>);
  return { ...view, preview, loadParts, submit, submitted };
}
async function read() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("公开 B 站收藏夹链接"), source);
  await user.click(screen.getByRole("button", { name: "预览收藏夹（清空原选择）" }));
  await screen.findByRole("heading", { name: "study" });
  return user;
}
async function expand(user: ReturnType<typeof userEvent.setup>, title = "父视频 1") {
  await user.click(screen.getByText(title));
  return screen.findByRole("list", { name: `${title}的分 P` });
}

describe("public favorites picker", () => {
  it("previews eight parents read-only, lazily expands parts and selects nothing by default", async () => {
    const items = Array.from({ length: 8 }, (_, index) => ({ ...video(), entry_key: `entry-${index}`, title: `视频 ${index + 1}` }));
    const preview = vi.fn().mockResolvedValue(favorites(items));
    const { loadParts, submit } = mount({ previewFavorites: preview });
    const user = await read();
    expect(preview).toHaveBeenCalledWith({ url: source, page: 1 }, expect.any(AbortSignal));
    expect(within(screen.getByRole("list", { name: "收藏夹视频" })).getAllByRole("listitem")).toHaveLength(8);
    expect(loadParts).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    await expand(user, "视频 1");
    expect(loadParts).toHaveBeenCalledWith({ url: video().canonical_url, page: 1 }, expect.any(AbortSignal));
    expect(screen.getByText("已选 0 / 10 个分 P")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox").every((value) => !(value as HTMLInputElement).checked)).toBe(true);
  });

  it("preserves distinct parts and stable cross-page dedupe without hidden requests", async () => {
    const preview = vi.fn().mockImplementation(({ page }) => Promise.resolve(page === 2 ? favorites([video(), video(2)], 2) : favorites([video()], 1, true)));
    const loadParts = vi.fn().mockImplementation(({ url }) => Promise.resolve(parts(2, url.endsWith("c7mE") ? "BV1xx411c7mE" : "BV1xx411c7mD")));
    const { submit } = mount({ previewFavorites: preview, previewParts: loadParts });
    const user = await read();
    await expand(user);
    await user.click(screen.getByRole("checkbox", { name: /^父视频 1 · P2/ }));
    await user.click(screen.getByRole("button", { name: "收藏夹下一页" }));
    await expand(user);
    expect(screen.getByRole("checkbox", { name: /^父视频 1 · P2/ })).toBeChecked();
    await expand(user, "父视频 2");
    await user.click(screen.getByRole("checkbox", { name: /^父视频 2 · P2/ }));
    expect(screen.getByText("已选 2 / 10 个分 P")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "添加已选 2 个分 P" }));
    expect(submit.mock.calls[0]![0]).toEqual({ why_saved: "#课程", selections: [
      { part_ref: "ref-BV1xx411c7mD-2", allow_legacy_overlap: false },
      { part_ref: "ref-BV1xx411c7mE-2", allow_legacy_overlap: false },
    ] });
    expect(preview).toHaveBeenCalledTimes(2);
  });

  it("bulk selection affects only displayed loaded rows and rejects more than ten atomically", async () => {
    mount({ previewFavorites: vi.fn().mockResolvedValue(favorites([video(), video(2)])), previewParts: vi.fn().mockResolvedValue(parts(11)) });
    const user = await read();
    expect(screen.getByRole("button", { name: "选择本页已显示分 P" })).toBeDisabled();
    await expand(user);
    await user.click(screen.getByRole("button", { name: "选择本页已显示分 P" }));
    expect(screen.getByRole("alert")).toHaveTextContent("超过 10 项，原选择已保留");
    expect(screen.getByText("已选 0 / 10 个分 P")).toBeInTheDocument();
  });

  it("offers one persistent selection status and a keyboard-focusable confirmation jump", async () => {
    const scroll = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scroll;
    try {
      mount();
      const user = await read(); await expand(user);
      await user.click(screen.getByRole("checkbox", { name: /^父视频 1 · P1/ }));
      expect(screen.getAllByText("已选 1 / 10 个分 P")).toHaveLength(1);
      await user.click(screen.getByRole("button", { name: "查看选择与确认" }));
      expect(screen.getByRole("region", { name: "分 P 选择与确认" })).toHaveFocus();
      expect(scroll).toHaveBeenCalledWith({ block: "start", behavior: "instant" });
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it("allows legacy acknowledgement from the persistent summary after collapsing a parent", async () => {
    const { submit } = mount({ previewParts: vi.fn().mockResolvedValue(parts(1, "BV1xx411c7mD", true)) });
    const user = await read();
    await expand(user);
    await user.click(screen.getByRole("checkbox", { name: /^父视频 1 · P1/ }));
    await user.click(screen.getByText("父视频 1"));
    const ack = await screen.findByRole("checkbox", { name: /^已选项目确认/ });
    expect(ack).not.toBeChecked();
    expect(screen.getByRole("button", { name: "添加已选 1 个分 P" })).toBeDisabled();
    await user.click(ack);
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    expect(submit.mock.calls[0]![0].selections[0].allow_legacy_overlap).toBe(true);
  });

  it("keeps partial results aligned to the frozen ordered rows and removes accepted selections", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [
      { result_id: "r1", input_index: 1, status: "quota_exceeded" },
      { result_id: "r0", input_index: 0, status: "queued" },
    ] });
    mount({ submitParts: submit });
    const user = await read(); await expand(user);
    await user.click(screen.getByRole("button", { name: "选择本页已显示分 P" }));
    await user.click(screen.getByRole("button", { name: "添加已选 2 个分 P" }));
    const result = await screen.findByRole("list", { name: "分 P 添加结果" });
    expect(within(result).getAllByRole("listitem")[0]).toHaveTextContent("P2 课程 2当前添加额度不足");
    expect(screen.getByText("已选 1 / 10 个分 P")).toBeInTheDocument();
  });

  it("reuses the same uncertain operation and synchronously prevents duplicate submits", async () => {
    let reject!: (reason: Error) => void;
    const submit = vi.fn().mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; })).mockResolvedValueOnce({ results: [] });
    mount({ submitParts: submit });
    const user = await read(); await expand(user);
    await user.click(screen.getByRole("checkbox", { name: /^父视频 1 · P1/ }));
    const button = screen.getByRole("button", { name: "添加已选 1 个分 P" });
    fireEvent.click(button); fireEvent.click(button);
    expect(submit).toHaveBeenCalledTimes(1);
    await act(async () => reject(new Error("unknown")));
    expect(screen.getByRole("button", { name: "预览收藏夹（清空原选择）" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重新确认本次结果" }));
    expect(submit.mock.calls[1]![0]).toEqual(submit.mock.calls[0]![0]);
    expect(submit.mock.calls[1]![1]).toBe(submit.mock.calls[0]![1]);
  });

  it("cancels old source reads and never lets a late list replace the new collection", async () => {
    let resolveOld!: (value: BilibiliFavoritesPreview) => void;
    const preview = vi.fn().mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ ...favorites(), title: "新收藏夹" });
    mount({ previewFavorites: preview });
    const user = userEvent.setup();
    const field = screen.getByLabelText("公开 B 站收藏夹链接");
    await user.type(field, source); await user.click(screen.getByRole("button", { name: "预览收藏夹（清空原选择）" }));
    const oldSignal = preview.mock.calls[0]![1] as AbortSignal;
    await user.clear(field); await user.type(field, source.replace("4157547776", "4157547777"));
    await user.click(screen.getByRole("button", { name: "预览收藏夹（清空原选择）" }));
    await screen.findByRole("heading", { name: "新收藏夹" });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld(favorites()));
    expect(screen.queryByRole("heading", { name: "study" })).not.toBeInTheDocument();
  });

  it("aborts a pending import on private view disposal and ignores its late result", async () => {
    let resolve!: (value: { results: [] }) => void;
    const submit = vi.fn().mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { unmount, submitted } = mount({ submitParts: submit });
    const user = await read(); await expand(user);
    await user.click(screen.getByRole("checkbox", { name: /^父视频 1 · P1/ }));
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    const signal = submit.mock.calls[0]![2] as AbortSignal;
    unmount(); expect(signal.aborted).toBe(true);
    await act(async () => resolve({ results: [] }));
    expect(submitted).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError(409, "bilibili_favorites_private", "private"), "只支持公开收藏夹"],
    [new ApiError(422, "bilibili_limit_exceeded", "large"), "超过预览范围"],
  ])("keeps inaccessible and cap errors distinct from an empty folder", async (error, copy) => {
    mount({ previewFavorites: vi.fn().mockRejectedValue(error) });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("公开 B 站收藏夹链接"), source);
    await user.click(screen.getByRole("button", { name: "预览收藏夹（清空原选择）" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(copy));
    expect(screen.queryByText("这个公开收藏夹当前没有条目。")).not.toBeInTheDocument();
  });

  it("reports the collection preview cap without enabling an unseen next page", async () => {
    mount({ previewFavorites: vi.fn().mockResolvedValue({ ...favorites(), page: 500, has_more: true, capped: true }) });
    await read();
    expect(screen.getByText(/已到第 500 页预览上限/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收藏夹下一页" })).toBeDisabled();
  });

  it("blocks locally expired proof until an explicit same-key refresh without selecting a replacement ordinal", async () => {
    const expired = parts(1);
    expired.parts[0]!.expires_at = Math.floor(Date.now() / 1000) - 1;
    const fresh = parts(1);
    fresh.parts[0] = { ...fresh.parts[0]!, page: 6, part_ref: "fresh-same-cid", expires_at: Math.floor(Date.now() / 1000) + 1800 };
    const preview = vi.fn().mockResolvedValueOnce(expired).mockResolvedValueOnce(fresh);
    const { submit } = mount({ previewParts: preview });
    const user = await read(); await expand(user);
    await user.click(screen.getByRole("checkbox", { name: /^父视频 1 · P1/ }));
    expect(screen.getByRole("button", { name: "添加已选 1 个分 P" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("选择已过期");
    await user.click(screen.getByRole("button", { name: "刷新该视频当前页的分 P 凭据" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "添加已选 1 个分 P" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    expect(submit.mock.calls[0]![0].selections).toEqual([{ part_ref: "fresh-same-cid", allow_legacy_overlap: false }]);
  });
});
