import { vi } from "vitest";

import type { BatchSubmitItem, BatchSubmitResponse, ConversationResponse, LibraryItem } from "../api/contracts";
import type { PageCapture } from "../protocol";
import type { StoredCollect } from "../sidepanel/collectModel";
import type { CollectStore, PanelDeps, PanelTab, TabsApi } from "../sidepanel/deps";
import type { WorkerApi } from "../sidepanel/workerApi";

export const YT_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa";
export const YT_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb";

export function youtubePage(id = "aaaaaaaaaaa", overrides: Partial<PageCapture> = {}): PageCapture {
  const url = `https://www.youtube.com/watch?v=${id}`;
  return {
    platform: "youtube",
    platform_id: id,
    canonical_url: url,
    page_url: url,
    metadata: {
      title: `Video ${id}`,
      author: "Author",
      duration_sec: 600,
      language: "en",
      description: "About the video",
      cover_url: null,
      tags: [],
      chapters: [],
    },
    caption: {
      status: "available",
      source: "official_cc",
      language: "en",
      cues: [{ start_sec: 1, end_sec: 4, text: "hello world" }, { start_sec: 4, end_sec: 9, text: "second cue" }],
    },
    ...overrides,
  };
}

export const BILI_ID = "BV1xx411c7mD";
export const BILI_URL = `https://www.bilibili.com/video/${BILI_ID}`;
export const BILI_P2_URL = `${BILI_URL}?p=2`;

/** A read Bilibili page; `part` above 1 puts `?p=` in the page URL, as the adapter does. */
export function bilibiliPage(part = 1, overrides: Partial<PageCapture> = {}): PageCapture {
  const base = youtubePage();
  return {
    ...base,
    platform: "bilibili",
    platform_id: BILI_ID,
    canonical_url: BILI_URL,
    page_url: part > 1 ? `${BILI_URL}?p=${part}` : BILI_URL,
    metadata: { ...base.metadata, title: `Bilibili ${BILI_ID}` },
    ...overrides,
  };
}

export function noCaptionBilibiliPage(part = 1): PageCapture {
  return { ...bilibiliPage(part), caption: { status: "unavailable", source: null, language: null, cues: [] } };
}

export function noCaptionPage(): PageCapture {
  return {
    ...youtubePage(),
    caption: { status: "unavailable", source: null, language: null, cues: [] },
  };
}

export const ANSWER: ConversationResponse = {
  status: "ok",
  text: "这是答案 [S3]\n\n来源：\n- Video aaaaaaaaaaa\n  - [S3] https://www.youtube.com/watch?v=aaaaaaaaaaa&t=330s — 摘录",
  citations: [{
    title: "Video aaaaaaaaaaa",
    excerpt: "摘录文字",
    url: "https://www.youtube.com/watch?v=aaaaaaaaaaa&t=330s",
    start_sec: 330,
  }],
  action_results: [],
  thread_id: "thread-1",
  error_code: null,
};

/** A library item as the API returns it, with the fields a progress block reads. */
export function libraryItem(overrides: Partial<LibraryItem> = {}): LibraryItem {
  return {
    author: "Author",
    available_actions: ["edit_why_saved", "archive", "open_source"],
    chapters: [],
    cover_url: null,
    description: null,
    duration_sec: 600,
    error_code: null,
    kind: "video",
    lang: "en",
    latest_dispatch_public_id: null,
    lifecycle: "processing",
    platform: "youtube",
    public_id: "item-1",
    published_at: null,
    saved_at: "2026-10-17T00:00:00Z",
    stage: "fetching",
    stage_updated_at: null,
    tags: [],
    text_source: "captions",
    title: "Video aaaaaaaaaaa",
    url: YT_A,
    why_saved: null,
    ...overrides,
  };
}

export function batchResponse(result: Partial<BatchSubmitItem> = {}): BatchSubmitResponse {
  return {
    results: [{ input_index: 0, result_id: "r-1", status: "queued", item_public_id: "item-1", lifecycle: "queued", ...result }],
  };
}

/** In-memory stand-in for `chrome.storage.session`, with the stored map exposed for assertions. */
export function memoryCollectStore(): CollectStore & { entries: Map<string, StoredCollect> } {
  const entries = new Map<string, StoredCollect>();
  return {
    entries,
    load: async (key) => entries.get(key) ?? null,
    save: async (key, entry) => { entries.set(key, JSON.parse(JSON.stringify(entry)) as StoredCollect); },
    clear: async (key) => { entries.delete(key); },
  };
}

