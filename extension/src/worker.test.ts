import { afterEach, describe, expect, it, vi } from "vitest";

import type { PageCapture } from "./protocol.js";

const ORIGIN = "https://notebookai.deequoique.tech";
const YOUTUBE_ID = "abcdefghijk";
const YOUTUBE_URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
const KALTURA_URL = "https://ntulearn.ntu.edu.sg/ultra/courses/example";

// The worker module is re-imported per test, so injected functions are matched by name.
function injectedName(call: unknown): string {
  return ((call as { func: { name: string } }).func).name;
}

type Reply = { ok: boolean; result?: unknown; error?: string };
type Listener = (message: unknown, sender: unknown, respond: (response: unknown) => void) => boolean | void;

function page(overrides: Partial<PageCapture> = {}): PageCapture {
  return {
    platform: "youtube",
    platform_id: YOUTUBE_ID,
    canonical_url: YOUTUBE_URL,
    page_url: YOUTUBE_URL,
    metadata: {
      title: "A video",
      author: "Author",
      duration_sec: 120,
      language: "en",
      description: "About the video",
      cover_url: `https://i.ytimg.com/vi/${YOUTUBE_ID}/hqdefault.jpg`,
      tags: [],
      chapters: [{ title: "Intro", start_sec: 0, end_sec: null }],
    },
    caption: { status: "available", source: "official_cc", language: "en", cues: [{ start_sec: 1, end_sec: 2, text: "hello" }] },
    ...overrides,
  };
}

function videoProbe(overrides: Record<string, unknown> = {}) {
  return { status: "video", current_sec: 10, ready: true, playing: true, seeked: false, ...overrides };
}

async function loadWorker(initialStorage: Record<string, unknown> = { captureToken: "capture-secret" }) {
  const store: Record<string, unknown> = { apiOrigin: ORIGIN, ...initialStorage };
  const tabs = {
    get: vi.fn(async (tabId: number): Promise<{ id: number; url?: string }> => ({ id: tabId, url: YOUTUBE_URL })),
    query: vi.fn(async () => [{ id: 7, url: YOUTUBE_URL }]),
    create: vi.fn(async () => ({})),
  };
  const executeScript = vi.fn();
  const fetchStub = vi.fn();
  let listener: Listener | undefined;
  const lifecycle: Record<"installed" | "startup", Array<() => void>> = { installed: [], startup: [] };
  const setPanelBehavior = vi.fn<(behavior: { openPanelOnActionClick: boolean }) => Promise<void>>(async () => undefined);
  vi.stubGlobal("chrome", {
    runtime: {
      getManifest: () => ({ host_permissions: ["https://www.youtube.com/*", `${ORIGIN}/*`] }),
      getURL: (path: string) => `chrome-extension://abc/${path}`,
      onMessage: { addListener: (callback: Listener) => { listener = callback; } },
      onInstalled: { addListener: (callback: () => void) => { lifecycle.installed.push(callback); } },
      onStartup: { addListener: (callback: () => void) => { lifecycle.startup.push(callback); } },
    },
    sidePanel: { setPanelBehavior },
    storage: {
      local: {
        get: vi.fn(async (keys: string[]) => Object.fromEntries(keys.filter((key) => key in store).map((key) => [key, store[key]]))),
        set: vi.fn(async (values: Record<string, unknown>) => { Object.assign(store, values); }),
        remove: vi.fn(async (keys: string[]) => { keys.forEach((key) => { delete store[key]; }); }),
      },
    },
    tabs,
    scripting: { executeScript },
  });
  vi.stubGlobal("fetch", fetchStub);
  vi.resetModules();
  await import("./worker.js");
  if (!listener) throw new Error("worker did not register a listener");
  const registered = listener;
  const send = (message: unknown) => new Promise<Reply>((resolve) => {
    registered(message, {}, (response) => resolve(response as Reply));
  });
  return { send, store, tabs, executeScript, fetchStub, lifecycle, setPanelBehavior };
}

