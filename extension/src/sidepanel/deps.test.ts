import { afterEach, describe, expect, it, vi } from "vitest";

import { defaultDeps, type PanelTab } from "./deps";

type ActivatedListener = (info: { tabId: number; windowId: number }) => void;
type UpdatedListener = (tabId: number, change: { status?: string; url?: string }, tab: { id?: number; url?: string }) => void;

const VIDEO_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa";
const VIDEO_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb";

function stubTabs(initial: { id?: number; url?: string } | undefined) {
  let active = initial;
  const activated = new Set<ActivatedListener>();
  const updated = new Set<UpdatedListener>();
  const query = vi.fn(async () => (active ? [active] : []));
  const create = vi.fn(async () => ({}));
  vi.stubGlobal("chrome", {
    tabs: {
      query,
      create,
      onActivated: { addListener: (l: ActivatedListener) => activated.add(l), removeListener: (l: ActivatedListener) => activated.delete(l) },
      onUpdated: { addListener: (l: UpdatedListener) => updated.add(l), removeListener: (l: UpdatedListener) => updated.delete(l) },
    },
  });
  return {
    query,
    create,
    activated,
    updated,
    setActive(tab: { id?: number; url?: string } | undefined) { active = tab; },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => { vi.unstubAllGlobals(); });

describe("chrome tab adapter", () => {
  it("delivers the active tab first and reports a withheld URL as null", async () => {
    stubTabs({ id: 4, url: undefined });
    const seen: Array<PanelTab | null> = [];
    const unsubscribe = defaultDeps.tabs.subscribe((tab) => seen.push(tab));
    await flush();
    expect(seen).toEqual([{ id: 4, url: null }]);
    unsubscribe();
  });

  it("delivers no tab when the window has none", async () => {
    stubTabs(undefined);
    const seen: Array<PanelTab | null> = [];
    const unsubscribe = defaultDeps.tabs.subscribe((tab) => seen.push(tab));
    await flush();
    expect(seen).toEqual([null]);
    unsubscribe();
  });

  it("follows tab activation and same-tab URL changes (YouTube SPA navigation)", async () => {
    const chromeStub = stubTabs({ id: 4, url: VIDEO_A });
    const seen: Array<PanelTab | null> = [];
    const unsubscribe = defaultDeps.tabs.subscribe((tab) => seen.push(tab));
    await flush();

    for (const listener of chromeStub.updated) listener(4, { url: VIDEO_B }, { id: 4, url: VIDEO_B });
    chromeStub.setActive({ id: 9, url: VIDEO_A });
    for (const listener of chromeStub.activated) listener({ tabId: 9, windowId: 1 });
    await flush();

    expect(seen).toEqual([
      { id: 4, url: VIDEO_A },
      { id: 4, url: VIDEO_B },
      { id: 9, url: VIDEO_A },
    ]);
    unsubscribe();
  });

  it("ignores updates of other tabs and updates that change neither URL nor load state", async () => {
    const chromeStub = stubTabs({ id: 4, url: VIDEO_A });
    const seen: Array<PanelTab | null> = [];
    const unsubscribe = defaultDeps.tabs.subscribe((tab) => seen.push(tab));
    await flush();

    for (const listener of chromeStub.updated) {
      listener(5, { url: VIDEO_B }, { id: 5, url: VIDEO_B });
      listener(4, { status: "loading" }, { id: 4, url: VIDEO_A });
    }
    expect(seen).toHaveLength(1);

    for (const listener of chromeStub.updated) listener(4, { status: "complete" }, { id: 4, url: VIDEO_A });
    expect(seen).toHaveLength(2);
    unsubscribe();
  });

  it("stops listening and delivering after unsubscribe", async () => {
    const chromeStub = stubTabs({ id: 4, url: VIDEO_A });
    const seen: Array<PanelTab | null> = [];
    const unsubscribe = defaultDeps.tabs.subscribe((tab) => seen.push(tab));
    await flush();
    unsubscribe();

    expect(chromeStub.activated.size).toBe(0);
    expect(chromeStub.updated.size).toBe(0);
    expect(seen).toHaveLength(1);
  });

  it("reads the active tab and opens a link in a new tab", async () => {
    const chromeStub = stubTabs({ id: 4, url: VIDEO_A });
    await expect(defaultDeps.tabs.getActive()).resolves.toEqual({ id: 4, url: VIDEO_A });
    await defaultDeps.tabs.openUrl(VIDEO_B);
    expect(chromeStub.create).toHaveBeenCalledWith({ url: VIDEO_B });
  });
});
