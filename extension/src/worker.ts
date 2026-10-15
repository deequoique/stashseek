import {
  captureActivePage,
  isSeekSeconds,
  normalizePageCapture,
  readPlaybackSeconds,
  seekPlayback,
} from "./page-capture.js";
import {
  CAPTURE_REQUEST_TIMEOUT_MS,
  requestJson,
  resolveApiOrigin,
  storedOriginAction,
} from "./api-client.js";
import { captureRequest, CLIENT_VERSION, sha256Hex, type PageCapture } from "./protocol.js";

const API_ORIGIN = resolveApiOrigin(chrome.runtime.getManifest().host_permissions ?? []);
const STORAGE_KEYS = ["apiOrigin", "accessToken", "captureToken", "expiresAt", "user", "tenant", "loginChallengeId", "deviceId", "pairingId", "pairingVerifier", "pendingCaptureRef", "pendingCaptureKey"];

type PlainCommandType = "STATUS" | "START_PAIRING" | "FINISH_PAIRING" | "START_LOGIN" | "FINISH_LOGIN" | "OPEN_WORKSPACE" | "CAPTURE" | "LIBRARY" | "DISCONNECT";
const PLAIN_COMMANDS = new Set<string>(["STATUS", "START_PAIRING", "FINISH_PAIRING", "START_LOGIN", "FINISH_LOGIN", "OPEN_WORKSPACE", "CAPTURE", "LIBRARY", "DISCONNECT"]);

// Closed command union. Replies are `{ ok: true, result }` or `{ ok: false, error }`.
// The side panel uses the last four:
//   READ_PAGE    -> PageCapture, read from the tab and never submitted
//   PLAYBACK     -> { playback_sec: number | null }
//   SEEK         -> { seeked: true } or error `seek_failed`
//   SAVE_CAPTURE -> the capture submission result, same as CAPTURE but for a page already read
type Command =
  | { type: PlainCommandType; email?: string; code?: string }
  | { type: "READ_PAGE"; tabId: number }
  | { type: "PLAYBACK"; tabId: number }
  | { type: "SEEK"; tabId: number; start_sec: number }
  | { type: "SAVE_CAPTURE"; page: PageCapture };

/** Returns a typed command, `unknown_command` for an unrecognised type, or `invalid_command` for a bad shape. */
function parseCommand(message: unknown): Command | "unknown_command" | "invalid_command" {
  if (!message || typeof message !== "object") return "unknown_command";
  const record = message as Record<string, unknown>;
  const type = record.type;
  if (typeof type !== "string") return "unknown_command";
  if (PLAIN_COMMANDS.has(type)) {
    return {
      type: type as PlainCommandType,
      email: typeof record.email === "string" ? record.email : undefined,
      code: typeof record.code === "string" ? record.code : undefined,
    };
  }
  if (type === "READ_PAGE" || type === "PLAYBACK" || type === "SEEK") {
    const tabId = record.tabId;
    if (typeof tabId !== "number" || !Number.isSafeInteger(tabId) || tabId < 0) return "invalid_command";
    if (type !== "SEEK") return { type, tabId };
    return isSeekSeconds(record.start_sec) ? { type, tabId, start_sec: record.start_sec } : "invalid_command";
  }
  if (type === "SAVE_CAPTURE") {
    const page = normalizePageCapture(record.page);
    return page ? { type, page } : "invalid_command";
  }
  return "unknown_command";
}

