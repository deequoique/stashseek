import { afterEach, describe, expect, it, vi } from "vitest";

import { API_ORIGINS } from "../api-client";
import { newCollectEntry, toStored } from "./collectModel";
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

function stubSessionStorage(options: { failing?: boolean } = {}) {
  const data: Record<string, unknown> = {};
  const get = vi.fn(async (keys: string[]) => {
    if (options.failing) throw new Error("storage unavailable");
    return Object.fromEntries(keys.filter((key) => key in data).map((key) => [key, data[key]]));
  });
  const set = vi.fn(async (values: Record<string, unknown>) => {
    if (options.failing) throw new Error("storage unavailable");
    Object.assign(data, values);
  });
  vi.stubGlobal("chrome", { storage: { session: { get, set } }, runtime: { getURL: (path: string) => `chrome-extension://abc/${path}` } });
  return { data, get, set };
}

describe("unfinished collect store", () => {
  const entryA = toStored({ ...newCollectEntry("a", "youtube:aaaaaaaaaaa", 1), phase: "tracking", path: "url", itemPublicId: "item-1" });
  const entryB = toStored({ ...newCollectEntry("b", "youtube:bbbbbbbbbbb", 2), phase: "tracking", path: "capture", itemPublicId: "item-2" });

  it("keeps one entry per video in chrome.storage.session", async () => {
    const storage = stubSessionStorage();
    await defaultDeps.collectStore.save("youtube:aaaaaaaaaaa", entryA);
    await defaultDeps.collectStore.save("youtube:bbbbbbbbbbb", entryB);

    expect(Object.keys(storage.data)).toEqual(["collectEntries"]);
    await expect(defaultDeps.collectStore.load("youtube:aaaaaaaaaaa")).resolves.toEqual(entryA);
    await expect(defaultDeps.collectStore.load("youtube:cccccccccc1")).resolves.toBeNull();

    await defaultDeps.collectStore.clear("youtube:aaaaaaaaaaa");
    await expect(defaultDeps.collectStore.load("youtube:aaaaaaaaaaa")).resolves.toBeNull();
    await expect(defaultDeps.collectStore.load("youtube:bbbbbbbbbbb")).resolves.toEqual(entryB);
  });

  it("is never fatal when session storage is unavailable", async () => {
    stubSessionStorage({ failing: true });
    await expect(defaultDeps.collectStore.load("youtube:aaaaaaaaaaa")).resolves.toBeNull();
    await expect(defaultDeps.collectStore.save("youtube:aaaaaaaaaaa", entryA)).resolves.toBeUndefined();
    await expect(defaultDeps.collectStore.clear("youtube:aaaaaaaaaaa")).resolves.toBeUndefined();
  });

  it("builds workspace links on the extension origin", () => {
    stubSessionStorage();
    expect(defaultDeps.workspaceUrl("/videos/item-1")).toBe("chrome-extension://abc/workspace.html#/videos/item-1");
  });

  it("builds the privacy policy link on the build's single API origin", () => {
    const stubManifest = (hostPermissions: string[]) => vi.stubGlobal("chrome", {
      runtime: { getManifest: () => ({ host_permissions: hostPermissions }) },
    });

    stubManifest(["https://www.youtube.com/*", `${API_ORIGINS.production}/*`]);
    expect(defaultDeps.privacyUrl()).toBe(`${API_ORIGINS.production}/privacy`);

    stubManifest(["https://www.youtube.com/*", `${API_ORIGINS.local}/*`]);
    expect(defaultDeps.privacyUrl()).toBe(`${API_ORIGINS.local}/privacy`);

    // A manifest with no API origin, or with both, fails closed instead of guessing.
    stubManifest(["https://www.youtube.com/*"]);
    expect(() => defaultDeps.privacyUrl()).toThrow("extension_api_origin_invalid");
    stubManifest([`${API_ORIGINS.production}/*`, `${API_ORIGINS.local}/*`]);
    expect(() => defaultDeps.privacyUrl()).toThrow("extension_api_origin_invalid");
  });

  it("polls every 2.5 s, backs off to 10 s, and stops after 30 minutes", () => {
    expect(defaultDeps.pollIntervalMs).toBe(2_500);
    expect(defaultDeps.pollBackoffMs).toBe(10_000);
    expect(defaultDeps.pollMaxMs).toBe(30 * 60 * 1000);
  });
});
