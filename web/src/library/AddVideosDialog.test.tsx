import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AddVideosDialog } from "./AddVideosDialog";

const shareTitle = "【一个小改装作坊，如何凭一台”红猪“改写奔驰赛车历史？】";
const shareUrl = "https://www.bilibili.com/video/BV15pao6iEZo/?share_source=copy_web&vd_source=test";

describe("add videos dialog", () => {
  it("keeps an explicit P selector when switching the direct draft into part preview", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AddVideosDialog open partsEnabled favoritesEnabled onClose={() => undefined} /></QueryClientProvider>);
    const value = "https://www.bilibili.com/video/BV1xx411c7mD?p=21";
    await user.type(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"), value);
    await user.click(screen.getByRole("button", { name: "选择 B 站分 P" }));
    expect(screen.getByLabelText("B 站视频 BV 号或链接")).toHaveValue(value);
    expect(screen.getByRole("button", { name: "导入公开 B 站收藏夹" })).toBeInTheDocument();
  });
  it("opens modally on first render without a declarative open attribute", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");

    render(<AddVideosDialog open onClose={() => undefined} />);

    expect(showModal).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog", { name: "添加视频链接" })).toBeInTheDocument();
    expect(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个")).toHaveAttribute(
      "placeholder",
      "输入 BV 号或粘贴 YouTube、Bilibili 链接",
    );
    expect(screen.getByText("输入链接或 BV 号（如 BV1xx411c7mD）后按 Enter；可直接粘贴 B 站分享文字，也可以混合粘贴多行。")).toBeInTheDocument();
  });

  it.each([
    `${shareTitle} ${shareUrl}`,
    `${shareTitle}\n${shareUrl}`,
    `分享一下 ${shareUrl} 快来看看`,
  ])(
    "pastes complete Bilibili share text as a URL tag without a title row: %s",
    async (share) => {
      const submit = vi.fn().mockResolvedValue({ results: [] });
      const user = userEvent.setup();
      render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);

      const input = screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个");
      await user.click(input);
      await user.paste(share);

      const entries = screen.getByRole("list", { name: "已添加的视频" });
      expect(within(entries).getAllByRole("listitem")).toHaveLength(1);
      expect(within(entries).getByText(shareUrl)).toBeInTheDocument();
      expect(within(entries).queryByText(shareTitle)).not.toBeInTheDocument();
      expect(input).toHaveValue("");
      await user.click(screen.getByRole("button", { name: "添加并整理" }));
      expect(submit).toHaveBeenCalledWith({ urls: [shareUrl], why_saved: null });
    },
  );

  it("submits an unconfirmed share draft through the same extraction path", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    const user = userEvent.setup();
    render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);
    const input = screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个");

    await user.type(input, `${shareTitle} ${shareUrl}`);
    expect(screen.queryByRole("list", { name: "已添加的视频" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).toHaveBeenCalledWith({ urls: [shareUrl], why_saved: null });
  });

  it("keeps mixed pasted shares, bare BV IDs, direct URLs and invalid lines in order", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    const user = userEvent.setup();
    render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);

    await user.click(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"));
    await user.paste(
      `${shareTitle}\n${shareUrl}\nbv1xx411c7mD\nnot a video\n`
      + `https://youtu.be/dQw4w9WgXcQ\n${shareTitle} ${shareUrl}`,
    );
    expect(screen.getByText("5 / 10")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).toHaveBeenCalledWith({
      urls: [shareUrl, "bv1xx411c7mD", "not a video", "https://youtu.be/dQw4w9WgXcQ", shareUrl],
      why_saved: null,
    });
  });

  it("merges a paste with the current selection and retains existing tags", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    const user = userEvent.setup();
    render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);
    const input = screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个") as HTMLTextAreaElement;
    await user.click(input);
    await user.paste("https://youtu.be/dQw4w9WgXcQ");
    fireEvent.change(input, { target: { value: "BV1xx411c7mD\nreplace me\nnot a video" } });
    input.setSelectionRange(13, 23);

    await user.paste(`${shareTitle} ${shareUrl}`);
    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).toHaveBeenCalledWith({
      urls: ["https://youtu.be/dQw4w9WgXcQ", "BV1xx411c7mD", shareUrl, "not a video"],
      why_saved: null,
    });
  });

  it("applies the ten-video limit after extracting pasted share blocks", async () => {
    const submit = vi.fn();
    const user = userEvent.setup();
    render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);
    await user.click(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"));
    await user.paste(Array.from({ length: 11 }, () => `${shareTitle}\n${shareUrl}`).join("\n"));
    expect(screen.getByText("11 / 10")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("一次最多添加 10 个视频");
  });

  it.each([
    { input: "  BV1xx411c7mD  ", urls: ["BV1xx411c7mD"] },
    {
      input: "  bv1xx411c7mD  \nhttps://youtu.be/dQw4w9WgXcQ\n https://www.bilibili.com/video/BV1xx411c7mD ",
      urls: ["bv1xx411c7mD", "https://youtu.be/dQw4w9WgXcQ", "https://www.bilibili.com/video/BV1xx411c7mD"],
    },
  ])("submits BV input and mixed lines without client canonicalization: $input", async ({ input, urls }) => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    const user = userEvent.setup();
    render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);

    await user.type(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"), input);
    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).toHaveBeenCalledOnce();
    expect(submit).toHaveBeenCalledWith({ urls, why_saved: null });
  });

  it("submits 1-10 trimmed URLs and renders per-item partial outcomes", async () => {
    const submit = vi.fn().mockResolvedValue({
      results: [
        { input_index: 0, status: "queued", item_public_id: "item-1", lifecycle: "queued" },
        { input_index: 1, status: "unsupported_url", error_code: "unsupported_url" },
        { input_index: 2, status: "quota_exceeded", safe_error_code: "quota_exceeded" },
      ],
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={client}>
        <AddVideosDialog open onClose={() => undefined} submitBatch={submit} />
      </QueryClientProvider>,
    );

    const dialog = screen.getByRole("dialog", { name: "添加视频链接" });
    await user.type(
      within(dialog).getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"),
      "https://youtu.be/dQw4w9WgXcQ\nhttps://example.com/nope",
    );
    await user.type(within(dialog).getByLabelText("备注（可选）"), "准备周末精读");
    await user.click(within(dialog).getByRole("button", { name: "添加并整理" }));

    expect(submit).toHaveBeenCalledWith({
      urls: ["https://youtu.be/dQw4w9WgXcQ", "https://example.com/nope"],
      why_saved: "准备周末精读",
    });
    expect(await within(dialog).findByText("已添加，等待整理")).toBeInTheDocument();
    expect(within(dialog).getByText("暂不支持这个链接或 BV 号")).toBeInTheDocument();
    expect(within(dialog).getByText("已达到保存上限")).toBeInTheDocument();
    expect(within(dialog).getByText("第 1 个视频")).toBeInTheDocument();
    expect(within(dialog).queryByText(/队列暂时不可用|请求已处理/)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("list", { name: "添加结果" })).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(within(dialog).getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个")).toHaveAttribute("name", "urls");
    expect(within(dialog).getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个")).toHaveAttribute("autocomplete", "off");
    expect(within(dialog).getByLabelText("备注（可选）")).toHaveAttribute("name", "why-saved");
  });

  it("adds an existing collection tag without changing the request contract", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    const user = userEvent.setup();
    render(
      <AddVideosDialog
        open
        onClose={() => undefined}
        submitBatch={submit}
        suggestedCollections={["产品调研", "AI_入门"]}
      />,
    );

    await user.type(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"), "https://youtu.be/dQw4w9WgXcQ");
    await user.type(screen.getByLabelText("备注（可选）"), "准备周末精读");
    await user.click(screen.getByRole("button", { name: "产品调研" }));
    expect(screen.getByRole("button", { name: "产品调研" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).toHaveBeenCalledWith({
      urls: ["https://youtu.be/dQw4w9WgXcQ"],
      why_saved: "准备周末精读 #产品调研",
    });
  });

  it("validates a new collection and can return to the unclassified default", async () => {
    const submit = vi.fn().mockResolvedValue({ results: [] });
    const user = userEvent.setup();
    render(
      <AddVideosDialog
        open
        onClose={() => undefined}
        submitBatch={submit}
        suggestedCollections={["产品调研"]}
      />,
    );

    await user.type(screen.getByLabelText("新收藏夹名称"), "名称 有空格");
    await user.click(screen.getByRole("button", { name: "创建并选择" }));
    expect(screen.getByRole("alert")).toHaveTextContent("名称只能使用中文、字母、数字、短横线或下划线");

    await user.clear(screen.getByLabelText("新收藏夹名称"));
    await user.type(screen.getByLabelText("新收藏夹名称"), "访谈笔记");
    await user.click(screen.getByRole("button", { name: "创建并选择" }));
    expect(screen.getByRole("button", { name: "访谈笔记" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "未归类" }));
    expect(screen.getByRole("button", { name: "未归类" })).toHaveAttribute("aria-pressed", "true");

    await user.type(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"), "https://youtu.be/dQw4w9WgXcQ");
    await user.click(screen.getByRole("button", { name: "添加并整理" }));
    expect(submit).toHaveBeenCalledWith({
      urls: ["https://youtu.be/dQw4w9WgXcQ"],
      why_saved: null,
    });
  });

  it("uses a multiline reason field with a native vertical resize affordance", () => {
    render(<AddVideosDialog open onClose={() => undefined} />);

    const reason = screen.getByLabelText("备注（可选）");
    expect(reason.tagName).toBe("TEXTAREA");
    expect(reason).toHaveClass("why-saved-textarea");
    expect(reason).toHaveAttribute("maxlength", "500");
    expect(screen.getByText("0 / 500")).toBeInTheDocument();
  });

  it("starts with a compact URL input and turns confirmed links into removable tags", async () => {
    const user = userEvent.setup();
    render(<AddVideosDialog open onClose={() => undefined} />);

    const input = screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个");
    expect(input).toHaveAttribute("rows", "1");
    expect(input).toHaveClass("url-draft-input");

    await user.type(input, "https://youtu.be/first");
    await user.keyboard("{Enter}");
    const links = screen.getByRole("list", { name: "已添加的视频" });
    expect(within(links).getByText("https://youtu.be/first")).toBeInTheDocument();
    expect(input).toHaveValue("");

    await user.type(input, "https://www.youtube.com/watch?v=second");
    await user.keyboard("{Enter}");
    expect(within(links).getByText("https://www.youtube.com/watch?v=second")).toBeInTheDocument();
    expect(screen.getByText("2 / 10")).toBeInTheDocument();

    await user.click(within(links).getByRole("button", { name: "移除视频 1" }));
    expect(within(links).queryByText("https://youtu.be/first")).not.toBeInTheDocument();
    expect(within(links).getByText("https://www.youtube.com/watch?v=second")).toBeInTheDocument();
  });

  it("blocks more than ten non-empty URLs before a network call", async () => {
    const submit = vi.fn();
    const user = userEvent.setup();

    render(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);
    await user.type(
      screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"),
      Array.from({ length: 11 }, (_, index) => `https://youtu.be/video${index}`).join("\n"),
    );
    await user.click(screen.getByRole("button", { name: "添加并整理" }));

    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("一次最多添加 10 个视频");
  });

  it("clears form values and submission results after closing", async () => {
    const submit = vi.fn().mockResolvedValue({
      results: [
        { input_index: 0, status: "queued", item_public_id: "item-1", lifecycle: "queued" },
      ],
    });
    const user = userEvent.setup();
    const { rerender } = render(
      <AddVideosDialog open onClose={() => undefined} submitBatch={submit} />,
    );

    await user.type(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"), "https://youtu.be/dQw4w9WgXcQ");
    await user.type(screen.getByLabelText("备注（可选）"), "准备周末精读");
    await user.click(screen.getByRole("button", { name: "添加并整理" }));
    expect(await screen.findByText("已添加，等待整理")).toBeInTheDocument();

    rerender(<AddVideosDialog open={false} onClose={() => undefined} submitBatch={submit} />);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    rerender(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);

    expect(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个")).toHaveValue("");
    expect(screen.getByLabelText("备注（可选）")).toHaveValue("");
    expect(screen.queryByText("已添加，等待整理")).not.toBeInTheDocument();
  });

  it("ignores a submission result that arrives after closing", async () => {
    let resolveSubmit: (result: {
      results: Array<{
        input_index: number;
        status: "queued";
        item_public_id: string;
        lifecycle: "queued";
        result_id: string;
      }>;
    }) => void = () => undefined;
    const submit = vi.fn(() => new Promise<{
      results: Array<{
        input_index: number;
        status: "queued";
        item_public_id: string;
        lifecycle: "queued";
        result_id: string;
      }>;
    }>((resolve) => {
      resolveSubmit = resolve;
    }));
    const user = userEvent.setup();
    const { rerender } = render(
      <AddVideosDialog open onClose={() => undefined} submitBatch={submit} />,
    );

    await user.type(screen.getByLabelText("BV 号、YouTube 或 Bilibili 链接，每行一个"), "https://youtu.be/dQw4w9WgXcQ");
    await user.click(screen.getByRole("button", { name: "添加并整理" }));
    rerender(<AddVideosDialog open={false} onClose={() => undefined} submitBatch={submit} />);
    await act(async () => {
      resolveSubmit({
        results: [
          {
            input_index: 0,
            status: "queued",
            item_public_id: "item-1",
            lifecycle: "queued",
            result_id: "result-1",
          },
        ],
      });
    });
    rerender(<AddVideosDialog open onClose={() => undefined} submitBatch={submit} />);

    expect(screen.queryByText("已添加，等待整理")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加并整理" })).toBeEnabled();
  });
});