function base64Url(bytes: Uint8Array): string {
  let raw = "";
  bytes.forEach((byte) => { raw += String.fromCharCode(byte); });
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function status() {
  const state = await storageState();
  return { paired: typeof state.captureToken === "string", pairing: typeof state.pairingId === "string" };
}

async function storageState(): Promise<Record<string, unknown>> {
  const state = await chrome.storage.local.get(STORAGE_KEYS);
  const action = storedOriginAction(state.apiOrigin, API_ORIGIN);
  if (action === "current") return state;
  if (action === "adopt") {
    await chrome.storage.local.set({ apiOrigin: API_ORIGIN });
    return { ...state, apiOrigin: API_ORIGIN };
  }
  await chrome.storage.local.remove(STORAGE_KEYS);
  await chrome.storage.local.set({ apiOrigin: API_ORIGIN });
  return { apiOrigin: API_ORIGIN };
}

async function startPairing(email?: string) {
  await storageState();
  if (!email) throw new Error("login_required");
  const emailChallenge = await requestJson<{ challenge_id: string }>(API_ORIGIN, "/api/v1/extension/auth/challenge", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const verifierBytes = crypto.getRandomValues(new Uint8Array(48));
  const verifier = base64Url(verifierBytes);
  const pairingChallenge = base64Url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const pairing = await requestJson<{ pairing_id: string; approval_url: string }>(API_ORIGIN, "/api/v1/browser-companion/extension/pairings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ challenge: pairingChallenge, client_label: "Chrome / Chromium", client_version: CLIENT_VERSION }),
  });
  await chrome.storage.local.set({ apiOrigin: API_ORIGIN, loginChallengeId: emailChallenge.challenge_id, pairingId: pairing.pairing_id, pairingVerifier: verifier });
  return { awaitingApproval: true };
}

async function startLogin(email?: string) {
  return startPairing(email);
}

async function finishLogin(email?: string, code?: string) {
  return finishPairing(email, code);
}

async function finishPairing(_email?: string, code?: string) {
  const state = await storageState();
  if (typeof state.pairingId !== "string" || typeof state.pairingVerifier !== "string" || typeof state.loginChallengeId !== "string") throw new Error("pairing_missing");
  if (!code) throw new Error("login_required");
  const session = await requestJson<{ access_token: string; expires_at: string; user: Record<string, unknown>; tenant: Record<string, unknown> }>(API_ORIGIN, "/api/v1/extension/auth/verify", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ challenge_id: state.loginChallengeId, code }),
  });
  await requestJson(API_ORIGIN, `/api/v1/browser-companion/pairings/${encodeURIComponent(state.pairingId)}:approve`, {
    method: "POST", headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const pairingStatus = await requestJson<{ status: string }>(API_ORIGIN, `/api/v1/browser-companion/extension/pairings/${encodeURIComponent(state.pairingId)}`);
  if (pairingStatus.status !== "approved") throw new Error(`pairing_${pairingStatus.status}`);
  const grant = await requestJson<{ access_token: string; device_id: string }>(API_ORIGIN, `/api/v1/browser-companion/extension/pairings/${encodeURIComponent(state.pairingId)}:exchange`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verifier: state.pairingVerifier }),
  });
  await chrome.storage.local.set({ accessToken: session.access_token, captureToken: grant.access_token, expiresAt: session.expires_at, user: session.user, tenant: session.tenant, deviceId: grant.device_id });
  await chrome.storage.local.remove(["loginChallengeId", "pairingId", "pairingVerifier"]);
  return { paired: true };
}

async function requirePairedState(): Promise<Record<string, unknown>> {
  const state = await storageState();
  if (typeof state.captureToken !== "string") throw new Error("pairing_required");
  return state;
}

async function submitCapture(state: Record<string, unknown>, page: PageCapture) {
  const payload = await captureRequest(page);
  const captureRef = await sha256Hex(`${page.platform}:${page.platform_id}:${payload.content_hash}`);
  const idempotency = state.pendingCaptureRef === captureRef && typeof state.pendingCaptureKey === "string"
    ? state.pendingCaptureKey
    : crypto.randomUUID();
  await chrome.storage.local.set({ pendingCaptureRef: captureRef, pendingCaptureKey: idempotency });
  try {
    const result = await requestJson<{ lifecycle: string; status: string; item_public_id: string }>(API_ORIGIN, "/api/v1/browser-companion/extension/captures", {
      method: "POST",
      headers: { "Authorization": `Bearer ${state.captureToken}`, "Content-Type": "application/json", "Idempotency-Key": idempotency },
      body: JSON.stringify(payload),
    }, CAPTURE_REQUEST_TIMEOUT_MS);
    await chrome.storage.local.remove(["pendingCaptureRef", "pendingCaptureKey"]);
    return result;
  } catch (error) {
    if (error instanceof Error && ["queue_unavailable", "capture_upload_failed", "capture_conflict"].includes(error.message)) {
      await chrome.storage.local.remove(["pendingCaptureRef", "pendingCaptureKey"]);
    }
    throw error;
  }
}

