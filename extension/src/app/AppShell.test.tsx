import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell, COMMUNITY_NOTICE_STORAGE_KEY } from "./AppShell";

describe("application shell", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps one quiet top bar and exposes an explicit logout", async () => {
    const onLogout = vi.fn();
    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter>
        <AppShell loginChannel="telegram" onLogout={onLogout}>
          <h1>我的资料库</h1>
        </AppShell>
      </MemoryRouter>,
    );

    expect(screen.getAllByText("StashSeek Chat")).toHaveLength(1);
    expect(container.querySelector(".wordmark .brand-logo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "跳到主要内容" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(screen.getByRole("heading", { name: "我的资料库" })).toBeInTheDocument();
    expect(screen.getByLabelText("查看用户交流群，QQ群 1107151131")).toBeInTheDocument();
    const githubLink = screen.getByRole("link", {
      name: "在 GitHub 查看 StashSeek 源码，欢迎提交 Issue 与 PR",
    });
    expect(githubLink).toHaveAttribute("href", "https://github.com/deequoique/stashseek");
    expect(githubLink).toHaveAttribute("target", "_blank");
    expect(githubLink).toHaveAttribute("rel", "noreferrer");
    await user.click(screen.getByLabelText("打开账户菜单，当前登录方式：Telegram"));
    expect(screen.queryByText("TG")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "绑定 Telegram" })).toHaveAttribute(
      "href",
      "/account/link",
    );
    expect(screen.getByRole("link", { name: "浏览器伴侣" })).toHaveAttribute(
      "href",
      "/account/browser-companion",
    );
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(onLogout).toHaveBeenCalledOnce();
  });

  it("shows first-level community details and persists announcement dismissal", async () => {
    const user = userEvent.setup();
    const view = render(
      <MemoryRouter>
        <AppShell loginChannel="email" onLogout={() => undefined}>
          <h1>我的资料库</h1>
        </AppShell>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("StashSeek 用户交流群公告")).toHaveTextContent(
      "非常高兴看到您在使用这款产品",
    );
    expect(screen.getByLabelText("StashSeek 用户交流群公告")).toHaveTextContent("1107151131");

    await user.click(screen.getByLabelText("查看用户交流群，QQ群 1107151131"));
    expect(screen.getByText("QQ群 1107151131")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭用户交流群公告" }));
    expect(screen.queryByLabelText("StashSeek 用户交流群公告")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(COMMUNITY_NOTICE_STORAGE_KEY)).toBe("true");

    view.unmount();
    render(
      <MemoryRouter>
        <AppShell loginChannel="email" onLogout={() => undefined}>
          <h1>我的资料库</h1>
        </AppShell>
      </MemoryRouter>,
    );
    expect(screen.queryByLabelText("StashSeek 用户交流群公告")).not.toBeInTheDocument();
    expect(screen.getByLabelText("查看用户交流群，QQ群 1107151131")).toBeInTheDocument();
  });

  it("shows and dismisses the announcement when browser storage is blocked", async () => {
    const storageRead = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage blocked", "SecurityError");
    });
    let storageWrite: ReturnType<typeof vi.spyOn> | undefined;
    try {
      render(
        <MemoryRouter>
          <AppShell loginChannel="wechat" onLogout={() => undefined}>
            <h1>我的资料库</h1>
          </AppShell>
        </MemoryRouter>,
      );
      expect(screen.getByLabelText("StashSeek 用户交流群公告")).toBeInTheDocument();

      storageRead.mockRestore();
      storageWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("Storage blocked", "SecurityError");
      });
      await userEvent.click(screen.getByRole("button", { name: "关闭用户交流群公告" }));
      expect(screen.queryByLabelText("StashSeek 用户交流群公告")).not.toBeInTheDocument();
    } finally {
      storageRead.mockRestore();
      storageWrite?.mockRestore();
    }
  });

  it("closes the account menu when the user clicks outside it", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <AppShell loginChannel="telegram" onLogout={() => undefined}>
          <button type="button">资料库操作</button>
        </AppShell>
      </MemoryRouter>,
    );

    const trigger = screen.getByLabelText("打开账户菜单，当前登录方式：Telegram");
    const menu = trigger.closest("details");
    await user.click(trigger);
    expect(menu).toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "资料库操作" }));
    expect(menu).not.toHaveAttribute("open");
  });

  it("keeps a failed logout visible without pretending the session ended", async () => {
    render(
      <MemoryRouter>
        <AppShell
          loginChannel="wechat"
          logoutError="退出失败，请检查网络后重试。"
          onLogout={() => undefined}
        >
          <h1>我的资料库</h1>
        </AppShell>
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByLabelText("打开账户菜单，当前登录方式：微信"));
    expect(screen.getByRole("alert")).toHaveTextContent("退出失败，请检查网络后重试。");
  });
});
