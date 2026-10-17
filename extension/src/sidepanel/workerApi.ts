import type { PageCapture } from "../protocol";

/** A failed worker command. `code` is the worker's stable error string. */
export class WorkerError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "WorkerError";
  }
}

export interface WorkerStatus {
  paired: boolean;
  pairing: boolean;
}

export interface SaveResult {
  lifecycle: string;
  status: string;
  item_public_id: string;
}

/** Typed view of the service worker's command channel. The worker owns every token. */
export interface WorkerApi {
  status(): Promise<WorkerStatus>;
  /** Sends the email code and prepares the capture-device pairing. */
  startLogin(email: string): Promise<void>;
  /** Verifies the code, approves the pairing, and stores both tokens. */
  finishLogin(email: string, code: string): Promise<void>;
  /** Reads captions and metadata from the tab. Never submits anything. */
  readPage(tabId: number): Promise<PageCapture>;
  playback(tabId: number): Promise<number | null>;
  seek(tabId: number, startSec: number): Promise<void>;
  /** Submits a page that was already read. */
  saveCapture(page: PageCapture): Promise<SaveResult>;
  openWorkspace(): Promise<void>;
  disconnect(): Promise<void>;
}

async function send<T>(command: Record<string, unknown>): Promise<T> {
  let reply: unknown;
  try {
    reply = await chrome.runtime.sendMessage(command);
  } catch {
    throw new WorkerError("worker_unavailable");
  }
  if (typeof reply !== "object" || reply === null) throw new WorkerError("worker_unavailable");
  const { ok, result, error } = reply as { ok?: unknown; result?: unknown; error?: unknown };
  if (ok === true) return result as T;
  throw new WorkerError(typeof error === "string" && error ? error : "request_failed");
}

export const workerApi: WorkerApi = {
  status: () => send<WorkerStatus>({ type: "STATUS" }),
  startLogin: async (email) => { await send({ type: "START_LOGIN", email }); },
  finishLogin: async (email, code) => { await send({ type: "FINISH_LOGIN", email, code }); },
  readPage: (tabId) => send<PageCapture>({ type: "READ_PAGE", tabId }),
  playback: async (tabId) => (await send<{ playback_sec: number | null }>({ type: "PLAYBACK", tabId })).playback_sec,
  seek: async (tabId, startSec) => { await send({ type: "SEEK", tabId, start_sec: startSec }); },
  saveCapture: (page) => send<SaveResult>({ type: "SAVE_CAPTURE", page }),
  openWorkspace: async () => { await send({ type: "OPEN_WORKSPACE" }); },
  disconnect: async () => { await send({ type: "DISCONNECT" }); },
};
