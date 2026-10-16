import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  ConversationStreamError,
  consumeLinkToken,
  createTelegramLinkToken,
  requestJson,
  setUnauthorizedHandler,
  streamConversationMessage,
  streamPageQuestion,
  StreamingUnavailableError,
  cancelExternalLogin,
  getExternalLoginPending,
  getSession,
  probeLoginSession,
  requestExternalLoginCode,
  verifyExternalLogin,
  previewBilibiliParts,
  submitBilibiliParts,
  previewBilibiliFavorites,
} from "./client";
import type { PageQaMessageInput } from "./contracts";

function streamRecord(event: Record<string, unknown>): string {
  return `event: ${String(event.type)}\ndata: ${JSON.stringify(event)}\n\n`;
}

describe("same-origin API client", () => {
  it("keeps part previews abortable and caller-owned stable import keys with Session CSRF", async () => {
    document.cookie = "__Host-kb_csrf=parts-csrf; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const signal = new AbortController().signal;
    await previewBilibiliParts({ url: "BV1xx411c7mD", page: 2 }, signal);
    const input = { selections: [{ part_ref: "signed-proof", allow_legacy_overlap: false }], why_saved: null };
    await submitBilibiliParts(input, "same-operation", signal);
    await submitBilibiliParts(input, "same-operation", signal);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/bilibili/parts:preview");
    for (const [path, init] of fetchMock.mock.calls.slice(1)) {
      expect(path).toBe("/api/v1/bilibili/parts:batch");
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("same-operation");
      expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("parts-csrf");
      expect(init?.signal).toBe(signal);
      expect(init?.credentials).toBe("same-origin");
      expect(JSON.parse(String(init?.body))).toEqual(input);
    }
    await previewBilibiliFavorites({ url: "https://space.bilibili.com/7/favlist?fid=8", page: 2 }, signal);
    const [favoritePath, favoriteInit] = fetchMock.mock.calls.at(-1)!;
    expect(favoritePath).toBe("/api/v1/bilibili/favorites:preview");
    expect(favoriteInit?.signal).toBe(signal);
    expect(new Headers(favoriteInit?.headers).get("X-CSRF-Token")).toBe("parts-csrf");
    expect(JSON.parse(String(favoriteInit?.body))).toEqual({ url: "https://space.bilibili.com/7/favlist?fid=8", page: 2 });
  });
  it("uses only the flow CSRF cookie for external mutations and keeps Session CSRF separate", async () => {
    document.cookie = "__Host-kb_csrf=session-csrf; Path=/; Secure";
    document.cookie = "__Host-kb_external_csrf=flow-csrf; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_path, init) =>
      init?.method === "DELETE" ? new Response(null, { status: 204 }) : new Response(JSON.stringify({ status: "accepted" }), { status: 200 }));
    await requestExternalLoginCode("reader@example.test");
    await verifyExternalLogin({ email: "reader@example.test", code: "123456", confirm_binding: true });
    await cancelExternalLogin();
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("flow-csrf");
      expect(init?.credentials).toBe("same-origin");
    }
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ email: "reader@example.test", code: "123456", confirm_binding: true });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/external/pending/challenges");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/external/pending/verify");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/auth/external/pending");
    await requestJson("/api/v1/protected", { method: "POST", body: "{}" });
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("X-CSRF-Token")).toBe("session-csrf");
    document.cookie = "__Host-kb_external_csrf=; Path=/; Secure; Max-Age=0";
    await requestExternalLoginCode("reader@example.test");
    expect(new Headers(fetchMock.mock.calls[4][1]?.headers).has("X-CSRF-Token")).toBe(false);
  });

  it("handles anonymous login probes locally while protected Session invalidation stays global", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), { status: 401 }));
    await expect(probeLoginSession()).resolves.toBeNull();
    expect(onUnauthorized).not.toHaveBeenCalled();
    await expect(getSession()).rejects.toMatchObject({ code: "session_invalid" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("reads pending state only through the Cookie and preserves cancelled-probe isolation", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ status: "expired", provider: null, expires_at: null }), { status: 200 }));
    await getExternalLoginPending();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/external/pending");
    expect(fetchMock.mock.calls[0][1]?.body).toBeUndefined();
    const controller = new AbortController(); controller.abort();
    await expect(probeLoginSession(controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("copies the readable CSRF cookie to unsafe requests", async () => {
    document.cookie = "__Host-kb_csrf=csrf-value; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await requestJson("/api/v1/example", { method: "POST", body: "{}" });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-value");
    expect(init.credentials).toBe("same-origin");
  });

  it("clears private client state through the unauthorized hook before surfacing 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(requestJson("/api/v1/library/items")).rejects.toEqual(
      expect.objectContaining({ status: 401, code: "session_invalid" }),
    );
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("keeps recoverable email verification failures inside the login form", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "verification_failed", message: "验证码无效或已过期" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(
      requestJson("/api/v1/extension/auth/verify", { method: "POST", body: "{}" }),
    ).rejects.toEqual(expect.objectContaining({ status: 401, code: "verification_failed" }));
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("ignores a cancelled old-session 401 after its response body arrives", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    const controller = new AbortController();
    let releaseBody: (value: { code: string; message: string }) => void = () => undefined;
    const response = new Response(null, { status: 401 });
    vi.spyOn(response, "json").mockImplementation(() => new Promise((resolve) => { releaseBody = resolve; }));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response);

    const result = requestJson("/api/v1/conversations?limit=30", { signal: controller.signal });
    const rejection = expect(result).rejects.toEqual(expect.objectContaining({ name: "AbortError" }));
    await vi.waitFor(() => expect(response.json).toHaveBeenCalledOnce());
    controller.abort();
    releaseBody({ code: "session_invalid", message: "登录已失效" });

    await rejection;
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not let an un-aborted old mutation invalidate a replacement Session owner", async () => {
    const oldOwner = vi.fn();
    const replacementOwner = vi.fn();
    setUnauthorizedHandler(oldOwner);
    let finish: (response: Response) => void = () => undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const request = requestJson("/api/v1/library/items/item:archive", { method: "POST" });
    const rejection = expect(request).rejects.toMatchObject({ code: "session_invalid" });
    // Cache rotation remounts the unauthorized boundary; mutations already
    // sent without AbortSignal can still finish after this replacement.
    setUnauthorizedHandler(replacementOwner);
    finish(new Response(JSON.stringify({ code: "session_invalid", message: "登录已失效" }), { status: 401 }));
    await rejection;
    expect(oldOwner).not.toHaveBeenCalled();
    expect(replacementOwner).not.toHaveBeenCalled();
  });

  it("creates a Telegram-targeted link token through the CSRF-protected API", async () => {
    document.cookie = "__Host-kb_csrf=csrf-link; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ token: "ephemeral-link-token" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(createTelegramLinkToken()).resolves.toEqual({ token: "ephemeral-link-token" });

    const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(path).toBe("/api/v1/link-tokens");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ target_channel: "telegram" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-link");
    expect(init.credentials).toBe("same-origin");
  });

  it("consumes a Telegram link token with the same unsafe-request contract", async () => {
    document.cookie = "__Host-kb_csrf=csrf-consume; Path=/; Secure";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ linked: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(consumeLinkToken("one-time-token")).resolves.toEqual({ linked: true });

    const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(path).toBe("/api/v1/link-tokens/consume");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ token: "one-time-token" });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-consume");
    expect(init.credentials).toBe("same-origin");
  });

  it("consumes ordered SSE events, copies CSRF, and returns the final projection", async () => {
    document.cookie = "__Host-kb_csrf=csrf-stream; Path=/; Secure";
    const response = {
      status: "ok",
      text: "最终答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response([
        streamRecord({ type: "started", request_id: "request-1", message_id: "message-1", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "activity", request_id: "request-1", message_id: "message-1", sequence: 2, activity: "retrieving" }),
        streamRecord({ type: "text_delta", request_id: "request-1", message_id: "message-1", sequence: 3, text: "最终答案" }),
        streamRecord({ type: "completed", request_id: "request-1", message_id: "message-1", sequence: 4, activity: "completed", response }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    const events: string[] = [];

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-1", text: "问题" },
      (event) => events.push(event.type),
    )).resolves.toEqual(response);
    expect(events).toEqual(["started", "activity", "text_delta", "completed"]);
    const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(path).toBe("/api/v1/conversations/conversation-1/messages/stream");
    expect(new Headers(init.headers).get("Accept")).toBe("text/event-stream");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-stream");
    expect(init.credentials).toBe("same-origin");
  });

  it("parses citation-first section lifecycle events and preserves section metadata", async () => {
    const final = {
      status: "ok",
      text: "第一段第二段",
      citations: [{ title: "来源视频", excerpt: "字幕依据", url: "https://example.test/video", start_sec: 11 }],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    const seen: Array<{ type: string; section_id?: string | null }> = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-section", message_id: "message-section", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "section_started", request_id: "request-section", message_id: "message-section", sequence: 2, section_id: "section-1", status: "grounded", citation_ids: [11], citations: final.citations }),
        streamRecord({ type: "text_delta", request_id: "request-section", message_id: "message-section", sequence: 3, section_id: "section-1", text: "第一段" }),
        streamRecord({ type: "text_delta", request_id: "request-section", message_id: "message-section", sequence: 4, section_id: "section-1", text: "第二段" }),
        streamRecord({ type: "section_completed", request_id: "request-section", message_id: "message-section", sequence: 5, section_id: "section-1", status: "grounded" }),
        streamRecord({ type: "completed", request_id: "request-section", message_id: "message-section", sequence: 6, response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-section", text: "问题" },
      (event) => seen.push({ type: event.type, section_id: event.section_id }),
    )).resolves.toEqual(final);
    expect(seen).toEqual([
      { type: "started", section_id: undefined },
      { type: "section_started", section_id: "section-1" },
      { type: "text_delta", section_id: "section-1" },
      { type: "text_delta", section_id: "section-1" },
      { type: "section_completed", section_id: "section-1" },
      { type: "completed", section_id: undefined },
    ]);
  });

  it("parses safe execution steps and validated plan snapshots", async () => {
    const final = {
      status: "ok",
      text: "最终答案",
      citations: [],
      action_results: [],
      thread_id: "thread-steps",
      error_code: null,
    };
    const events: Array<{ type: string; step?: string | null }> = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-steps", message_id: "message-steps", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "step_started", request_id: "request-steps", message_id: "message-steps", sequence: 2, step_id: "step-1", step_code: "searching_library" }),
        streamRecord({ type: "step_completed", request_id: "request-steps", message_id: "message-steps", sequence: 3, step_id: "step-1", step_code: "searching_library", step_outcome: "completed", result_count: 5 }),
        streamRecord({ type: "plan_updated", request_id: "request-steps", message_id: "message-steps", sequence: 4, plan: [{ id: "answer", title: "整理回答", status: "in_progress" }] }),
        streamRecord({ type: "completed", request_id: "request-steps", message_id: "message-steps", sequence: 5, response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-steps", text: "问题" },
      (event) => events.push({ type: event.type, step: event.step_id }),
    )).resolves.toEqual(final);
    expect(events).toEqual([
      { type: "started", step: undefined },
      { type: "step_started", step: "step-1" },
      { type: "step_completed", step: "step-1" },
      { type: "plan_updated", step: undefined },
      { type: "completed", step: undefined },
    ]);
  });

  it("rejects step results that do not match the open step", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-invalid-step", message_id: "message-invalid-step", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "step_started", request_id: "request-invalid-step", message_id: "message-invalid-step", sequence: 2, step_id: "step-1", step_code: "searching_library" }),
        streamRecord({ type: "step_completed", request_id: "request-invalid-step", message_id: "message-invalid-step", sequence: 3, step_id: "step-2", step_code: "searching_library", step_outcome: "completed" }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-invalid-step", text: "问题" },
    )).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("rejects unknown public step codes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-private-step", message_id: "message-private-step", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "step_started", request_id: "request-private-step", message_id: "message-private-step", sequence: 2, step_id: "step-1", step_code: "PRIVATE_INTERNAL_TOOL" }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-private-step", text: "问题" },
    )).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("rejects section deltas that escape their validated lifecycle", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-invalid-section", message_id: "message-invalid-section", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "section_started", request_id: "request-invalid-section", message_id: "message-invalid-section", sequence: 2, section_id: "section-1", status: "grounded", citation_ids: [11], citations: [{ title: "来源视频", excerpt: "字幕依据", url: "https://example.test/video", start_sec: 11 }] }),
        streamRecord({ type: "text_delta", request_id: "request-invalid-section", message_id: "message-invalid-section", sequence: 3, section_id: "section-2", text: "越界文本" }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(
      streamConversationMessage(
        "conversation-1",
        { message_id: "message-invalid-section", text: "问题" },
      ),
    ).rejects.toEqual(expect.objectContaining({ code: "stream_protocol_error" }));
  });

  it("ignores duplicate sequence numbers but rejects a future sequence", async () => {
    const final = {
      status: "ok",
      text: "答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-2", message_id: "message-2", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "activity", request_id: "request-2", message_id: "message-2", sequence: 1, activity: "retrieving" }),
        streamRecord({ type: "completed", request_id: "request-2", message_id: "message-2", sequence: 2, response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    await expect(streamConversationMessage("conversation-1", { message_id: "message-2", text: "问题" })).resolves.toEqual(final);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-3", message_id: "message-3", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "completed", request_id: "request-3", message_id: "message-3", sequence: 3, response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    await expect(streamConversationMessage("conversation-1", { message_id: "message-3", text: "问题" })).rejects.toBeInstanceOf(ConversationStreamError);
  });

  it("rejects records after a terminal completion in the same chunk", async () => {
    const final = {
      status: "ok",
      text: "答案",
      citations: [],
      action_results: [],
      thread_id: "thread-1",
      error_code: null,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response([
        streamRecord({ type: "started", request_id: "request-terminal", message_id: "message-terminal", sequence: 1, activity: "preparing" }),
        streamRecord({ type: "completed", request_id: "request-terminal", message_id: "message-terminal", sequence: 2, response: final }),
        // A terminal event is a hard lifecycle barrier.
        streamRecord({ type: "activity", request_id: "request-terminal", message_id: "message-terminal", sequence: 3, activity: "retrieving" }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(streamConversationMessage(
      "conversation-1",
      { message_id: "message-terminal", text: "问题" },
    )).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("rejects events for a different message id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        streamRecord({
          type: "started",
          request_id: "request-mismatch",
          message_id: "another-message",
          sequence: 1,
          activity: "preparing",
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(
      streamConversationMessage("conversation-1", { message_id: "message-expected", text: "问题" }),
    ).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("requires the stream to start with sequence one", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        streamRecord({
          type: "activity",
          request_id: "request-no-start",
          message_id: "message-no-start",
          sequence: 1,
          activity: "retrieving",
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(
      streamConversationMessage("conversation-1", { message_id: "message-no-start", text: "问题" }),
    ).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("rejects a text delta without text instead of passing it to the UI", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        streamRecord({
          type: "started",
          request_id: "request-invalid-delta",
          message_id: "message-invalid-delta",
          sequence: 1,
          activity: "preparing",
        })
        + streamRecord({
          type: "text_delta",
          request_id: "request-invalid-delta",
          message_id: "message-invalid-delta",
          sequence: 2,
          text: null,
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(
      streamConversationMessage("conversation-1", { message_id: "message-invalid-delta", text: "问题" }),
    ).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("surfaces explicit stream disablement for a one-time JSON fallback", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "streaming_disabled", message: "流式回答当前未启用" }), {
        status: 406,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(streamConversationMessage("conversation-1", { message_id: "message-4", text: "问题" })).rejects.toBeInstanceOf(StreamingUnavailableError);
  });
});

const PAGE_QUESTION: PageQaMessageInput = {
  message_id: "page-message-1",
  question: "这个视频讲了什么？",
  playback_sec: 12.5,
  page: {
    platform: "youtube",
    platform_id: "abcdefghijk",
    canonical_url: "https://www.youtube.com/watch?v=abcdefghijk",
    page_url: "https://www.youtube.com/watch?v=abcdefghijk",
    metadata: { title: "A video", tags: [], chapters: [] },
    caption: { status: "available", source: "official_cc", language: "en", cues: [{ start_sec: 1, end_sec: 2, text: "hello" }] },
  },
};
const PAGE_CONVERSATION_ID = `page-${"a".repeat(32)}`;

function pageEvent(type: string, sequence: number, extra: Record<string, unknown> = {}): string {
  return streamRecord({ type, request_id: "request-page", message_id: "page-message-1", sequence, ...extra });
}

describe("page Q&A stream", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("posts the page to the page-qa route with the extension Bearer and the shared lifecycle checks", async () => {
    vi.stubGlobal("chrome", {
      storage: { local: { get: vi.fn(async () => ({ apiOrigin: "https://api.example", accessToken: "session-token" })) } },
    });
    const citation = { title: "A video", excerpt: "hello", url: "https://www.youtube.com/watch?v=abcdefghijk&t=1s", start_sec: 1 };
    const final = { status: "ok", text: "答案 [S1]\n\n来源：\n1. A video", citations: [citation], action_results: [], thread_id: "thread-page", error_code: null };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response([
        pageEvent("started", 1, { activity: "preparing" }),
        pageEvent("text_delta", 2, { text: "答案 " }),
        pageEvent("text_delta", 3, { text: "[S1]" }),
        pageEvent("completed", 4, { activity: "completed", response: final }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    const events: string[] = [];
    const deltas: string[] = [];

    await expect(streamPageQuestion(
      PAGE_CONVERSATION_ID,
      PAGE_QUESTION,
      { onEvent: (event) => events.push(event.type), onTextDelta: (text) => deltas.push(text) },
    )).resolves.toEqual(final);

    expect(events).toEqual(["started", "text_delta", "text_delta", "completed"]);
    expect(deltas).toEqual(["答案 ", "[S1]"]);
    const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
    expect(url).toBe(`https://api.example/api/v1/page-qa/conversations/${PAGE_CONVERSATION_ID}/messages/stream`);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(JSON.parse(init.body as string)).toEqual(PAGE_QUESTION);
  });

  it("rejects a server error event with its stable code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response([
        pageEvent("started", 1, { activity: "preparing" }),
        pageEvent("error", 2, {
          activity: "failed",
          error_code: "answer_unavailable",
          message: "暂时无法生成可靠回答",
          response: { status: "failed", text: "暂时无法生成可靠回答", citations: [], action_results: [], error_code: "answer_unavailable" },
        }),
      ].join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await expect(streamPageQuestion(PAGE_CONVERSATION_ID, PAGE_QUESTION)).rejects.toMatchObject({
      name: "ConversationStreamError",
      code: "answer_unavailable",
    });
  });

  it("surfaces HTTP failures as ApiError instead of a streaming fallback", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "rate_limited", message: "提问太频繁" }), {
        status: 429,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(streamPageQuestion(PAGE_CONVERSATION_ID, PAGE_QUESTION)).rejects.toMatchObject({ status: 429, code: "rate_limited" });

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "not_found", message: "未启用" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const error = await streamPageQuestion(PAGE_CONVERSATION_ID, PAGE_QUESTION).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(StreamingUnavailableError);
  });

  it("fails closed on a non-stream response and on a foreign message id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    await expect(streamPageQuestion(PAGE_CONVERSATION_ID, PAGE_QUESTION)).rejects.toMatchObject({ code: "stream_protocol_error" });

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        streamRecord({ type: "started", request_id: "request-page", message_id: "someone-else", sequence: 1, activity: "preparing" }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    await expect(streamPageQuestion(PAGE_CONVERSATION_ID, PAGE_QUESTION)).rejects.toMatchObject({ code: "stream_protocol_error" });
  });

  it("maps an aborted request to a cancelled stream error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(Object.assign(new Error("aborted"), { name: "AbortError" }));
    await expect(streamPageQuestion(PAGE_CONVERSATION_ID, PAGE_QUESTION, {}, new AbortController().signal)).rejects.toMatchObject({ code: "cancelled" });
  });
});