export interface FakeEnvironment {
  deps: PanelDeps;
  worker: { [K in keyof WorkerApi]: ReturnType<typeof vi.fn<WorkerApi[K]>> };
  tabs: { [K in keyof TabsApi]: ReturnType<typeof vi.fn<TabsApi[K]>> };
  streamQuestion: ReturnType<typeof vi.fn<PanelDeps["streamQuestion"]>>;
  submitBatch: ReturnType<typeof vi.fn<PanelDeps["submitBatch"]>>;
  getItem: ReturnType<typeof vi.fn<PanelDeps["getItem"]>>;
  retryItem: ReturnType<typeof vi.fn<PanelDeps["retryItem"]>>;
  collectStore: ReturnType<typeof memoryCollectStore>;
  /** Controls `deps.now` and `deps.isVisible`. */
  clock: { now: number; visible: boolean };
  getSession: ReturnType<typeof vi.fn<PanelDeps["getSession"]>>;
  getCapabilities: ReturnType<typeof vi.fn<PanelDeps["getCapabilities"]>>;
  /** Moves the active tab and tells every subscriber, like tabs.onActivated / onUpdated. */
  navigate(tab: PanelTab | null): void;
  /** Changes the active tab without telling subscribers. */
  setActiveSilently(tab: PanelTab | null): void;
}

export function makeEnvironment(options: {
  tab?: PanelTab | null;
  pages?: Record<string, PageCapture>;
  paired?: boolean;
  pageQa?: boolean;
  /** Platforms the server says it transcribes (capabilities.transcription_platforms). */
  transcriptionPlatforms?: Array<"youtube" | "bilibili">;
} = {}): FakeEnvironment {
  const pages: Record<string, PageCapture> = options.pages ?? { [YT_A]: youtubePage(), [YT_B]: youtubePage("bbbbbbbbbbb") };
  let active: PanelTab | null = options.tab === undefined ? { id: 7, url: YT_A } : options.tab;
  const listeners = new Set<(tab: PanelTab | null) => void>();
  let ids = 0;
  let conversations = 0;

  const tabs: FakeEnvironment["tabs"] = {
    getActive: vi.fn(async () => active),
    subscribe: vi.fn((listener: (tab: PanelTab | null) => void) => {
      listeners.add(listener);
      listener(active);
      return () => { listeners.delete(listener); };
    }),
    openUrl: vi.fn(async () => undefined),
  };
  const worker: FakeEnvironment["worker"] = {
    status: vi.fn(async () => ({ paired: options.paired ?? true, pairing: false })),
    startLogin: vi.fn(async () => undefined),
    finishLogin: vi.fn(async () => undefined),
    readPage: vi.fn(async () => {
      const page = active?.url ? pages[active.url] : undefined;
      if (!page) throw new Error("capture_unavailable");
      return page;
    }),
    playback: vi.fn(async () => 42),
    seek: vi.fn(async () => undefined),
    saveCapture: vi.fn(async () => ({ lifecycle: "queued", status: "accepted", item_public_id: "item-1" })),
    openWorkspace: vi.fn(async () => undefined),
    disconnect: vi.fn(async () => undefined),
  };
  const streamQuestion = vi.fn<PanelDeps["streamQuestion"]>(async (_id, _body, handlers) => {
    handlers?.onTextDelta?.("这是答案");
    return ANSWER;
  });
  const submitBatch = vi.fn<PanelDeps["submitBatch"]>(async () => batchResponse());
  const getItem = vi.fn<PanelDeps["getItem"]>(async () => libraryItem());
  const retryItem = vi.fn<PanelDeps["retryItem"]>(async () => libraryItem({ lifecycle: "queued", stage: "queued" }));
  const collectStore = memoryCollectStore();
  const clock = { now: 1_000_000, visible: true };
  const getSession = vi.fn<PanelDeps["getSession"]>(async () => ({}));
  const getCapabilities = vi.fn<PanelDeps["getCapabilities"]>(async () => ({
    page_qa: options.pageQa ?? true,
    ...(options.transcriptionPlatforms ? { transcription_platforms: options.transcriptionPlatforms } : {}),
  }));

  const deps: PanelDeps = {
    worker,
    tabs,
    getSession,
    getCapabilities,
    streamQuestion,
    submitBatch,
    getItem,
    retryItem,
    collectStore,
    now: () => clock.now,
    isVisible: () => clock.visible,
    workspaceUrl: (route) => `chrome-extension://test/workspace.html#${route}`,
    privacyUrl: () => "https://api.test/privacy",
    createId: () => `message-${++ids}`,
    createConversationId: () => `page-${String(++conversations).padStart(32, "0")}`,
    settleDelayMs: 0,
    retryDelayMs: 0,
    requestTimeoutMs: 5_000,
    pollIntervalMs: 5,
    pollBackoffMs: 25,
    pollMaxMs: 30 * 60 * 1000,
    collectReadWaitMs: 5_000,
    clockTickMs: 60_000,
  };
  return {
    deps,
    worker,
    tabs,
    streamQuestion,
    submitBatch,
    getItem,
    retryItem,
    collectStore,
    clock,
    getSession,
    getCapabilities,
    navigate(tab) {
      active = tab;
      listeners.forEach((listener) => listener(tab));
    },
    setActiveSilently(tab) {
      active = tab;
    },
  };
}