async function capture() {
  const state = await requirePairedState();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("active_tab_missing");
  const page = await captureActivePage(tab.id, tab.url);
  return submitCapture(state, page);
}

/** Submits a page the side panel already read; the tab is not read again. */
async function saveCapture(page: PageCapture) {
  const state = await requirePairedState();
  return submitCapture(state, page);
}

async function tabUrl(tabId: number): Promise<string> {
  let tab: { url?: string } | undefined;
  try { tab = await chrome.tabs.get(tabId); } catch { throw new Error("active_tab_missing"); }
  // Without host permission for the page Chrome omits the URL, so it is not a supported media page.
  if (!tab?.url) throw new Error("unsupported_page");
  return tab.url;
}

/** Reads the tab's captions and metadata without submitting anything. */
async function readPage(tabId: number): Promise<PageCapture> {
  return captureActivePage(tabId, await tabUrl(tabId));
}

async function playback(tabId: number) {
  return { playback_sec: await readPlaybackSeconds(tabId, await tabUrl(tabId)) };
}

async function seek(tabId: number, startSec: number) {
  await seekPlayback(tabId, await tabUrl(tabId), startSec);
  return { seeked: true };
}

async function disconnect() {
  const state = await storageState();
  if (typeof state.captureToken === "string") {
    try {
      await requestJson(API_ORIGIN, "/api/v1/browser-companion/extension/grant", {
        method: "DELETE",
        headers: { Authorization: `Bearer ${state.captureToken}` },
      });
    } catch { /* Local credential deletion still stops this installation. */ }
  }
  if (typeof state.accessToken === "string") {
    try {
      await requestJson(API_ORIGIN, "/api/v1/extension/auth/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${state.accessToken}` },
      });
    } catch { /* Local credential deletion still stops this installation. */ }
  }
  await chrome.storage.local.remove(STORAGE_KEYS);
  return { paired: false };
}

async function library() {
  const state = await storageState();
  if (typeof state.accessToken !== "string") throw new Error("login_required");
  return requestJson(API_ORIGIN, "/api/v1/library/items?page=1&page_size=8", {
    headers: { Authorization: `Bearer ${state.accessToken}` },
  });
}

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  const command = parseCommand(message);
  if (typeof command === "string") {
    respond({ ok: false, error: command });
    return false;
  }
  if (command.type === "OPEN_WORKSPACE") {
    void chrome.tabs.create({ url: chrome.runtime.getURL("workspace.html#/chat") }).then(() => respond({ ok: true, result: { opened: true } }));
    return true;
  }
  const operation = command.type === "STATUS" ? status()
      : command.type === "START_LOGIN" ? startLogin(command.email)
      : command.type === "FINISH_LOGIN" ? finishLogin(command.email, command.code)
      : command.type === "START_PAIRING" ? startPairing(command.email)
      : command.type === "FINISH_PAIRING" ? finishPairing(command.email, command.code)
        : command.type === "CAPTURE" ? capture()
          : command.type === "LIBRARY" ? library()
          : command.type === "DISCONNECT" ? disconnect()
            : command.type === "READ_PAGE" ? readPage(command.tabId)
            : command.type === "PLAYBACK" ? playback(command.tabId)
            : command.type === "SEEK" ? seek(command.tabId, command.start_sec)
            : command.type === "SAVE_CAPTURE" ? saveCapture(command.page)
            : Promise.reject(new Error("unknown_command"));
  void operation.then((result) => respond({ ok: true, result })).catch((error: unknown) => respond({ ok: false, error: error instanceof Error ? error.message : "request_failed" }));
  return true;
});
