import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { Capabilities, ConversationHistoryPage, ConversationTurns, SessionInfo } from "../api/contracts";
import { conversationHistoryQueryOptions, conversationTurnsQueryOptions, preloadConversationHistory } from "./conversationQueries";

const capabilities: Capabilities = {
  supported_platforms: ["youtube"], browser_companion: true, web_login_channels: ["email"],
  external_login_providers: [],
  save_enabled: true, max_save_batch_size: 10,
  bilibili_parts_enabled: false,
  bilibili_favorites_enabled: false, transcript_pagination: true,
  archive: true, summary_generation: false, chat: true,
};
const session: SessionInfo = { authenticated: true, login_channel: "email", expires_at: "2027-01-01T00:00:00Z" };
const history: ConversationHistoryPage = {
  items: [
    { thread_id: "latest", conversation_id: "latest-id", title: "最近的问题", preview: "", updated_at: "2026-10-14T12:00:00Z" },
    { thread_id: "older", conversation_id: "older-id", title: "之前的问题", preview: "", updated_at: "2026-10-13T12:00:00Z" },
  ],
  next_cursor: "next-page",
};
const turns: ConversationTurns = { thread_id: "latest", conversation_id: "latest-id", turns: [] };

function authenticatedClient() {
  const client = new QueryClient();
  client.setQueryData(["session"], session);
  client.setQueryData(["capabilities"], capabilities);
  return client;
}

function json(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
}

describe("authenticated conversation preload", () => {
  it("requires a verified session and enabled chat before reading private history", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const client = new QueryClient();
    client.setQueryData(["capabilities"], capabilities);
    await preloadConversationHistory(client);
    client.setQueryData(["session"], session);
    client.setQueryData(["capabilities"], { ...capabilities, chat: false });
    await preloadConversationHistory(client);
    expect(fetch).not.toHaveBeenCalled();
    client.clear();
  });

  it("joins pending route reads and warms only the first page and latest transcript", async () => {
    let releaseHistory: (response: Response) => void = () => undefined;
    let releaseTurns: (response: Response) => void = () => undefined;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input) === "/api/v1/conversations?limit=30") {
        return new Promise((resolve) => { releaseHistory = resolve; });
      }
      if (String(input) === "/api/v1/conversations/latest/turns") {
        return new Promise((resolve) => { releaseTurns = resolve; });
      }
      throw new Error("Unexpected extra preload");
    });
    const client = authenticatedClient();
    const preload = preloadConversationHistory(client);
    const routeHistory = client.fetchQuery(conversationHistoryQueryOptions());
    expect(fetch).toHaveBeenCalledTimes(1);
    releaseHistory(json(history));
    await expect(routeHistory).resolves.toEqual(history);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    const routeTurns = client.fetchQuery(conversationTurnsQueryOptions("latest"));
    expect(fetch).toHaveBeenCalledTimes(2);
    releaseTurns(json(turns));
    await expect(routeTurns).resolves.toEqual(turns);
    await preload;

    await preloadConversationHistory(client);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(["conversation", "older"])).toBeUndefined();
    client.clear();
  });

  it("keeps empty history empty without creating a conversation", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ items: [], next_cursor: null }));
    const client = authenticatedClient();
    await preloadConversationHistory(client);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(["conversations"])).toEqual({ items: [], next_cursor: null });
    client.clear();
  });

  it("retains a failed warm request as an error rather than an empty history", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    const client = authenticatedClient();
    await preloadConversationHistory(client);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(client.getQueryState(["conversations"])?.status).toBe("error");
    expect(client.getQueryData(["conversations"])).toBeUndefined();
    expect(client.getQueryData(["conversation", "latest"])).toBeUndefined();
    client.clear();
  });

  it("cancels old-client warming and prevents a late history from starting a transcript", async () => {
    let releaseHistory: (response: Response) => void = () => undefined;
    let signal: AbortSignal | null | undefined;
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      signal = init?.signal;
      return new Promise((resolve) => { releaseHistory = resolve; });
    });
    const client = authenticatedClient();
    const preload = preloadConversationHistory(client);
    client.clear();
    expect(signal?.aborted).toBe(true);
    releaseHistory(json(history));
    await preload;
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(["conversations"])).toBeUndefined();
    expect(client.getQueryData(["conversation", "latest"])).toBeUndefined();
  });

  it("cancels an already-started transcript when the private session ends", async () => {
    let releaseTurns: (response: Response) => void = () => undefined;
    let signal: AbortSignal | null | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (String(input) === "/api/v1/conversations?limit=30") return Promise.resolve(json(history));
      signal = init?.signal;
      return new Promise((resolve) => { releaseTurns = resolve; });
    });
    const client = authenticatedClient();
    const preload = preloadConversationHistory(client);
    await vi.waitFor(() => expect(signal).toBeDefined());
    client.clear();
    expect(signal?.aborted).toBe(true);
    releaseTurns(json(turns));
    await preload;
    expect(client.getQueryData(["conversation", "latest"])).toBeUndefined();
  });

  it("refreshes expired in-memory history instead of retaining it indefinitely", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ items: [], next_cursor: null }));
    const client = authenticatedClient();
    client.setQueryData(["conversations"], history, { updatedAt: Date.now() - 61_000 });
    await preloadConversationHistory(client);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(["conversations"])).toEqual({ items: [], next_cursor: null });
    client.clear();
  });
});
