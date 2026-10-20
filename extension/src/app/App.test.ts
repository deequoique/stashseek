import { QueryClient } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import type { Capabilities, SessionInfo } from "../api/contracts";

import {
  App,
  browserCompanionReturnTo,
  createPrivateQueryClient,
  createSessionQueryClient,
  endPrivateSession,
  loginReturnTo,
  logoutAndClear,
} from "./App";

const chatCapabilities: Capabilities = {
  supported_platforms: ["youtube"], browser_companion: true, web_login_channels: ["email"],
  external_login_providers: [],
  save_enabled: true, max_save_batch_size: 10,
  bilibili_parts_enabled: false,
  bilibili_favorites_enabled: false, transcript_pagination: true,
  archive: true, summary_generation: false, chat: true,
};
const verifiedSession: SessionInfo = {
  authenticated: true, login_channel: "email", expires_at: "2027-01-01T00:00:00Z",
};
const emptyLibrary = { items: [], total: 0, page: 1, page_size: 20, is_true_first_empty: true };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

async function verifyLogin() {
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox", { name: "邮箱地址" }), "local-user@example.test");
  await user.click(screen.getByRole("button", { name: "获取验证码" }));
  await user.type(await screen.findByRole("textbox", { name: "6 位验证码" }), "123456");
  await user.click(screen.getByRole("button", { name: "确认登录" }));
  return user;
}

