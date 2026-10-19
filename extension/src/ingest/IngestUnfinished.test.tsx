import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { IngestReason, IngestReasonCategory } from "../api/contracts";
import { IngestUnfinished, UnsearchableBadge } from "./IngestUnfinished";

const reason = (category: IngestReasonCategory, retryUseful = true): IngestReason => ({ code: `code_${category}`, category, retry_useful: retryUseful });

function renderUnfinished(overrides: Partial<Parameters<typeof IngestUnfinished>[0]> = {}) {
  return render(
    <IngestUnfinished title="如何从访谈中找到真实需求" platformName="YouTube" reason={reason("no_transcription")} {...overrides} />,
  );
}

describe("IngestUnfinished", () => {
  it("keeps the link and says why the body cannot be searched", () => {
    renderUnfinished();

    expect(screen.getByRole("heading", { name: "收藏还在，整理没有完成" })).toBeInTheDocument();
    expect(screen.getByText("如何从访谈中找到真实需求")).toBeInTheDocument();
    expect(screen.getByText("YouTube · 链接已保存")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("没有字幕，暂时无法语音转写，正文暂不可检索。");
    expect(screen.getByText("可以按标题和备注查找，也可以稍后重试。")).toBeInTheDocument();
  });

  it("shows a status line for every reason category", () => {
    const expected: Array<[IngestReasonCategory, string]> = [
      ["no_speech", "没有字幕，也没有识别出语音，正文暂不可检索。"],
      ["no_transcription", "没有字幕，暂时无法语音转写，正文暂不可检索。"],
      ["fetch_failed", "没能获取视频内容，可能是平台暂时限制了访问，正文暂不可检索。"],
      ["asr_failed", "语音转写服务暂时出错，正文暂不可检索。"],
      ["quota", "处理额度不足，正文暂不可检索。"],
      ["too_large", "视频太长或内容太大，超出处理上限，正文暂不可检索。"],
      ["login_required", "这个视频的字幕需要登录 B站 才能读取，正文暂不可检索。"],
      ["error", "整理时出错了，正文暂不可检索。"],
    ];
    for (const [category, line] of expected) {
      const { unmount } = renderUnfinished({ reason: reason(category) });
      expect(screen.getByRole("status")).toHaveTextContent(line);
      unmount();
    }
  });

  it("warns that another try will probably end the same way when retrying is not useful", () => {
    const { rerender } = renderUnfinished({ reason: reason("no_speech", false) });
    expect(screen.getByText("再试一次大概率还是同样的结果。")).toBeInTheDocument();

    rerender(<IngestUnfinished title="t" platformName="YouTube" reason={reason("fetch_failed", true)} />);
    expect(screen.queryByText("再试一次大概率还是同样的结果。")).not.toBeInTheDocument();
  });

  it("asks a Bilibili login-required item to be collected again from the video page", () => {
    renderUnfinished({ platformName: "B站", reason: reason("login_required", false) });

    expect(screen.getByText("请先登录 B站，再在视频页重新收藏。")).toBeInTheDocument();
    expect(screen.queryByText("可以按标题和备注查找，也可以稍后重试。")).not.toBeInTheDocument();
    expect(screen.queryByText("再试一次大概率还是同样的结果。")).not.toBeInTheDocument();
  });

  it("runs the main action and the title search from their buttons", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const onSearchTitles = vi.fn();
    renderUnfinished({ primary: { kind: "button", label: "重新整理", onClick }, onSearchTitles });

    await user.click(screen.getByRole("button", { name: "重新整理" }));
    expect(onClick).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "仅查标题 / 备注" }));
    expect(onSearchTitles).toHaveBeenCalledOnce();
  });

  it("can offer the video page as a link, for the case where the server cannot retry", () => {
    renderUnfinished({
      reason: reason("login_required", false),
      primary: { kind: "link", label: "打开视频页", href: "https://www.bilibili.com/video/BV1xx411c7mD" },
    });

    const link = screen.getByRole("link", { name: "打开视频页" });
    expect(link).toHaveAttribute("href", "https://www.bilibili.com/video/BV1xx411c7mD");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(screen.queryByRole("button", { name: "重新整理" })).not.toBeInTheDocument();
  });

  it("disables the main button while the request is in flight", () => {
    renderUnfinished({ primary: { kind: "button", label: "重新整理", onClick: vi.fn(), pending: true } });
    expect(screen.getByRole("button", { name: "重新整理" })).toBeDisabled();
  });

  it("leaves out the buttons it was not given", () => {
    renderUnfinished();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows why the last action did not go through", () => {
    renderUnfinished({ error: "处理额度不足，请稍后再试。" });
    expect(screen.getByRole("alert")).toHaveTextContent("处理额度不足，请稍后再试。");
  });

  it("does not invent a title or a platform", () => {
    renderUnfinished({ title: null, platformName: "" });
    expect(screen.getByText("视频信息尚未准备好")).toBeInTheDocument();
    expect(screen.getByText("链接已保存")).toBeInTheDocument();
  });
});

describe("UnsearchableBadge", () => {
  it("marks a body that content search cannot find", () => {
    const { rerender } = render(<UnsearchableBadge />);
    expect(screen.getByText("正文不可检索")).toBeInTheDocument();

    rerender(<UnsearchableBadge detailed />);
    expect(screen.getByText("正文不可检索，可按标题 / 备注查找")).toBeInTheDocument();
  });
});
