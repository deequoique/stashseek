import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { IngestStage } from "../api/contracts";
import { IngestProgress } from "./IngestProgress";

const stage = (key: string, status: IngestStage["status"], detail?: IngestStage["detail"]): IngestStage => ({ key, status, detail });
const steps = () => within(screen.getByRole("list", { name: "整理步骤" }));
const row = (text: string) => steps().getByText(text).closest("li") as HTMLElement;

describe("IngestProgress", () => {
  it("tells the user to stop waiting, with the saved link already done", () => {
    render(<IngestProgress stages={[stage("body", "running", "fetching")]} />);

    expect(screen.getByRole("heading", { name: "已经保存，不必等在这里" })).toBeInTheDocument();
    expect(screen.getByText("现在可以继续看视频，关闭插件也不影响处理。")).toBeInTheDocument();
    expect(row("保存视频链接")).toHaveClass("is-done");
    expect(row("保存视频链接")).toHaveTextContent("✓");
  });

  it("shows a queued body as not started and a fetching body as running", () => {
    const { rerender } = render(<IngestProgress stages={[stage("body", "pending", "queued")]} />);
    expect(row("排队中")).toHaveClass("is-pending");
    expect(row("排队中")).toHaveTextContent("○");

    rerender(<IngestProgress stages={[stage("body", "running", "fetching")]} />);
    expect(row("读取字幕与内容")).toHaveClass("is-running");
    expect(row("读取字幕与内容")).toHaveTextContent("◌");
  });

  it("says speech transcription is running on the body step", () => {
    render(<IngestProgress stages={[stage("body", "running", "transcribing")]} />);

    expect(row("语音转写中")).toHaveClass("is-running");
    expect(steps().queryByText("读取字幕与内容")).not.toBeInTheDocument();
  });

  it("says the index is being built while the body is chunked and embedded", () => {
    render(<IngestProgress stages={[stage("body", "running", "indexing")]} />);
    expect(row("建立检索索引")).toHaveClass("is-running");
  });

  it("names a finished body by its stage, whatever it last did", () => {
    render(<IngestProgress stages={[stage("body", "done", "transcribing")]} />);
    expect(row("读取字幕与内容")).toHaveClass("is-done");
  });

  it("announces a searchable body", () => {
    const { rerender } = render(<IngestProgress stages={[stage("body", "running", "indexing")]} />);
    expect(screen.queryByText("已整理，可检索")).not.toBeInTheDocument();

    rerender(<IngestProgress stages={[stage("body", "done")]} searchable />);
    expect(screen.getByRole("status")).toHaveTextContent("已整理，可检索");
  });

  it("lets a finished body stage announce itself when the caller does not say, and the caller overrule it", () => {
    const { rerender } = render(<IngestProgress stages={[stage("body", "done")]} />);
    expect(screen.getByRole("status")).toHaveTextContent("已整理，可检索");

    rerender(<IngestProgress stages={[stage("body", "done")]} searchable={false} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    rerender(<IngestProgress stages={[stage("body", "running", "indexing")]} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("lists the later stages the server sends, and nothing before they exist", () => {
    const { rerender } = render(<IngestProgress stages={[stage("body", "running", "fetching")]} />);
    expect(steps().getAllByRole("listitem")).toHaveLength(2);

    rerender(
      <IngestProgress
        stages={[stage("body", "done"), stage("classify", "running"), stage("summary", "pending")]}
        searchable
      />,
    );
    expect(steps().getAllByRole("listitem")).toHaveLength(4);
    expect(row("分类与主题")).toHaveClass("is-running");
    expect(row("内容要点")).toHaveClass("is-pending");
  });

  it("orders stages by the stage table whatever order they arrive in", () => {
    render(<IngestProgress stages={[stage("summary", "pending"), stage("body", "done"), stage("classify", "done")]} />);
    expect(steps().getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("保存视频链接"),
      expect.stringContaining("读取字幕与内容"),
      expect.stringContaining("分类与主题"),
      expect.stringContaining("内容要点"),
    ]);
  });

  it("does not draw skipped stages or stages it does not know", () => {
    render(
      <IngestProgress
        stages={[stage("body", "done"), stage("classify", "skipped"), stage("translation", "running"), stage("movie", "pending")]}
      />,
    );

    expect(steps().queryByText("分类与主题")).not.toBeInTheDocument();
    expect(steps().queryByText(/translation/)).not.toBeInTheDocument();
    expect(steps().getAllByRole("listitem")).toHaveLength(3);
    expect(row("电影识别")).toHaveClass("is-pending");
  });

  it("shows the placeholder of a later stage that failed, without turning the item unusable", () => {
    render(<IngestProgress stages={[stage("body", "done"), stage("classify", "failed"), stage("summary", "failed")]} searchable />);

    expect(row("分类与主题 · 未分类")).toHaveClass("is-failed");
    expect(row("内容要点 · 暂无内容要点")).toHaveClass("is-failed");
    expect(screen.getByRole("status")).toHaveTextContent("已整理，可检索");
  });

  it("puts the note under the running step, or the first waiting step when nothing runs", () => {
    const { rerender } = render(<IngestProgress stages={[stage("body", "done"), stage("classify", "running"), stage("summary", "pending")]} note="已用时 5 秒" />);
    expect(row("分类与主题")).toHaveTextContent("已用时 5 秒");
    expect(row("内容要点")).not.toHaveTextContent("已用时");

    rerender(<IngestProgress stages={[stage("body", "pending", "queued")]} note="已用时 5 秒" />);
    expect(row("排队中")).toHaveTextContent("已用时 5 秒");
  });

  it("describes each step to a screen reader as well as by its glyph", () => {
    render(<IngestProgress stages={[stage("body", "running", "fetching"), stage("classify", "pending")]} />);

    expect(row("读取字幕与内容")).toHaveTextContent("进行中");
    expect(row("分类与主题")).toHaveTextContent("未开始");
    expect(row("保存视频链接")).toHaveTextContent("已完成");
    expect(row("保存视频链接").querySelector("[aria-hidden='true']")).toHaveTextContent("✓");
  });

  it("does not throw for a status it does not know", () => {
    const future: IngestStage = JSON.parse('{"key":"body","status":"paused"}');
    render(<IngestProgress stages={[future]} />);
    expect(row("读取字幕与内容")).toHaveClass("is-pending");
  });
});