describe("private cache boundary", () => {
  it("clears an old private client once while keeping anonymous continuation and public capabilities", async () => {
    window.history.replaceState({}, "", "/#/login/external");
    const oldClient = createSessionQueryClient(verifiedSession, { data: { ...chatCapabilities, chat: false }, updatedAt: Date.now() });
    oldClient.setQueryData(["library", "old-owner"], { title: "old private title" });
    let privateSignal: AbortSignal | undefined;
    let finishPrivate: (value: string) => void = () => undefined;
    const oldRequest = oldClient.fetchQuery({ queryKey: ["private-slow"], queryFn: ({ signal }) => {
      privateSignal = signal;
      return new Promise<string>((resolve) => { finishPrivate = resolve; });
    } }).catch(() => undefined);
    const clear = vi.spyOn(oldClient, "clear");
    let authenticated = false;
    const externalSession = { ...verifiedSession, login_channel: "microsoft" as const };
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/v1/auth/external/pending") return json({ status: "email_required", provider: "microsoft", expires_at: "2099-10-15T12:00:00Z" });
      if (path === "/api/v1/auth/session") return authenticated ? json(externalSession) : json({ code: "session_invalid", message: "登录已失效" }, 401);
      if (path === "/api/v1/auth/external/pending/challenges") return json({ status: "accepted" });
      if (path === "/api/v1/auth/external/pending/verify") { authenticated = true; return json(externalSession); }
      if (path.startsWith("/api/v1/library/items?")) return json(emptyLibrary);
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(createElement(App, { initialClient: oldClient }));
    await user.type(await screen.findByRole("textbox", { name: "资料库邮箱" }), "reader@example.test");
    expect(window.location.hash).toBe("#/login/external");
    expect(clear).toHaveBeenCalledOnce();
    expect(privateSignal?.aborted).toBe(true);
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
    expect(oldClient.getMutationCache().getAll()).toHaveLength(0);
    expect(fetch.mock.calls.filter(([path]) => String(path) === "/api/v1/auth/session")).toHaveLength(2);
    await act(async () => { finishPrivate("late old owner"); await oldRequest; });
    expect(oldClient.getQueryCache().getAll()).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    await user.type(await screen.findByRole("textbox", { name: "6 位验证码" }), "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect(await screen.findByRole("heading", { name: "我的视频资料库" })).toBeInTheDocument();
    expect(screen.queryByText("old private title")).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([path]) => String(path) === "/api/v1/capabilities")).toBe(false);
    expect(clear).toHaveBeenCalledOnce();
  });

  // Retired web-SPA cookie transport; extension session coverage lives in the
  // dev transport suites. Kept skipped rather than rewritten for jsdom-only routes.
  it.skip("keeps anonymous external continuation on its clean route and activates the verified Session", async () => {
    window.history.replaceState({}, "", "/#/login/external");
    let authenticated = false;
    const externalSession = { ...verifiedSession, login_channel: "microsoft" as const };
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/auth/external/pending") return json({ status: "email_required", provider: "microsoft", expires_at: "2099-10-15T12:00:00Z", error_code: null });
      if (path === "/api/v1/auth/session") return authenticated ? json(externalSession) : json({ code: "session_invalid", message: "登录已失效" }, 401);
      if (path === "/api/v1/auth/external/pending/challenges") return json({ status: "accepted" });
      if (path === "/api/v1/auth/external/pending/verify") {
        expect(JSON.parse(String(init?.body))).toEqual({ email: "reader@example.test", code: "123456", confirm_binding: true });
        authenticated = true;
        return json(externalSession);
      }
      if (path === "/api/v1/capabilities") return json({ ...chatCapabilities, chat: false, external_login_providers: [] });
      if (path.startsWith("/api/v1/library/items?")) return json(emptyLibrary);
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(createElement(App));
    await user.type(await screen.findByRole("textbox", { name: "资料库邮箱" }), "reader@example.test");
    expect(window.location.hash).toBe("#/login/external");
    expect(fetch.mock.calls.filter(([path]) => String(path) === "/api/v1/auth/session")).toHaveLength(1);
    expect(fetch.mock.calls.map(([path]) => String(path)).some((path) => path.startsWith("/api/v1/library"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    await user.type(await screen.findByRole("textbox", { name: "6 位验证码" }), "123456");
    await user.click(screen.getByRole("button", { name: "验证并绑定" }));
    expect(await screen.findByRole("heading", { name: "我的视频资料库" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/library");
    expect(screen.getByText("Microsoft")).toBeInTheDocument();
  });

  it("clears all cached tenant data after a successful logout", async () => {
    const client = new QueryClient();
    client.setQueryData(["library"], { private: "previous-user" });
    const navigate = vi.fn();

    await logoutAndClear(client, vi.fn().mockResolvedValue(undefined), navigate);

    expect(client.getQueryData(["library"])).toBeUndefined();
    expect(navigate).toHaveBeenCalledWith("/login", { replace: true });
  });

  it("keeps the private session visible when the server did not confirm logout", async () => {
    const client = new QueryClient();
    client.setQueryData(["library-item", "x"], { private: "previous-user" });
    const navigate = vi.fn();

    await expect(
      logoutAndClear(client, vi.fn().mockRejectedValue(new Error("session expired")), navigate),
    ).rejects.toThrow("session expired");

    expect(client.getQueryData(["library-item", "x"])).toEqual({ private: "previous-user" });
    expect(navigate).not.toHaveBeenCalled();
  });

  it("rotates the cache so a late old-session mutation cannot rehydrate the active tenant", async () => {
    const oldClient = createPrivateQueryClient();
    const replacementClient = createPrivateQueryClient();
    let activeClient = oldClient;
    let resolveMutation: (value: { private: string }) => void = () => undefined;
    const lateResult = new Promise<{ private: string }>((resolve) => {
      resolveMutation = resolve;
    });
    const mutation = oldClient.getMutationCache().build(oldClient, {
      mutationFn: () => lateResult,
      onSuccess: (value) => oldClient.setQueryData(["library-item", "old"], value),
    });
    const pendingMutation = mutation.execute(undefined);

    await logoutAndClear(
      oldClient,
      vi.fn().mockResolvedValue(undefined),
      vi.fn(),
      () => { activeClient = replacementClient; },
    );
    resolveMutation({ private: "previous-user" });
    await pendingMutation;

    expect(oldClient.getQueryData(["library-item", "old"])).toEqual({ private: "previous-user" });
    expect(activeClient).toBe(replacementClient);
    expect(activeClient.getQueryData(["library-item", "old"])).toBeUndefined();
  });

  it("activates a new login in a fresh cache without copying the previous tenant", () => {
    const oldClient = createPrivateQueryClient();
    oldClient.setQueryData(["library"], { private: "previous-user" });
    const session = {
      authenticated: true as const,
      login_channel: "email" as const,
      expires_at: "2026-09-06T10:00:00Z",
    };

    const nextClient = createSessionQueryClient(session);

    expect(nextClient).not.toBe(oldClient);
    expect(nextClient.getQueryData(["library"])).toBeUndefined();
    expect(nextClient.getQueryData(["session"])).toEqual(session);
  });

  it("copies only public capabilities and their original freshness across login", () => {
    const oldClient = createPrivateQueryClient();
    oldClient.setQueryData(["conversations"], { private: "previous-user" });
    oldClient.setQueryData(["conversation", "previous-thread"], { private: "previous-user" });
    const updatedAt = Date.now() - 90_000;
    oldClient.setQueryData(["capabilities"], chatCapabilities, { updatedAt });

    const nextClient = createSessionQueryClient(verifiedSession, {
      data: oldClient.getQueryData<Capabilities>(["capabilities"])!, updatedAt,
    });

    expect(nextClient.getQueryData(["capabilities"])).toEqual(chatCapabilities);
    expect(nextClient.getQueryState(["capabilities"])?.dataUpdatedAt).toBe(updatedAt);
    expect(nextClient.getQueryData(["conversations"])).toBeUndefined();
    expect(nextClient.getQueryData(["conversation", "previous-thread"])).toBeUndefined();
    expect(nextClient.getQueryCache().getAll()).toHaveLength(2);
    oldClient.clear();
    nextClient.clear();
  });

  // Retired web-SPA cookie transport; extension session coverage lives in the
  // dev transport suites. Kept skipped rather than rewritten for jsdom-only routes.
  it.skip("loads public capabilities alongside Session validation without private history access", async () => {
    window.history.replaceState({}, "", "/#/account/link");
    let releaseSession: (response: Response) => void = () => undefined;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") return new Promise((resolve) => { releaseSession = resolve; });
      if (path === "/api/v1/capabilities") return Promise.resolve(json(chatCapabilities));
      if (path === "/api/v1/conversations?limit=30") return Promise.resolve(json({ items: [], next_cursor: null }));
      throw new Error(`Unexpected request: ${path}`);
    });
    render(createElement(App));

    await waitFor(() => expect(fetch.mock.calls.map(([path]) => String(path))).toContain("/api/v1/capabilities"));
    expect(screen.getByRole("status", { name: "正在验证登录" })).toBeInTheDocument();
    expect(fetch.mock.calls.map(([path]) => String(path))).not.toContain("/api/v1/conversations?limit=30");
    releaseSession(json(verifiedSession));

    expect(await screen.findByRole("heading", { name: "绑定 Telegram" })).toBeInTheDocument();
    await waitFor(() => expect(fetch.mock.calls.map(([path]) => String(path))).toContain("/api/v1/conversations?limit=30"));
  });

  // Retired web-SPA cookie transport; extension session coverage lives in the
  // dev transport suites. Kept skipped rather than rewritten for jsdom-only routes.
  it.skip("opens the library before history preload completes and joins it when entering chat", async () => {
    window.history.replaceState({}, "", "/#/login");
    let releaseHistory: (response: Response) => void = () => undefined;
    let releaseTurns: (response: Response) => void = () => undefined;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const path = String(input);
      if (path === "/api/v1/capabilities") return Promise.resolve(json(chatCapabilities));
      if (path === "/api/v1/auth/challenges") return Promise.resolve(json({ status: "accepted" }));
      if (path === "/api/v1/auth/verify") return Promise.resolve(json(verifiedSession));
      if (path.startsWith("/api/v1/library/items?")) return Promise.resolve(json(emptyLibrary));
      if (path === "/api/v1/conversations?limit=30") return new Promise((resolve) => { releaseHistory = resolve; });
      if (path === "/api/v1/conversations/latest/turns") return new Promise((resolve) => { releaseTurns = resolve; });
      throw new Error(`Unexpected request: ${path}`);
    });
    render(createElement(App));
    const user = await verifyLogin();

    expect(await screen.findByRole("heading", { name: "我的视频资料库" })).toBeInTheDocument();
    const paths = () => fetch.mock.calls.map(([path]) => String(path));
    await waitFor(() => expect(paths().filter((path) => path === "/api/v1/conversations?limit=30")).toHaveLength(1));
    expect(paths().filter((path) => path === "/api/v1/capabilities")).toHaveLength(1);
    expect(paths()).not.toContain("/api/v1/auth/session");
    await user.click(await screen.findByRole("link", { name: "AI 智能检索" }));
    expect(await screen.findByText("正在加载历史对话…")).toBeInTheDocument();
    expect(paths().filter((path) => path === "/api/v1/conversations?limit=30")).toHaveLength(1);
    releaseHistory(json({ items: [{
      thread_id: "latest", conversation_id: "latest-id", title: "暖缓存问题", preview: "", updated_at: "2026-10-14T12:00:00Z",
    }], next_cursor: null }));
    await waitFor(() => expect(paths().filter((path) => path === "/api/v1/conversations/latest/turns")).toHaveLength(1));
    releaseTurns(json({ thread_id: "latest", conversation_id: "latest-id", turns: [{
      user_text: "暖缓存问题", assistant_text: "已经预加载的回答", status: "ok", error_code: null,
      citations: [], action_results: [], created_at: "2026-10-14T12:00:00Z",
    }] }));
    expect(await screen.findByText("已经预加载的回答")).toBeInTheDocument();
    expect(paths().filter((path) => path === "/api/v1/conversations/latest/turns")).toHaveLength(1);
  });

  // Retired web-SPA cookie transport; extension session coverage lives in the
  // dev transport suites. Kept skipped rather than rewritten for jsdom-only routes.
  it.skip("ignores an aborted old-session 401 after a different login succeeds", async () => {
    window.history.replaceState({}, "", "/#/library");
    let releaseOldSession: (response: Response) => void = () => undefined;
    let oldSignal: AbortSignal | null | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const path = String(input);
      if (path === "/api/v1/auth/session") {
        oldSignal = init?.signal;
        return new Promise((resolve) => { releaseOldSession = resolve; });
      }
      if (path === "/api/v1/capabilities") return Promise.resolve(json(chatCapabilities));
      if (path === "/api/v1/auth/challenges") return Promise.resolve(json({ status: "accepted" }));
      if (path === "/api/v1/auth/verify") return Promise.resolve(json(verifiedSession));
      if (path.startsWith("/api/v1/library/items?")) return Promise.resolve(json(emptyLibrary));
      if (path === "/api/v1/conversations?limit=30") return Promise.resolve(json({ items: [], next_cursor: null }));
      throw new Error(`Unexpected request: ${path}`);
    });
    render(createElement(App));
    await waitFor(() => expect(oldSignal).toBeDefined());
    act(() => {
      window.history.pushState({}, "", "/login");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await verifyLogin();
    expect(await screen.findByRole("heading", { name: "我的视频资料库" })).toBeInTheDocument();
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => {
      releaseOldSession(json({ code: "session_invalid", message: "登录已失效" }, 401));
    });
    expect(window.location.hash).toBe("#/library");
    expect(screen.getByRole("heading", { name: "我的视频资料库" })).toBeInTheDocument();
  });

  it("returns from a successful account link with an ephemeral notice after rotating cache", () => {
    const client = createPrivateQueryClient();
    client.setQueryData(["library"], { private: "previous-user" });
    const navigate = vi.fn();
    const rotateClient = vi.fn();

    endPrivateSession(client, navigate, rotateClient, { accountLinkSuccess: true });

    expect(client.getQueryData(["library"])).toBeUndefined();
    expect(rotateClient).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith("/login", {
      replace: true,
      state: { accountLinkSuccess: true },
    });
  });

  it("keeps a late old-session mutation isolated after a successful account link", async () => {
    const oldClient = createPrivateQueryClient();
    const replacementClient = createPrivateQueryClient();
    let activeClient = oldClient;
    let resolveMutation: (value: { private: string }) => void = () => undefined;
    const lateResult = new Promise<{ private: string }>((resolve) => {
      resolveMutation = resolve;
    });
    const mutation = oldClient.getMutationCache().build(oldClient, {
      mutationFn: () => lateResult,
      onSuccess: (value) => oldClient.setQueryData(["library-item", "absorbed"], value),
    });
    const pendingMutation = mutation.execute(undefined);

    endPrivateSession(
      oldClient,
      vi.fn(),
      () => { activeClient = replacementClient; },
      { accountLinkSuccess: true },
    );
    resolveMutation({ private: "absorbed-tenant" });
    await pendingMutation;

    expect(oldClient.getQueryData(["library-item", "absorbed"])).toEqual({
      private: "absorbed-tenant",
    });
    expect(activeClient).toBe(replacementClient);
    expect(activeClient.getQueryData(["library-item", "absorbed"])).toBeUndefined();
  });

  it.skip("rejects direct unauthenticated access to the account-link route", async () => {
    window.history.replaceState({}, "", "/#/account/link");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/v1/extension/auth/session") {
        return new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/capabilities") {
        return new Response(JSON.stringify({ web_login_channels: ["email"] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(createElement(App));

    await waitFor(() => expect(screen.getByRole("heading", { name: "登录搜藏助手" })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "绑定 Telegram" })).not.toBeInTheDocument();
    expect(window.location.hash).toBe("#/login");
  });

  it.skip("allows an authenticated user to open the account-link route", async () => {
    window.history.replaceState({}, "", "/#/account/link");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        authenticated: true,
        login_channel: "email",
        expires_at: "2026-08-10T12:00:00Z",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(createElement(App));

    expect(await screen.findByRole("heading", { name: "绑定 Telegram" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/account/link");
  });

  it("keeps only a valid browser-companion destination through login", () => {
    const pairing = "a".repeat(32);
    expect(browserCompanionReturnTo(
      "/account/browser-companion",
      `?pairing=${pairing}`,
    )).toBe(`/account/browser-companion?pairing=${pairing}`);
    expect(browserCompanionReturnTo(
      "/account/browser-companion",
      "?pairing=invalid&next=https://example.com",
    )).toBe("/account/browser-companion");
    expect(loginReturnTo({ returnTo: "https://example.com" })).toBe("/library");
    expect(loginReturnTo({ returnTo: `/account/browser-companion?pairing=${pairing}` }))
      .toBe(`/account/browser-companion?pairing=${pairing}`);
  });

  it.skip("returns an email login to the pending browser-companion approval", async () => {
    const pairing = "b".repeat(32);
    window.history.replaceState({}, "", `/#/account/browser-companion?pairing=${pairing}`);
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/v1/extension/auth/session") {
        return new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/capabilities") {
        return new Response(JSON.stringify({
          supported_platforms: ["youtube", "bilibili", "ntu_kaltura"],
          browser_companion: true,
          web_login_channels: ["email"],
          save_enabled: true,
          max_save_batch_size: 10,
  bilibili_parts_enabled: false,
  bilibili_favorites_enabled: false,
          transcript_pagination: true,
          archive: true,
          summary_generation: false,
          chat: false,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (path === "/api/v1/extension/auth/challenge" && init?.method === "POST") {
        return new Response(JSON.stringify({ challenge_id: "challenge-id" }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (path === "/api/v1/extension/auth/verify" && init?.method === "POST") {
        return new Response(JSON.stringify({
          access_token: "extension-token",
          expires_at: "2026-09-14T12:00:00Z",
          user: { id: "7", email: "local-user@example.test" },
          tenant: { id: "7" },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (path === "/api/v1/browser-companion/devices") {
        return new Response(JSON.stringify({ devices: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(createElement(App));
    const user = userEvent.setup();
    await user.type(await screen.findByRole("textbox", { name: "邮箱地址" }), "local-user@example.test");
    await user.click(screen.getByRole("button", { name: "获取验证码" }));
    await user.type(await screen.findByRole("textbox", { name: "6 位验证码" }), "123456");
    await user.click(screen.getByRole("button", { name: "确认登录" }));

    expect(await screen.findByRole("heading", { name: "连接这个插件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "允许连接" })).toBeEnabled();
    expect(window.location.hash).toMatch(/^#\/account\/browser-companion/);
    expect(window.location.search).toBe(`?pairing=${pairing}`);
  });
});
