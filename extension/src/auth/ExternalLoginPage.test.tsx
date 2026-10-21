import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { ExternalLoginPending, SessionInfo } from "../api/contracts";
import { ExternalLoginPage } from "./ExternalLoginPage";

const emailRequired: ExternalLoginPending = {
  status: "email_required", provider: "microsoft", expires_at: "2099-10-15T12:00:00Z", error_code: null,
};
const session: SessionInfo = { authenticated: true, login_channel: "microsoft", expires_at: "2099-10-16T12:00:00Z" };

function renderPage(props: Partial<Parameters<typeof ExternalLoginPage>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const defaults = {
    loadPending: vi.fn().mockResolvedValue(emailRequired), probeSession: vi.fn().mockResolvedValue(null),
    requestCode: vi.fn().mockResolvedValue({ status: "accepted" }), verify: vi.fn().mockResolvedValue(session),
    cancel: vi.fn().mockResolvedValue(undefined), signOut: vi.fn().mockResolvedValue(undefined), onAuthenticated: vi.fn(),
  };
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/login/external"]}>
    <Routes><Route path="/login/external" element={<ExternalLoginPage {...defaults} {...props} />} /><Route path="/login" element={<p>选择登录方式</p>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  return { ...view, client, ...defaults };
}

async function enterCode(user: ReturnType<typeof userEvent.setup>, email = "reader@example.test") {
  await user.type(await screen.findByRole("textbox", { name: "资料库邮箱" }), email);
  await user.click(screen.getByRole("button", { name: "获取验证码" }));
  return screen.findByRole("textbox", { name: "6 位验证码" });
}

beforeEach(() => { window.localStorage.clear(); window.sessionStorage.clear(); });

describe("external login continuation", () => {
  it("identifies the mailbox and binds with one explicit code submit", async () => {
    const user = userEvent.setup();
    const verify = vi.fn().mockResolvedValue(session);
    const onAuthenticated = vi.fn();
    const { requestCode } = renderPage({ verify, onAuthenticated });
    const code = await enterCode(user);
    expect(requestCode).toHaveBeenCalledWith("reader@example.test", expect.any(AbortSignal));
    expect(screen.getByText("reader@example.test")).toBeInTheDocument();
    expect(screen.getByText(/提交即验证并绑定/)).toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();
    await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect(verify).toHaveBeenCalledWith({ email: "reader@example.test", code: "123456", confirm_binding: true }, expect.any(AbortSignal));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(session));
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
    expect(window.location.search).toBe("");
  });

  it("keeps wrong codes local and permits a deliberate retry without another confirmation", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    const verify = vi.fn().mockRejectedValueOnce(new ApiError(401, "verification_failed", "private provider detail")).mockResolvedValue(session);
    renderPage({ verify, onAuthenticated });
    const code = await enterCode(user);
    await user.type(code, "12");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect(verify).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("请输入 6 位数字验证码");
    await user.clear(code); await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("验证码无效或已过期");
    expect(screen.queryByText("private provider detail")).not.toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce());
    expect(verify).toHaveBeenCalledTimes(2);
  });

  it("blocks duplicate verification and discards sensitive mutation variables on unmount", async () => {
    const user = userEvent.setup();
    let finish: (value: SessionInfo) => void = () => undefined;
    const verify = vi.fn().mockImplementation(() => new Promise<SessionInfo>((resolve) => { finish = resolve; }));
    const onAuthenticated = vi.fn();
    const view = renderPage({ verify, onAuthenticated });
    await user.type(await enterCode(user), "123456");
    const submit = screen.getByRole("button", { name: "验证并绑定" });
    await user.click(submit); await user.click(submit);
    expect(verify).toHaveBeenCalledOnce();
    const signal = verify.mock.calls[0]![1] as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { finish(session); });
    expect(onAuthenticated).not.toHaveBeenCalled();
    await waitFor(() => expect(view.client.getMutationCache().getAll()).toHaveLength(0));
  });

  it("reconciles a lost successful response before activating the authoritative session", async () => {
    const user = userEvent.setup();
    const loadPending = vi.fn().mockResolvedValueOnce(emailRequired).mockResolvedValue({ ...emailRequired, status: "completed" });
    const probeSession = vi.fn().mockResolvedValueOnce(null).mockResolvedValue(session);
    const verify = vi.fn().mockRejectedValue(new TypeError("network lost after commit"));
    const onAuthenticated = vi.fn();
    renderPage({ loadPending, probeSession, verify, onAuthenticated });
    await user.type(await enterCode(user), "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(session));
    expect(verify).toHaveBeenCalledOnce();
    expect(loadPending).toHaveBeenCalledTimes(2);
    expect(probeSession).toHaveBeenCalledTimes(2);
  });

  it("does not resubmit consumed proof when binding completed without a delivered session", async () => {
    const user = userEvent.setup();
    const verify = vi.fn().mockRejectedValue(new TypeError("response lost"));
    const onAuthenticated = vi.fn();
    renderPage({ verify, onAuthenticated, loadPending: vi.fn().mockResolvedValueOnce(emailRequired).mockResolvedValue({ ...emailRequired, status: "completed" }) });
    await user.type(await enterCode(user), "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect((await screen.findAllByText("绑定已完成，请重新选择该登录方式进入资料库。")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "验证并绑定" })).not.toBeInTheDocument();
    expect(verify).toHaveBeenCalledOnce(); expect(onAuthenticated).not.toHaveBeenCalled();
  });

  it("allows only explicit retry after an uncertain uncommitted result", async () => {
    const user = userEvent.setup();
    const verify = vi.fn().mockRejectedValue(new TypeError("response lost"));
    const onAuthenticated = vi.fn();
    renderPage({ verify, onAuthenticated });
    const code = await enterCode(user); await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("绑定尚未完成");
    expect(await screen.findByRole("textbox", { name: "6 位验证码" })).toHaveValue("");
    expect(verify).toHaveBeenCalledOnce(); expect(onAuthenticated).not.toHaveBeenCalled();
  });

  it("does not activate an unrelated session introduced in another tab", async () => {
    const user = userEvent.setup();
    const otherSession = { ...session, login_channel: "qq" as const };
    const onAuthenticated = vi.fn();
    renderPage({ onAuthenticated, probeSession: vi.fn().mockResolvedValue(otherSession), loadPending: vi.fn().mockResolvedValue({ ...emailRequired, status: "failed", error_code: "session_changed" }) });
    expect(await screen.findByRole("button", { name: "继续当前资料库" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "资料库邮箱" })).not.toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "继续当前资料库" }));
    expect(onAuthenticated).toHaveBeenCalledWith(otherSession);
  });

  it("checks the flow after the Session probe when another account uses the same provider", async () => {
    const onAuthenticated = vi.fn();
    const otherSession = { ...session, expires_at: "2099-10-17T12:00:00Z" };
    let cookieChangedDuringProbe = false;
    const probeSession = vi.fn(async () => {
      // Another tab's Cookie becomes visible as this Session read completes.
      cookieChangedDuringProbe = true;
      return otherSession;
    });
    const loadPending = vi.fn(async (): Promise<ExternalLoginPending> => ({
      ...emailRequired,
      status: cookieChangedDuringProbe ? "failed" : "completed",
      error_code: cookieChangedDuringProbe ? "session_changed" : null,
    }));
    renderPage({ onAuthenticated, probeSession, loadPending });
    expect(await screen.findByRole("button", { name: "继续当前资料库" })).toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("此浏览器的登录已改变");
  });

  it("requires successful cancellation and logout before switching accounts", async () => {
    const user = userEvent.setup();
    const onSignedOut = vi.fn();
    const signOut = vi.fn().mockRejectedValue(new Error("logout unavailable"));
    const { cancel } = renderPage({ onSignedOut, signOut, probeSession: vi.fn().mockResolvedValue(session), loadPending: vi.fn().mockResolvedValue({ ...emailRequired, status: "failed", error_code: "session_present" }) });
    await user.click(await screen.findByRole("button", { name: "退出后重新开始" }));
    expect(cancel).toHaveBeenCalledOnce(); expect(signOut).toHaveBeenCalledOnce();
    expect(await screen.findByRole("alert")).toHaveTextContent("当前登录仍保留");
    expect(onSignedOut).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "继续当前资料库" })).toBeInTheDocument();
  });

  it("reloads into email entry and clears stale drafts when another tab changes provider", async () => {
    const user = userEvent.setup();
    const loadPending = vi.fn().mockResolvedValueOnce(emailRequired).mockResolvedValue({ ...emailRequired, provider: "qq" });
    renderPage({ loadPending });
    await user.type(await enterCode(user), "123");
    fireEvent.focus(window);
    const email = await screen.findByRole("textbox", { name: "资料库邮箱" });
    expect(email).toHaveValue("");
    expect(screen.queryByRole("textbox", { name: "6 位验证码" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "完成QQ登录" })).toBeInTheDocument();
  });

  it("clears stale drafts for a replacement flow using the same provider", async () => {
    const user = userEvent.setup();
    const loadPending = vi.fn().mockResolvedValueOnce(emailRequired).mockResolvedValue({ ...emailRequired, expires_at: "2099-10-15T12:01:00Z" });
    renderPage({ loadPending });
    await user.type(await enterCode(user), "123");
    fireEvent.focus(window);
    expect(await screen.findByRole("textbox", { name: "资料库邮箱" })).toHaveValue("");
    expect(screen.queryByRole("textbox", { name: "6 位验证码" })).not.toBeInTheDocument();
  });

  it("does not log out a newer page after a cancelled observer finishes late", async () => {
    const user = userEvent.setup();
    let finish: () => void = () => undefined;
    const cancel = vi.fn().mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    const signOut = vi.fn().mockResolvedValue(undefined);
    const view = renderPage({ cancel, signOut, probeSession: vi.fn().mockResolvedValue(session), loadPending: vi.fn().mockResolvedValue({ ...emailRequired, status: "failed", error_code: "session_present" }) });
    await user.click(await screen.findByRole("button", { name: "退出后重新开始" }));
    view.unmount();
    await act(async () => { finish(); });
    expect(signOut).not.toHaveBeenCalled();
  });

  it("retries a failed safe-status read without exposing raw errors", async () => {
    const user = userEvent.setup();
    renderPage({ loadPending: vi.fn().mockRejectedValueOnce(new Error("raw token detail")).mockResolvedValue(emailRequired) });
    expect(await screen.findByRole("alert")).toHaveTextContent("无法确认登录结果");
    expect(screen.queryByText("raw token detail")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新检查" }));
    expect(await screen.findByRole("textbox", { name: "资料库邮箱" })).toHaveValue("");
  });

  it("cancels server-side before returning to email login", async () => {
    const user = userEvent.setup();
    const { cancel } = renderPage();
    await user.click(await screen.findByRole("button", { name: "取消并返回登录" }));
    expect(cancel).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(await screen.findByText("选择登录方式")).toBeInTheDocument();
  });

  it("returns an expired or missing flow to login without retrying a consumed binding", async () => {
    const user = userEvent.setup();
    const { cancel, verify } = renderPage({ loadPending: vi.fn().mockResolvedValue({ status: "expired", provider: null, expires_at: null, error_code: "flow_invalid" }) });
    expect(await screen.findByRole("heading", { name: "完成所选账号登录" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消并返回登录" }));
    expect(await screen.findByText("选择登录方式")).toBeInTheDocument();
    expect(cancel).not.toHaveBeenCalled(); expect(verify).not.toHaveBeenCalled();
  });
});
