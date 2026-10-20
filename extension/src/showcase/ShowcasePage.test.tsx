import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as BrowserCompanionModule from "../app/browserCompanion";

const extensionContext = vi.hoisted(() => ({ active: false }));

vi.mock("../app/browserCompanion", async (importOriginal) => ({
  ...(await importOriginal<typeof BrowserCompanionModule>()),
  isExtensionContext: () => extensionContext.active,
}));

import { ShowcasePage } from "./ShowcasePage";

const STORE_URL = "https://chromewebstore.google.com/detail/jeemllapbelpglmpnhjfamcnoibpeail";

function renderShowcase() {
  return render(
    <MemoryRouter>
      <ShowcasePage />
    </MemoryRouter>,
  );
}

describe("ShowcasePage", () => {
  beforeEach(() => {
    extensionContext.active = false;
  });

  it("shows the production ICP filing link", () => {
    render(
      <MemoryRouter>
        <ShowcasePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "粤ICP备2026101890号-1" })).toHaveAttribute(
      "href",
      "https://beian.miit.gov.cn/",
    );
    const githubLink = screen.getByRole("link", {
      name: "在 GitHub 查看 StashSeek 源码，欢迎提交 Issue 与 PR",
    });
    expect(githubLink).toHaveAttribute("href", "https://github.com/deequoique/stashseek");
    expect(githubLink).toHaveAttribute("target", "_blank");
    expect(githubLink).toHaveAttribute("rel", "noreferrer");
  });

  it("explains the project, audience, workflow, and honest preset-demo boundary", () => {
    const { container } = renderShowcase();

    expect(container.querySelector("[style]")).not.toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "让收藏过的内容，直接回答你。" })).toBeInTheDocument();
    expect(screen.getByText(/安装 Chrome 插件，在 YouTube 或哔哩哔哩视频旁直接提问/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /不要让遗忘成为收藏视频的终点/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/当我们按下收藏键的刹那/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "独立资料空间" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "基于原文生成回答" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "答案依据全程可追溯" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "为了让你跳过等待而设计：“我好像看过这个……我找找？”",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "从装上插件，到可追溯的回答。" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "安装插件并登录" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "边看视频边提问" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "一键保存到资料库" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "跨收藏检索与回源" })).toBeInTheDocument();
    expect(screen.getByText(/保存后的内容在后台建立索引/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "选一个场景，看答案出处。" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "答案与原文，一步之遥" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "下次需要答案，直接回到原文。" })).not.toBeInTheDocument();
    expect(container.querySelectorAll(".showcase-audience__icon")).toHaveLength(4);
    expect(screen.getByRole("heading", { name: "跨平台收集" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "多渠道使用者" })).not.toBeInTheDocument();
    expect(screen.getByText(/不会调用模型或上传数据/)).toBeInTheDocument();
    expect(screen.getByLabelText("从视频到可追溯答案的处理路径")).toBeInTheDocument();
    expect(screen.queryByText("来源可核对")).not.toBeInTheDocument();
    expect(container.querySelector(".instrument-status")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".showcase-brand__mark.brand-logo")).toHaveLength(2);
    expect(screen.getAllByRole("img")).toHaveLength(4);
    expect(screen.getByRole("img", { name: "How to Talk to Users 视频封面" })).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/MT4Ig2uqjTc/hqdefault.jpg",
    );
    expect(screen.getByRole("img", { name: "当前场景：How to Talk to Users 视频封面" })).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/MT4Ig2uqjTc/hqdefault.jpg",
    );
    expect(screen.getByRole("button", { name: "显示中文要点" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("demo-subtitle-ticker")).toHaveTextContent(/最近一次真实经历/);
    expect(container.querySelector(".demo-subtitle-bar > .sr-only")).toHaveTextContent(/片段要点.*最近一次真实经历/);
    expect(screen.getByLabelText("预设回答演示步骤")).toBeInTheDocument();
    expect(
      screen.queryByText("这组公开视频与时间点已经提前核对。点击下方按钮，查看系统如何从原文整理出答案。"),
    ).not.toBeInTheDocument();
    expect(document.body).toHaveTextContent("Chrome 插件 + 邮箱登录");
    expect(document.body).toHaveTextContent("字幕 → 私人资料库索引");
    expect(document.body).toHaveTextContent("页面内容、检索结果与回答依据都限定在当前账户范围内");
    expect(document.body).toHaveTextContent("可用入口以部署配置为准");
    expect(document.body).not.toHaveTextContent(/why_saved|tenant|Transcript|Chunks|Hybrid Search|EVIDENCE MODE|混合索引/i);
  });

  it("makes the Chrome Web Store listing the primary entry from the nav, hero, and closing call to action", () => {
    renderShowcase();

    const storeLinks = screen.getAllByRole("link", { name: /添加到 Chrome/ });
    expect(storeLinks).toHaveLength(3);
    for (const link of storeLinks) {
      expect(link).toHaveAttribute("href", STORE_URL);
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toContain("noreferrer");
    }
    expect(storeLinks[0]).toHaveClass("showcase-nav__install");
    expect(storeLinks[1]).toHaveClass("showcase-button--signal");
    expect(storeLinks[2]).toHaveClass("showcase-button--dark");

    expect(screen.getByRole("link", { name: "登录" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: /已经安装？登录私人资料库/ })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: /进入资料库/ })).not.toBeInTheDocument();
    expect(screen.getByText(/支持 Chrome 114 及以上版本/)).toBeInTheDocument();
  });

  it("keeps the offline package as a secondary download link", () => {
    renderShowcase();

    const offline = screen.getByRole("link", { name: "下载离线安装包" });
    expect(offline).toHaveAttribute(
      "href",
      "/assets/stashseek-chat-browser-companion-production-0.2.1.zip",
    );
    expect(offline).toHaveAttribute("download");
    expect(screen.queryByRole("link", { name: "下载浏览器插件" })).not.toBeInTheDocument();
  });

  it("falls back to entering the library inside the extension workspace", () => {
    extensionContext.active = true;
    renderShowcase();

    expect(screen.queryByRole("link", { name: /添加到 Chrome/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载离线安装包" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "登录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /已经安装/ })).not.toBeInTheDocument();
    const libraryLinks = screen.getAllByRole("link", { name: /进入(私人)?资料库/ });
    expect(libraryLinks).toHaveLength(3);
    for (const link of libraryLinks) expect(link).toHaveAttribute("href", "/login");
  });

  it("runs each source-backed preset only after the visitor asks for it", async () => {
    const user = userEvent.setup();
    const { container } = renderShowcase();

    expect(screen.queryByText(/不要先推销方案/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /查看这次回答/ }));

    expect(screen.getAllByText(/不要先推销方案/)).toHaveLength(2);
    const typedSegments = container.querySelectorAll(".demo-typewriter");
    expect(typedSegments.length).toBeGreaterThan(1);
    expect(typedSegments[0]).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector(".demo-answer__lead .sr-only")).toHaveTextContent(
      /不要先推销方案/,
    );
    expect(container.querySelector(".demo-answer__mark.brand-logo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /06:07/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=367s",
    );

    await user.click(screen.getByRole("button", { name: /AI 入门/ }));
    expect(screen.queryByText(/784 个输入到 10 个输出/)).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "当前场景：But what is a neural network? 视频封面" })).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/aircAruvnKk/hqdefault.jpg",
    );
    await user.click(screen.getByRole("button", { name: "显示英文要点" }));
    expect(screen.getByRole("button", { name: "显示英文要点" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("demo-subtitle-ticker")).toHaveTextContent(/28×28 image as 784 brightness inputs/);
    await user.click(screen.getByRole("button", { name: /查看这次回答/ }));

    expect(screen.getAllByText(/784 个输入到 10 个输出/)).toHaveLength(2);
    expect(screen.getByRole("link", { name: /03:08/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=aircAruvnKk&t=188s",
    );
  });

  it("previews a hero cover on hover and opens its matching demo on click", async () => {
    const user = userEvent.setup();
    renderShowcase();

    const aiCover = screen.getByRole("link", {
      name: "打开 But what is a neural network? 问答场景",
    });

    expect(aiCover).toHaveAttribute("href", "#demo");
    expect(screen.getByRole("img", { name: "当前场景：How to Talk to Users 视频封面" }))
      .toBeInTheDocument();

    await user.hover(aiCover);
    expect(aiCover).toHaveClass("is-front");
    expect(screen.getByRole("img", { name: "当前场景：How to Talk to Users 视频封面" }))
      .toBeInTheDocument();

    await user.click(aiCover);
    expect(screen.getByRole("img", { name: "当前场景：But what is a neural network? 视频封面" }))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AI 入门/ })).toHaveAttribute("aria-pressed", "true");
  });
});
