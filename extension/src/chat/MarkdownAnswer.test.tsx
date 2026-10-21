import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConversationCitation } from "../api/contracts";
import { MarkdownAnswer } from "./MarkdownAnswer";

const citations: ConversationCitation[] = [
  { title: "第一视频", excerpt: "第一段字幕", url: "https://example.test/a?t=11", start_sec: 11 },
  { title: "访谈方法", excerpt: "从开放问题开始。", url: "https://example.test/b?t=3225", start_sec: 3225 },
  { title: "标题视频", excerpt: "视频标题与简介正文", url: "https://example.test/c", start_sec: null },
];

describe("MarkdownAnswer", () => {
  it("renders the restrained Markdown structures semantically", () => {
    const { container } = render(
      <MarkdownAnswer>{[
        "## 结论",
        "",
        "第一段包含 **重点** 和 `open_at`。",
        "",
        "- 无序项",
        "1. 有序项",
        "",
        "> 保持问题聚焦。",
      ].join("\n")}</MarkdownAnswer>,
    );

    expect(screen.getByRole("heading", { level: 2, name: "结论" })).toBeInTheDocument();
    expect(screen.getByText("重点").tagName).toBe("STRONG");
    expect(screen.getByText("open_at").tagName).toBe("CODE");
    expect(screen.getByText("无序项").closest("ul")).not.toBeNull();
    expect(screen.getByText("有序项").closest("ol")).not.toBeNull();
    expect(screen.getByText("保持问题聚焦。").closest("blockquote")).not.toBeNull();
    expect(container.querySelector(".chat-markdown")).not.toBeNull();
  });

  it("keeps plain text readable and suppresses active model-authored content", () => {
    const { container } = render(
      <MarkdownAnswer>{[
        "历史纯文本回答。",
        "",
        "[external](https://example.test)",
        "![private image](https://example.test/image.png)",
        "<script>alert('unsafe')</script>",
      ].join("\n")}</MarkdownAnswer>,
    );

    expect(screen.getByText("历史纯文本回答。").tagName).toBe("P");
    expect(screen.getByText("external").tagName).toBe("SPAN");
    expect(screen.getByText("private image").tagName).toBe("SPAN");
    expect(container.querySelector("a, img, script")).toBeNull();
    expect(container).toHaveTextContent("<script>alert('unsafe')</script>");
  });

  it("renders a [^n] marker as a keyboard-reachable link to the n-th citation", () => {
    render(<MarkdownAnswer citations={citations}>{"先问开放问题[^2]，再写假设。"}</MarkdownAnswer>);

    const chip = screen.getByRole("link", { name: "引用 2：《访谈方法》 53:45" });
    expect(chip).toHaveAttribute("href", "https://example.test/b?t=3225");
    expect(chip).toHaveAttribute("target", "_blank");
    expect(chip).toHaveAttribute("rel", "noreferrer");
    // Copied text keeps the "[n]" form through zero-size brackets.
    expect(chip.textContent).toBe("[2]");
    const tooltip = screen.getByRole("tooltip");
    expect(chip).toHaveAttribute("aria-describedby", tooltip.id);
    expect(tooltip).toHaveTextContent("访谈方法 · 53:45");
    expect(tooltip).toHaveTextContent("从开放问题开始。");
    expect(tooltip.previousElementSibling).toBe(chip);
  });

  it("keeps collapsed chips in the text but out of the accessibility tree and tab order", () => {
    const { container } = render(
      <MarkdownAnswer citations={citations} citesHidden>{"先问开放问题[^2]，再写假设。"}</MarkdownAnswer>,
    );

    expect(screen.queryByRole("link")).toBeNull();
    const chip = container.querySelector("a.chat-cite") as HTMLElement;
    expect(chip).toHaveAttribute("aria-hidden", "true");
    expect(chip).toHaveAttribute("tabindex", "-1");
    expect(chip).toHaveAttribute("href", "https://example.test/b?t=3225");
    expect(chip).not.toHaveAttribute("aria-describedby");
    expect(chip.textContent).toBe("[2]");
    expect(container).toHaveTextContent("先问开放问题[2]");
  });

  it("truncates the tooltip excerpt to 120 characters", () => {
    const long = "字".repeat(130);
    render(
      <MarkdownAnswer citations={[{ ...citations[0]!, excerpt: long }]}>{"一句话[^1]"}</MarkdownAnswer>,
    );

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent(`${"字".repeat(120)}…`);
    expect(tooltip).not.toHaveTextContent("字".repeat(121));
  });

  it("labels a title-row citation without a time and keeps its bare link", () => {
    render(<MarkdownAnswer citations={citations}>{"标题命中[^3]"}</MarkdownAnswer>);

    const chip = screen.getByRole("link", { name: "引用 3：《标题视频》 标题与简介" });
    expect(chip).toHaveAttribute("href", "https://example.test/c");
    expect(screen.getByRole("tooltip")).toHaveTextContent("标题视频 · 标题与简介");
  });

  it("renders adjacent markers side by side in order", () => {
    const { container } = render(
      <MarkdownAnswer citations={citations}>{"结论成立[^1][^2][^3]。"}</MarkdownAnswer>,
    );

    const links = within(container).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "https://example.test/a?t=11",
      "https://example.test/b?t=3225",
      "https://example.test/c",
    ]);
    expect(container.querySelectorAll(".chat-cite-wrap")).toHaveLength(3);
  });

  it("leaves a marker outside the citation list as plain text", () => {
    const { container } = render(
      <MarkdownAnswer citations={citations}>{"模型自带的脚注[^9]和[^0]"}</MarkdownAnswer>,
    );

    expect(container).toHaveTextContent("模型自带的脚注[^9]和[^0]");
    expect(container.querySelector("a")).toBeNull();
  });

  it("leaves markers as text when no citations are supplied", () => {
    const { container } = render(<MarkdownAnswer>{"没有引用列表[^1]"}</MarkdownAnswer>);

    expect(container).toHaveTextContent("没有引用列表[^1]");
    expect(container.querySelector("a")).toBeNull();
  });

  it("does not rewrite markers inside code", () => {
    const { container } = render(
      <MarkdownAnswer citations={citations}>{[
        "行内 `[^1]` 代码",
        "",
        "```",
        "[^2]",
        "```",
      ].join("\n")}</MarkdownAnswer>,
    );

    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("code")).toHaveTextContent("[^1]");
    expect(container.querySelector("pre")).toHaveTextContent("[^2]");
  });

  it("keeps model-authored links inert even when they imitate a citation anchor", () => {
    const { container } = render(
      <MarkdownAnswer citations={citations}>{"[伪造](#cite-1) 和 [外链](https://example.test)"}</MarkdownAnswer>,
    );

    expect(screen.getByText("伪造").tagName).toBe("SPAN");
    expect(screen.getByText("外链").tagName).toBe("SPAN");
    expect(container.querySelector("a")).toBeNull();
  });
});
