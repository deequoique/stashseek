import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LibraryEmptyState, LibraryErrorState, LibraryLoadingState } from "./LibraryStates";

describe("library page states", () => {
  it("shows agent guidance only for a true first empty library", () => {
    const { container, rerender } = render(<LibraryEmptyState trueFirstEmpty />);
    expect(screen.getByText("资料库还是空的")).toBeInTheDocument();
    expect(container.querySelector(".agent-mark.brand-logo")).toBeInTheDocument();
    expect(screen.queryByText(/我是你的资料整理助手/)).not.toBeInTheDocument();

    rerender(<LibraryEmptyState trueFirstEmpty={false} />);
    expect(screen.getByText(/没有符合当前条件的视频/)).toBeInTheDocument();
  });

  it("keeps loading and error distinct from empty", () => {
    const { rerender } = render(<LibraryLoadingState />);
    expect(screen.getByLabelText("正在加载资料库")).toBeInTheDocument();
    expect(screen.queryByText(/我是你的资料整理助手/)).not.toBeInTheDocument();

    rerender(<LibraryErrorState onRetry={() => undefined} />);
    expect(screen.getByRole("alert")).toHaveTextContent("暂时无法打开资料库");
    expect(screen.queryByText(/数据没有丢失/)).not.toBeInTheDocument();
  });

  it("uses detail-specific loading and failure copy when requested", () => {
    const { rerender } = render(<LibraryLoadingState label="正在加载视频详情" />);
    expect(screen.getByLabelText("正在加载视频详情")).toBeInTheDocument();

    rerender(
      <LibraryErrorState
        onRetry={() => undefined}
        title="暂时无法加载视频详情"
        description="请检查网络后重新加载。"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("暂时无法加载视频详情");
  });
});
