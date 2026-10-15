declare namespace chrome {
  namespace runtime {
    const lastError: { message?: string } | undefined;
    function getManifest(): { host_permissions?: string[] };
    function getURL(path: string): string;
    function sendMessage(message: unknown): Promise<unknown>;
    const onMessage: { addListener(callback: (message: unknown, sender: unknown, respond: (response: unknown) => void) => boolean | void): void };
  }
  namespace storage { namespace local { function get(keys: string[]): Promise<Record<string, unknown>>; function set(values: Record<string, unknown>): Promise<void>; function remove(keys: string[]): Promise<void>; } }
  namespace tabs {
    function query(query: { active: boolean; currentWindow: boolean }): Promise<Array<{ id?: number; url?: string }>>;
    function get(tabId: number): Promise<{ id?: number; url?: string }>;
    function create(create: { url: string }): Promise<unknown>;
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
