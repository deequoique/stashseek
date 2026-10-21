import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { BilibiliPartPreview, BilibiliPartsPreview } from "../api/contracts";
import { ApiError } from "../api/client";
import { PartRows, PartsPicker } from "./PartsPicker";

function part(index: number, legacy = false): BilibiliPartPreview {
  return { part_key: `BV1xx411c7mD:${index}`, part_ref: `signed-${index}`, page: index,
    title: `课程 ${index}`, duration_sec: 100, selectable: true,
    unavailable_reason: null, has_unverified_legacy_item: legacy };
}
function page(parts = [part(1), part(2)], index = 1, hasMore = false): BilibiliPartsPreview {
  return { bvid: "BV1xx411c7mD", title: "父视频", page: index, page_size: 20,
    requested_page: null, total_parts: hasMore ? 21 : parts.length, has_more: hasMore, parts };
}
function mount(props: Partial<Parameters<typeof PartsPicker>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const preview = vi.fn().mockResolvedValue(page());
  const submit = vi.fn().mockResolvedValue({ results: [] });
  const submitted = vi.fn();
  const view = render(<QueryClientProvider client={client}><PartsPicker
    whySaved="#课程" previewParts={preview} submitParts={submit} onSubmitted={submitted} {...props}
  /></QueryClientProvider>);
  return { ...view, client, preview, submit, submitted };
}
async function previewVideo() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("B 站视频 BV 号或链接"), "BV1xx411c7mD");
  await user.click(screen.getByRole("button", { name: "预览分 P（清空原选择）" }));
  await screen.findByRole("list", { name: "父视频的分 P" });
  return user;
}

describe("Bilibili part picker", () => {
  it("previews without selecting or saving and sends exact explicit parts", async () => {
    const { submit, preview } = mount();
    const user = await previewVideo();
    expect(preview).toHaveBeenCalledWith({ url: "BV1xx411c7mD" }, expect.any(AbortSignal));
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByText("已选 0 / 10 个分 P")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /父视频 · P2/ }));
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    expect(submit).toHaveBeenCalledWith({ selections: [{ part_ref: "signed-2", allow_legacy_overlap: false }], why_saved: "#课程" }, expect.any(String), expect.any(AbortSignal));
  });

  it("requires default-off per-entry consent for unverified legacy overlap", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    mount({ previewParts: vi.fn().mockResolvedValue(page([part(1, true)])), submitParts: submit });
    const user = await previewVideo();
    await user.click(screen.getByRole("checkbox", { name: /^父视频 · P1/ }));
    expect(screen.getByRole("button", { name: "添加已选 1 个分 P" })).toBeDisabled();
    const consent = screen.getByRole("checkbox", { name: /我了解并同意/ });
    expect(consent).not.toBeChecked();
    await user.click(consent);
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    expect(submit.mock.calls[0]![0].selections[0].allow_legacy_overlap).toBe(true);
  });

  it("blocks an eleventh part without silently replacing earlier choices", async () => {
    mount({ previewParts: vi.fn().mockResolvedValue(page(Array.from({ length: 11 }, (_, index) => part(index + 1)))) });
    const user = await previewVideo();
    const rows = within(screen.getByRole("list", { name: "父视频的分 P" })).getAllByRole("checkbox");
    for (const checkbox of rows.slice(0, 10)) await user.click(checkbox);
    expect(rows[10]).toBeDisabled();
    expect(screen.getByText("已选 10 / 10 个分 P")).toBeInTheDocument();
  });

  it("retains selected stable refs across preview pages and maps partial results to frozen rows", async () => {
    const preview = vi.fn().mockImplementation(({ page: index }) => Promise.resolve(index === 2
      ? page([part(21)], 2) : page([part(1)], 1, true)));
    const submit = vi.fn().mockResolvedValue({ results: [
      { result_id: "r0", input_index: 0, status: "queued" },
      { result_id: "r1", input_index: 1, status: "quota_exceeded" },
    ] });
    mount({ previewParts: preview, submitParts: submit });
    const user = await previewVideo();
    await user.click(screen.getByRole("checkbox", { name: /^父视频 · P1/ }));
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await user.click(await screen.findByRole("checkbox", { name: /^父视频 · P21/ }));
    await user.click(screen.getByRole("button", { name: "添加已选 2 个分 P" }));
    expect(submit.mock.calls[0]![0].selections.map((value: { part_ref: string }) => value.part_ref)).toEqual(["signed-1", "signed-21"]);
    const result = await screen.findByRole("list", { name: "分 P 添加结果" });
    expect(within(result).getAllByRole("listitem")[0]).toHaveTextContent("P1 课程 1已添加，等待整理");
    expect(screen.getByText("已选 1 / 10 个分 P")).toBeInTheDocument();
  });

  it("reconfirms an uncertain result with the identical key and immutable payload", async () => {
    const submit = vi.fn().mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({ results: [] });
    mount({ submitParts: submit });
    const user = await previewVideo();
    await user.click(screen.getByRole("checkbox", { name: /父视频 · P2/ }));
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    await user.click(await screen.findByRole("button", { name: "重新确认本次结果" }));
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1]![0]).toEqual(submit.mock.calls[0]![0]);
    expect(submit.mock.calls[1]![1]).toBe(submit.mock.calls[0]![1]);
  });

  it("aborts disposal and ignores a late success instead of touching another account", async () => {
    let resolve!: (value: { results: [] }) => void;
    const submit = vi.fn().mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { unmount, submitted } = mount({ submitParts: submit });
    const user = await previewVideo();
    await user.click(screen.getByRole("checkbox", { name: /父视频 · P2/ }));
    await user.click(screen.getByRole("button", { name: "添加已选 1 个分 P" }));
    const signal = submit.mock.calls[0]![2] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ results: [] }));
    expect(submitted).not.toHaveBeenCalled();
  });

  it("shows a preview failure as an error rather than an empty list", async () => {
    mount({ previewParts: vi.fn().mockRejectedValue(new Error("provider")) });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("B 站视频 BV 号或链接"), "BV1xx411c7mD");
    await user.click(screen.getByRole("button", { name: "预览分 P（清空原选择）" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("暂时无法读取"));
    expect(screen.queryByText("没有可显示的分 P。")).not.toBeInTheDocument();
  });

  it("explains a provider preview cap rather than labelling a valid URL invalid", async () => {
    mount({ previewParts: vi.fn().mockRejectedValue(new ApiError(422, "bilibili_limit_exceeded", "limit")) });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("B 站视频 BV 号或链接"), "BV1xx411c7mD");
    await user.click(screen.getByRole("button", { name: "预览分 P（清空原选择）" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("超过当前预览范围"));
    expect(screen.queryByText(/请输入完整 BV 号/)).not.toBeInTheDocument();
  });

  it("controlled shared rows expose no default or whole-video selection", () => {
    const change = vi.fn();
    render(<PartRows parts={[part(2)]} parentTitle="父视频" selected={[]} onChange={change} />);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(change).not.toHaveBeenCalled();
  });
});