function okResponse(body: unknown) {
  return { ok: true, json: async () => body };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("worker command parsing", () => {
  it("rejects unknown commands and malformed messages with stable errors", async () => {
    const { send } = await loadWorker();

    await expect(send({ type: "NOPE" })).resolves.toEqual({ ok: false, error: "unknown_command" });
    await expect(send(null)).resolves.toEqual({ ok: false, error: "unknown_command" });
    await expect(send("CAPTURE")).resolves.toEqual({ ok: false, error: "unknown_command" });
    await expect(send({ type: 7 })).resolves.toEqual({ ok: false, error: "unknown_command" });
  });

  it("keeps the existing commands working", async () => {
    const { send } = await loadWorker();
    await expect(send({ type: "STATUS" })).resolves.toEqual({ ok: true, result: { paired: true, pairing: false } });

    const unpaired = await loadWorker({});
    await expect(unpaired.send({ type: "CAPTURE" })).resolves.toEqual({ ok: false, error: "pairing_required" });
    expect(unpaired.tabs.query).not.toHaveBeenCalled();
  });

  it("requires a valid tab id for the page commands", async () => {
    const { send, executeScript } = await loadWorker();

    for (const type of ["READ_PAGE", "PLAYBACK"]) {
      await expect(send({ type })).resolves.toEqual({ ok: false, error: "invalid_command" });
      await expect(send({ type, tabId: "7" })).resolves.toEqual({ ok: false, error: "invalid_command" });
      await expect(send({ type, tabId: -1 })).resolves.toEqual({ ok: false, error: "invalid_command" });
      await expect(send({ type, tabId: 1.5 })).resolves.toEqual({ ok: false, error: "invalid_command" });
    }
    expect(executeScript).not.toHaveBeenCalled();
  });
});

describe("READ_PAGE", () => {
  it("returns the tab's capture without submitting or storing anything", async () => {
    const { send, tabs, executeScript, fetchStub, store } = await loadWorker();
    const capture = page();
    executeScript.mockResolvedValue([{ frameId: 0, result: { status: "captured", capture } }]);
    const before = JSON.stringify(store);

    const reply = await send({ type: "READ_PAGE", tabId: 7 });

    expect(reply).toEqual({ ok: true, result: capture });
    expect(tabs.get).toHaveBeenCalledWith(7);
    expect(tabs.query).not.toHaveBeenCalled();
    expect(executeScript).toHaveBeenCalledOnce();
    expect(executeScript.mock.calls[0]?.[0]).toMatchObject({ target: { tabId: 7, allFrames: false } });
    expect(injectedName(executeScript.mock.calls[0]?.[0])).toBe("captureYouTubePage");
    expect(fetchStub).not.toHaveBeenCalled();
    expect(JSON.stringify(store)).toBe(before);
  });

  it("works for an unpaired installation and returns an unavailable-caption capture as a success", async () => {
    const { send, executeScript, fetchStub } = await loadWorker({});
    const noCaption = page({ caption: { status: "unavailable", source: null, language: null, cues: [] } });
    executeScript.mockResolvedValue([{ frameId: 0, result: { status: "no_caption", capture: noCaption } }]);

    const reply = await send({ type: "READ_PAGE", tabId: 7 });

    expect(reply).toEqual({ ok: true, result: noCaption });
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("surfaces typed failures for a missing tab, an unsupported page and an adapter error", async () => {
    const { send, tabs, executeScript } = await loadWorker();

    tabs.get.mockRejectedValueOnce(new Error("No tab with id 7"));
    await expect(send({ type: "READ_PAGE", tabId: 7 })).resolves.toEqual({ ok: false, error: "active_tab_missing" });

    tabs.get.mockResolvedValueOnce({ id: 7 });
    await expect(send({ type: "READ_PAGE", tabId: 7 })).resolves.toEqual({ ok: false, error: "unsupported_page" });

    tabs.get.mockResolvedValueOnce({ id: 7, url: "https://example.com/watch?v=abcdefghijk" });
    await expect(send({ type: "READ_PAGE", tabId: 7 })).resolves.toEqual({ ok: false, error: "unsupported_page" });
    expect(executeScript).not.toHaveBeenCalled();

    executeScript.mockResolvedValueOnce([{ frameId: 0, result: { status: "failed", error: "caption_fetch_failed" } }]);
    await expect(send({ type: "READ_PAGE", tabId: 7 })).resolves.toEqual({ ok: false, error: "caption_fetch_failed" });
  });
});

describe("PLAYBACK", () => {
  it("returns the YouTube playback position from the top frame", async () => {
    const { send, executeScript } = await loadWorker();
    executeScript.mockResolvedValue([{ frameId: 0, result: videoProbe({ current_sec: 83.4567 }) }]);

    await expect(send({ type: "PLAYBACK", tabId: 7 })).resolves.toEqual({ ok: true, result: { playback_sec: 83.457 } });

    expect(executeScript).toHaveBeenCalledWith({ target: { tabId: 7, allFrames: false }, world: "MAIN", func: expect.any(Function), args: [null, "youtube"] });
    expect(injectedName(executeScript.mock.calls[0]?.[0])).toBe("controlPlayer");
  });

  it("returns a null position when the page has no ready video", async () => {
    const { send, executeScript } = await loadWorker();
    executeScript.mockResolvedValue([{ frameId: 0, result: { status: "no_video" } }]);

    await expect(send({ type: "PLAYBACK", tabId: 7 })).resolves.toEqual({ ok: true, result: { playback_sec: null } });
  });

  it("picks the Kaltura frame that holds the video from a multi-frame result", async () => {
    const { send, tabs, executeScript } = await loadWorker();
    tabs.get.mockResolvedValue({ id: 8, url: KALTURA_URL });
    executeScript.mockResolvedValue([
      { frameId: 0, result: { status: "no_video" } },
      { frameId: 3, result: { status: "no_video" } },
      { frameId: 5, result: videoProbe({ current_sec: 421.5 }) },
    ]);

    await expect(send({ type: "PLAYBACK", tabId: 8 })).resolves.toEqual({ ok: true, result: { playback_sec: 421.5 } });

    expect(executeScript.mock.calls[0]?.[0]).toMatchObject({ target: { tabId: 8, allFrames: true }, args: [null, "ntu_kaltura"] });
  });

  it("fails for an unsupported page", async () => {
    const { send, tabs, executeScript } = await loadWorker();
    tabs.get.mockResolvedValue({ id: 7, url: "https://example.com/" });

    await expect(send({ type: "PLAYBACK", tabId: 7 })).resolves.toEqual({ ok: false, error: "unsupported_page" });
    expect(executeScript).not.toHaveBeenCalled();
  });
});

describe("SEEK", () => {
  it("seeks YouTube with the requested position", async () => {
    const { send, executeScript } = await loadWorker();
    executeScript.mockResolvedValue([{ frameId: 0, result: videoProbe({ seeked: true, current_sec: 125.5 }) }]);

    await expect(send({ type: "SEEK", tabId: 7, start_sec: 125.5 })).resolves.toEqual({ ok: true, result: { seeked: true } });

    expect(executeScript).toHaveBeenCalledWith({ target: { tabId: 7, allFrames: false }, world: "MAIN", func: expect.any(Function), args: [125.5, "youtube"] });
    expect(injectedName(executeScript.mock.calls[0]?.[0])).toBe("controlPlayer");
  });

  it("rejects an invalid start_sec without touching the tab", async () => {
    const { send, tabs, executeScript } = await loadWorker();

    for (const start_sec of [-1, Number.NaN, Number.POSITIVE_INFINITY, "10", null, undefined, 31_536_001]) {
      await expect(send({ type: "SEEK", tabId: 7, start_sec })).resolves.toEqual({ ok: false, error: "invalid_command" });
    }
    await expect(send({ type: "SEEK", start_sec: 5 })).resolves.toEqual({ ok: false, error: "invalid_command" });
    expect(tabs.get).not.toHaveBeenCalled();
    expect(executeScript).not.toHaveBeenCalled();
  });

  it("reports seek_failed when no player accepted the position", async () => {
    const { send, executeScript } = await loadWorker();
    executeScript.mockResolvedValue([{ frameId: 0, result: { status: "no_video" } }]);

    await expect(send({ type: "SEEK", tabId: 7, start_sec: 30 })).resolves.toEqual({ ok: false, error: "seek_failed" });
  });

  it("seeks only the Kaltura frame that holds the video", async () => {
    const { send, tabs, executeScript } = await loadWorker();
    tabs.get.mockResolvedValue({ id: 8, url: KALTURA_URL });
    executeScript
      .mockResolvedValueOnce([
        { frameId: 0, result: { status: "no_video" } },
        { frameId: 5, result: videoProbe() },
      ])
      .mockResolvedValueOnce([{ frameId: 5, result: videoProbe({ seeked: true, current_sec: 300 }) }]);

    await expect(send({ type: "SEEK", tabId: 8, start_sec: 300 })).resolves.toEqual({ ok: true, result: { seeked: true } });

    expect(executeScript.mock.calls[1]?.[0]).toMatchObject({ target: { tabId: 8, frameIds: [5] }, args: [300, "ntu_kaltura"] });
  });
});

describe("SAVE_CAPTURE", () => {
  it("posts the given page with the capture Bearer and an idempotency key, without reading the tab", async () => {
    const { send, tabs, executeScript, fetchStub, store } = await loadWorker();
    fetchStub.mockResolvedValue(okResponse({ lifecycle: "queued", status: "accepted", item_public_id: "item-1" }));

    const reply = await send({ type: "SAVE_CAPTURE", page: page() });

    expect(reply).toEqual({ ok: true, result: { lifecycle: "queued", status: "accepted", item_public_id: "item-1" } });
    expect(tabs.get).not.toHaveBeenCalled();
    expect(tabs.query).not.toHaveBeenCalled();
    expect(executeScript).not.toHaveBeenCalled();
    expect(fetchStub).toHaveBeenCalledOnce();
    const [url, init] = fetchStub.mock.calls[0] as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe(`${ORIGIN}/api/v1/browser-companion/extension/captures`);
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer capture-secret");
    expect(init.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      protocol_version: "capture.v1",
      platform: "youtube",
      platform_id: YOUTUBE_ID,
      content_hash: expect.stringMatching(/^[0-9a-f]{64}$/),
    });
    expect((body.metadata as { description: string }).description).toBe("About the video");
    expect("pendingCaptureKey" in store).toBe(false);
    expect("pendingCaptureRef" in store).toBe(false);
  });

  it("reuses the pending idempotency key across retries, and a later CAPTURE of the same page shares it", async () => {
    const { send, tabs, executeScript, fetchStub, store } = await loadWorker();
    fetchStub.mockRejectedValueOnce(new TypeError("offline"));

    await expect(send({ type: "SAVE_CAPTURE", page: page() })).resolves.toEqual({ ok: false, error: "network_unavailable" });
    const firstKey = (fetchStub.mock.calls[0] as [string, { headers: Record<string, string> }])[1].headers["Idempotency-Key"];
    expect(store.pendingCaptureKey).toBe(firstKey);

    fetchStub.mockRejectedValueOnce(new TypeError("offline"));
    await send({ type: "SAVE_CAPTURE", page: page() });
    expect((fetchStub.mock.calls[1] as [string, { headers: Record<string, string> }])[1].headers["Idempotency-Key"]).toBe(firstKey);

    tabs.query.mockResolvedValue([{ id: 7, url: YOUTUBE_URL }]);
    executeScript.mockResolvedValue([{ frameId: 0, result: { status: "captured", capture: page() } }]);
    fetchStub.mockResolvedValueOnce(okResponse({ lifecycle: "queued", status: "accepted", item_public_id: "item-1" }));
    await expect(send({ type: "CAPTURE" })).resolves.toMatchObject({ ok: true });
    expect((fetchStub.mock.calls[2] as [string, { headers: Record<string, string> }])[1].headers["Idempotency-Key"]).toBe(firstKey);
    expect("pendingCaptureKey" in store).toBe(false);
  });

  it("uses a new idempotency key for different content and clears it after a terminal conflict", async () => {
    const { send, fetchStub, store } = await loadWorker();
    fetchStub.mockRejectedValue(new TypeError("offline"));
    await send({ type: "SAVE_CAPTURE", page: page() });
    const firstKey = store.pendingCaptureKey;

    const changed = page({ caption: { status: "available", source: "official_cc", language: "en", cues: [{ start_sec: 1, end_sec: 2, text: "different" }] } });
    await send({ type: "SAVE_CAPTURE", page: changed });
    expect(store.pendingCaptureKey).not.toBe(firstKey);

    fetchStub.mockResolvedValueOnce({ ok: false, json: async () => ({ code: "capture_conflict" }) });
    await expect(send({ type: "SAVE_CAPTURE", page: changed })).resolves.toEqual({ ok: false, error: "capture_conflict" });
    expect("pendingCaptureKey" in store).toBe(false);
  });

  it("fails before any request when the device is not paired", async () => {
    const { send, fetchStub } = await loadWorker({});

    await expect(send({ type: "SAVE_CAPTURE", page: page() })).resolves.toEqual({ ok: false, error: "pairing_required" });
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("rejects a malformed page and strips fields outside capture.v1", async () => {
    const { send, fetchStub } = await loadWorker();
    fetchStub.mockResolvedValue(okResponse({ lifecycle: "queued", status: "accepted", item_public_id: "item-1" }));

    await expect(send({ type: "SAVE_CAPTURE" })).resolves.toEqual({ ok: false, error: "invalid_command" });
    await expect(send({ type: "SAVE_CAPTURE", page: { platform: "youtube" } })).resolves.toEqual({ ok: false, error: "invalid_command" });
    await expect(send({ type: "SAVE_CAPTURE", page: page({ platform_id: "short" }) })).resolves.toEqual({ ok: false, error: "invalid_command" });
    expect(fetchStub).not.toHaveBeenCalled();

    await send({ type: "SAVE_CAPTURE", page: { ...page(), ks: "private-signature", caption_url: "https://x.example/c" } });
    const body = String((fetchStub.mock.calls[0] as [string, RequestInit])[1].body);
    expect(body).not.toContain("private-signature");
    expect(body).not.toContain("caption_url");
  });
});

describe("side panel behaviour", () => {
  it("makes the toolbar icon open the side panel on install and on browser startup", async () => {
    const { lifecycle, setPanelBehavior } = await loadWorker();
    expect(lifecycle.installed).toHaveLength(1);
    expect(lifecycle.startup).toHaveLength(1);
    expect(setPanelBehavior).not.toHaveBeenCalled();

    lifecycle.installed[0]?.();
    lifecycle.startup[0]?.();

    expect(setPanelBehavior).toHaveBeenCalledTimes(2);
    expect(setPanelBehavior).toHaveBeenCalledWith({ openPanelOnActionClick: true });
  });

  it("keeps serving commands when the side panel API rejects or is missing", async () => {
    const { lifecycle, setPanelBehavior, send } = await loadWorker();
    setPanelBehavior.mockRejectedValueOnce(new Error("unsupported"));
    expect(() => lifecycle.installed[0]?.()).not.toThrow();
    setPanelBehavior.mockImplementationOnce(() => { throw new Error("sync failure"); });
    expect(() => lifecycle.startup[0]?.()).not.toThrow();
    await expect(send({ type: "STATUS" })).resolves.toEqual({ ok: true, result: { paired: true, pairing: false } });
  });
});

