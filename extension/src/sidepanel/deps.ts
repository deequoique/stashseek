import { getCapabilities, getSession, streamPageQuestion } from "../api/client";
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

/** Everything the panel touches outside React, so tests can replace it without a browser. */
export interface PanelDeps {
  worker: WorkerApi;
  tabs: TabsApi;
  getSession: () => Promise<unknown>;
  getCapabilities: () => Promise<{ page_qa?: boolean }>;
  streamQuestion: typeof streamPageQuestion;
  createId: () => string;
  createConversationId: () => string;
  /** Wait after a tab change before reading, so a YouTube SPA navigation can settle. */
  settleDelayMs: number;
  /** Wait before re-reading a page whose player had not loaded yet. */
  retryDelayMs: number;
  /** Bound for the startup session and capability checks. */
  requestTimeoutMs: number;
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

export const defaultDeps: PanelDeps = {
  worker: workerApi,
  tabs: chromeTabs,
  getSession,
  getCapabilities,
  streamQuestion: streamPageQuestion,
  createId: () => crypto.randomUUID(),
  createConversationId: newConversationId,
  settleDelayMs: 600,
  retryDelayMs: 1500,
  requestTimeoutMs: 10_000,
};
