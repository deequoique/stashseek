import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { StepTimeline, type TimelineStep, useAutoCollapse } from "./StepTimeline";

const STEPS: TimelineStep[] = [
  { id: "a", text: "第一步", outcome: "completed" },
  { id: "b", text: "第二步", outcome: "running" },
  { id: "c", text: "第三步", outcome: "failed" },
  { id: "d", text: "第四步", outcome: "skipped" },
];

describe("StepTimeline", () => {
  it("renders one list item per step with its outcome class, and a named list", () => {
    render(<StepTimeline summary="概要" steps={STEPS} expanded onToggle={() => undefined} stepsLabel="自定义步骤" />);

    const list = screen.getByRole("list", { name: "自定义步骤" });
    expect(Array.from(list.querySelectorAll("li"), (item) => item.className)).toEqual(["is-completed", "is-running", "is-failed", "is-skipped"]);
    expect(screen.getByText("概要").closest("summary")).not.toBeNull();
    expect(screen.getByText("第二步").closest("li")?.querySelector("span[aria-hidden='true']")).not.toBeNull();
  });

  it("keeps the workspace markup unchanged when no step has a detail", () => {
    render(<StepTimeline summary="s" steps={STEPS} expanded onToggle={() => undefined} />);

    expect(screen.getByRole("list", { name: "Agent 执行步骤" })).toBeInTheDocument();
    expect(screen.getByText("第一步").parentElement?.tagName).toBe("LI");
    expect(screen.getByText("第一步").closest("details")).toHaveClass("chat-execution");
  });

  it("puts a step's detail under its text", () => {
    render(
      <StepTimeline
        summary="s"
        steps={[{ id: "a", text: "语音转写中", outcome: "running", detail: <small>已用时 5 秒</small> }]}
        expanded
        onToggle={() => undefined}
      />,
    );

    const item = screen.getByText("语音转写中").closest("li") as HTMLElement;
    expect(item).toHaveClass("is-running", "has-detail");
    expect(item).toHaveTextContent("语音转写中已用时 5 秒");
  });

  it("is open or closed as told and reports the user's toggle", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<StepTimeline summary="概要" steps={STEPS} expanded={false} onToggle={onToggle} />);
    const details = screen.getByText("概要").closest("details") as HTMLDetailsElement;
    expect(details).not.toHaveAttribute("open");
    expect(screen.getByText("展开")).toBeInTheDocument();

    rerender(<StepTimeline summary="概要" steps={STEPS} expanded onToggle={onToggle} />);
    expect(details).toHaveAttribute("open");
    expect(screen.getByText("收起")).toBeInTheDocument();

    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(onToggle).toHaveBeenLastCalledWith(false);
  });

  it("adds a class and renders extra content above the list", () => {
    render(
      <StepTimeline summary="s" steps={STEPS} expanded onToggle={() => undefined} className="sp-timeline">
        <p>计划</p>
      </StepTimeline>,
    );

    const details = screen.getByText("计划").closest("details") as HTMLElement;
    expect(details).toHaveClass("chat-execution", "sp-timeline");
    expect(details.querySelector(".chat-execution__body")?.firstElementChild).toBe(screen.getByText("计划"));
  });

  it("renders no list for an empty step set", () => {
    render(<StepTimeline summary="s" steps={[]} expanded onToggle={() => undefined} />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});

function Harness() {
  const [done, setDone] = useState(false);
  const [expanded, setExpanded] = useAutoCollapse(done);
  return (
    <>
      <button type="button" onClick={() => setDone((value) => !value)}>切换</button>
      <StepTimeline summary="概要" steps={STEPS} expanded={expanded} onToggle={setExpanded} />
    </>
  );
}

describe("useAutoCollapse", () => {
  it("is open, collapses once when the trigger turns on, lets the user reopen, and re-arms when it turns off", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const details = screen.getByText("概要").closest("details") as HTMLDetailsElement;
    expect(details).toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "切换" }));
    await waitFor(() => expect(details).not.toHaveAttribute("open"));

    details.open = true;
    fireEvent(details, new Event("toggle"));
    await waitFor(() => expect(details).toHaveAttribute("open"));

    await user.click(screen.getByRole("button", { name: "切换" }));
    await waitFor(() => expect(details).toHaveAttribute("open"));
    await user.click(screen.getByRole("button", { name: "切换" }));
    await waitFor(() => expect(details).not.toHaveAttribute("open"));
  });
});
