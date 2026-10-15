import { vi } from "vitest";

import type { ConversationResponse } from "../api/contracts";
import type { PageCapture } from "../protocol";
import type { PanelDeps, PanelTab, TabsApi } from "../sidepanel/deps";
import type { WorkerApi } from "../sidepanel/workerApi";

export const YT_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa";
export const YT_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb";
export const KALTURA_URL = "https://ntulearnv1.ntu.edu.sg/media/t/1_abcdefgh";

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

export function noCaptionPage(platform: "youtube" | "ntu_kaltura"): PageCapture {
  const base = platform === "youtube" ? youtubePage() : {
    ...youtubePage(),
    platform: "ntu_kaltura" as const,
    platform_id: "1_abcdefgh",
    canonical_url: KALTURA_URL,
    page_url: KALTURA_URL,
  };
  return {
    ...base,
    metadata: platform === "youtube"
      ? base.metadata
      : { ...base.metadata, title: null, author: null, duration_sec: null, description: null },
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

export interface FakeEnvironment {
  deps: PanelDeps;
  worker: { [K in keyof WorkerApi]: ReturnType<typeof vi.fn<WorkerApi[K]>> };
  tabs: { [K in keyof TabsApi]: ReturnType<typeof vi.fn<TabsApi[K]>> };
  streamQuestion: ReturnType<typeof vi.fn<PanelDeps["streamQuestion"]>>;
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
  const getSession = vi.fn<PanelDeps["getSession"]>(async () => ({}));
  const getCapabilities = vi.fn<PanelDeps["getCapabilities"]>(async () => ({ page_qa: options.pageQa ?? true }));

  const deps: PanelDeps = {
    worker,
    tabs,
    getSession,
    getCapabilities,
    streamQuestion,
    createId: () => `message-${++ids}`,
    createConversationId: () => `page-${String(++conversations).padStart(32, "0")}`,
    settleDelayMs: 0,
    retryDelayMs: 0,
    requestTimeoutMs: 5_000,
  };
  return {
    deps,
    worker,
    tabs,
    streamQuestion,
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
