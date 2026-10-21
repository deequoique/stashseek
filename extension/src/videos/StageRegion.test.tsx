import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { IngestStage } from "../api/contracts";
import { StageRegion } from "./StageRegion";

const stage = (key: string, status: IngestStage["status"]): IngestStage => ({ key, status });

describe("StageRegion", () => {
  it("shows the placeholder of a stage that failed", () => {
    const { rerender } = render(<StageRegion stage={stage("classify", "failed")} />);
    expect(screen.getByRole("heading", { name: "分类与主题" })).toBeInTheDocument();
    expect(screen.getByText("未分类")).toBeInTheDocument();

    rerender(<StageRegion stage={stage("summary", "failed")} />);
    expect(screen.getByRole("heading", { name: "内容要点" })).toBeInTheDocument();
    expect(screen.getByText("暂无内容要点")).toBeInTheDocument();

    rerender(<StageRegion stage={stage("movie", "failed")} />);
    expect(screen.getByText("未识别")).toBeInTheDocument();
  });

  it("says a stage that has not finished is in progress", () => {
    const { rerender } = render(<StageRegion stage={stage("classify", "pending")} />);
    expect(screen.getByText("整理中…")).toBeInTheDocument();
    expect(screen.queryByText("未分类")).not.toBeInTheDocument();

    rerender(<StageRegion stage={stage("classify", "running")} />);
    expect(screen.getByText("整理中…")).toBeInTheDocument();
  });

  it("shows what the owner of a finished stage provides", () => {
    render(<StageRegion stage={stage("summary", "done")}><p>第一条要点</p></StageRegion>);
    expect(screen.getByText("第一条要点")).toBeInTheDocument();
    expect(screen.queryByText("暂无内容要点")).not.toBeInTheDocument();
  });

  it("falls back to the placeholder when a finished stage has nothing to show", () => {
    render(<StageRegion stage={stage("classify", "done")} />);
    expect(screen.getByText("未分类")).toBeInTheDocument();
  });

  it("draws nothing for the body or a stage it does not know", () => {
    const { container, rerender } = render(<StageRegion stage={stage("body", "done")} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<StageRegion stage={stage("translation", "running")} />);
    expect(container).toBeEmptyDOMElement();
  });
});
