declare namespace chrome {
  namespace runtime {
    const lastError: { message?: string } | undefined;
    function getManifest(): { host_permissions?: string[] };
    function getURL(path: string): string;
    function sendMessage(message: unknown): Promise<unknown>;
    const onInstalled: { addListener(callback: () => void): void };
    const onStartup: { addListener(callback: () => void): void };
    const onMessage: { addListener(callback: (message: unknown, sender: unknown, respond: (response: unknown) => void) => boolean | void): void };
  }
  namespace storage { namespace local { function get(keys: string[]): Promise<Record<string, unknown>>; function set(values: Record<string, unknown>): Promise<void>; function remove(keys: string[]): Promise<void>; } }
  namespace sidePanel {
    function setPanelBehavior(behavior: { openPanelOnActionClick: boolean }): Promise<void>;
  }
  namespace tabs {
    type Tab = { id?: number; url?: string };
    type ActiveInfo = { tabId: number; windowId: number };
    type ChangeInfo = { status?: string; url?: string };
    function query(query: { active: boolean; currentWindow: boolean }): Promise<Tab[]>;
    function get(tabId: number): Promise<Tab>;
    function create(create: { url: string }): Promise<unknown>;
    const onActivated: {
      addListener(callback: (info: ActiveInfo) => void): void;
      removeListener(callback: (info: ActiveInfo) => void): void;
    };
    const onUpdated: {
      addListener(callback: (tabId: number, changeInfo: ChangeInfo, tab: Tab) => void): void;
      removeListener(callback: (tabId: number, changeInfo: ChangeInfo, tab: Tab) => void): void;
    };
  }
  namespace scripting {
    function executeScript<T, A extends unknown[] = []>(injection: {
      target: { tabId: number; allFrames?: boolean; frameIds?: number[] };
      world: "MAIN";
      func: (...args: A) => T | Promise<T>;
      args?: A;
    }): Promise<Array<{ frameId?: number; result?: T; error?: string }>>;
  }
}
