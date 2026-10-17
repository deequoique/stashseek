import { getCapabilities, getLibraryItem, getSession, streamPageQuestion, submitVideoBatch } from "../api/client";
import type { StoredCollect } from "./collectModel";
import { newConversationId } from "./pageIdentity";
import { workerApi, type WorkerApi } from "./workerApi";

export interface PanelTab {
  id: number;
  /** Undefined or null when Chrome withholds the URL, i.e. not a host the extension may read. */
  url: string | null;
}

export interface TabsApi {
  /** The active tab of this panel's window. */
  getActive(): Promise<PanelTab | null>;
  /** Delivers the current active tab first, then every change of the active tab or its URL. */
  subscribe(listener: (tab: PanelTab | null) => void): () => void;
  openUrl(url: string): Promise<void>;
}

/** Unfinished 「收藏视频」 actions, kept for the browser session so a reopened panel can resume them. */
export interface CollectStore {
  load(videoKey: string): Promise<unknown>;
  save(videoKey: string, entry: StoredCollect): Promise<void>;
  clear(videoKey: string): Promise<void>;
}

/** Everything the panel touches outside React, so tests can replace it without a browser. */
export interface PanelDeps {
  worker: WorkerApi;
  tabs: TabsApi;
  getSession: () => Promise<unknown>;
  getCapabilities: () => Promise<{ page_qa?: boolean }>;
  streamQuestion: typeof streamPageQuestion;
  /** Library batch import by URL (extension session Bearer). */
  submitBatch: typeof submitVideoBatch;
  getItem: typeof getLibraryItem;
  collectStore: CollectStore;
  /** Epoch milliseconds. */
  now: () => number;
  /** False while the panel's document is hidden; progress polling pauses then. */
  isVisible: () => boolean;
  /** Extension-page URL for a workspace route such as `/videos/<id>`. */
  workspaceUrl: (route: string) => string;
  createId: () => string;
  createConversationId: () => string;
  /** Wait after a tab change before reading, so a YouTube SPA navigation can settle. */
  settleDelayMs: number;
  /** Wait before re-reading a page whose player had not loaded yet. */
  retryDelayMs: number;
  /** Bound for the startup session and capability checks. */
  requestTimeoutMs: number;
  /** Item progress poll interval, and the slower interval after a failed read. */
  pollIntervalMs: number;
  pollBackoffMs: number;
  /** Progress polling stops this long after the click. */
  pollMaxMs: number;
  /** How long 「收藏视频」 waits for a page read before importing by URL instead. */
  collectReadWaitMs: number;
  /** Refresh rate of the live elapsed/ETA line. */
  clockTickMs: number;
}

function toPanelTab(tab: chrome.tabs.Tab | undefined): PanelTab | null {
  return tab?.id === undefined ? null : { id: tab.id, url: tab.url ?? null };
}

async function queryActive(): Promise<PanelTab | null> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return toPanelTab(tab);
}

const chromeTabs: TabsApi = {
  getActive: queryActive,
  subscribe(listener) {
    let activeId: number | null = null;
    let live = true;
    const deliver = (tab: PanelTab | null) => {
      if (!live) return;
      activeId = tab?.id ?? null;
      listener(tab);
    };
    const refresh = () => { void queryActive().then(deliver, () => deliver(null)); };
    const onUpdated = (tabId: number, change: chrome.tabs.ChangeInfo, tab: chrome.tabs.Tab) => {
      // Other tabs, and updates that change neither the URL nor the load state, cannot change the video.
      if (tabId !== activeId || (change.url === undefined && change.status !== "complete")) return;
      deliver(toPanelTab(tab));
    };
    chrome.tabs.onActivated.addListener(refresh);
    chrome.tabs.onUpdated.addListener(onUpdated);
    refresh();
    return () => {
      live = false;
      chrome.tabs.onActivated.removeListener(refresh);
      chrome.tabs.onUpdated.removeListener(onUpdated);
    };
  },
  async openUrl(url) {
    await chrome.tabs.create({ url });
  },
};

const COLLECT_STORAGE_KEY = "collectEntries";

async function readCollectMap(): Promise<Record<string, unknown>> {
  const stored = await chrome.storage.session.get([COLLECT_STORAGE_KEY]);
  const value = stored[COLLECT_STORAGE_KEY];
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

/** `chrome.storage.session`: cleared when the browser closes and never shared with web pages. Failures are not fatal. */
const sessionCollectStore: CollectStore = {
  async load(videoKey) {
    try { return (await readCollectMap())[videoKey] ?? null; } catch { return null; }
  },
  async save(videoKey, entry) {
    try { await chrome.storage.session.set({ [COLLECT_STORAGE_KEY]: { ...(await readCollectMap()), [videoKey]: entry } }); } catch { /* Resuming is a convenience. */ }
  },
  async clear(videoKey) {
    try {
      const rest = await readCollectMap();
      delete rest[videoKey];
      await chrome.storage.session.set({ [COLLECT_STORAGE_KEY]: rest });
    } catch { /* Resuming is a convenience. */ }
  },
};

export const defaultDeps: PanelDeps = {
  worker: workerApi,
  tabs: chromeTabs,
  getSession,
  getCapabilities,
  streamQuestion: streamPageQuestion,
  submitBatch: submitVideoBatch,
  getItem: getLibraryItem,
  collectStore: sessionCollectStore,
  now: () => Date.now(),
  isVisible: () => document.visibilityState === "visible",
  workspaceUrl: (route) => chrome.runtime.getURL(`workspace.html#${route}`),
  createId: () => crypto.randomUUID(),
  createConversationId: newConversationId,
  settleDelayMs: 600,
  retryDelayMs: 1500,
  requestTimeoutMs: 10_000,
  pollIntervalMs: 2_500,
  pollBackoffMs: 10_000,
  pollMaxMs: 30 * 60 * 1000,
  collectReadWaitMs: 20_000,
  clockTickMs: 1_000,
};
