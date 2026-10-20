import { type MutableRefObject, useCallback, useEffect, useRef } from "react";

import { ApiError } from "../api/client";
import type { LibraryItem } from "../api/contracts";
import { bilibiliVideoRef } from "../page-capture";
import type { PageCapture } from "../protocol";
import {
  applyItem,
  type CollectEntry,
  type FallbackReason,
  finishEntry,
  newCollectEntry,
  parseStored,
  progressSignature,
  restartTracking,
  toStored,
} from "./collectModel";
import { errorCode, isGrantRejected } from "./copy";
import type { PanelDeps } from "./deps";
import { type Poller, startPolling } from "./itemPoller";
import type { VideoSession } from "./usePageQa";

/** Platforms the server can import from a plain video URL. Anything else has no fallback. */
const URL_IMPORT_PLATFORMS: ReadonlySet<string> = new Set(["youtube", "bilibili"]);

export interface CollectHost {
  depsRef: MutableRefObject<PanelDeps>;
  sessionRef: MutableRefObject<VideoSession | null>;
  patchSession(conversationId: string, change: (current: VideoSession) => VideoSession): void;
  /** The page read for the session, started when none is running; null when it could not be read. */
  ensureRead(conversationId: string): Promise<PageCapture | null>;
  /** The capture device grant exists. */
  pairedRef: MutableRefObject<boolean>;
  onGrantRejected(): void;
  onSessionInvalid(): void;
  /** URL of the active tab while it still shows the session's video. */
  videoTabUrl(session: VideoSession): string | null;
}

export interface CollectController {
  /** One click: read the page if needed, save through capture or URL import, then follow progress. */
  collect(): Promise<void>;
  /** Runs ingestion again on the item of a finished entry that the server lists a retry for, then follows it. */
  retry(entryId: string): Promise<void>;
  /** Picks up an unfinished action of this video from before the panel was reopened. */
  resume(conversationId: string, videoKey: string): Promise<void>;
  stopPolling(): void;
}

export function isActiveCollect(entry: { kind: string; phase?: string; retryState?: string }): boolean {
  return entry.kind === "collect" && (entry.phase !== "done" || entry.retryState === "pending");
}

export type CollectButtonState = "idle" | "saving" | "saved";

/**
 * What the 「收藏视频」 button is doing. The save is over as soon as the server has queued the item:
 * the processing that follows is shown by the card in the thread, so the button only says "saved"
 * while the item is followed, instead of staying "saving" until every stage has finished.
 */
export function collectButtonState(thread: readonly { kind: string; phase?: string; retryState?: string }[]): CollectButtonState {
  const active = thread.find(isActiveCollect);
  if (!active) return "idle";
  return active.phase === "tracking" ? "saved" : "saving";
}

function youtubeUrl(videoKey: string): string | null {
  const id = /^youtube:([A-Za-z0-9_-]{11})$/.exec(videoKey)?.[1];
  return id ? `https://www.youtube.com/watch?v=${id}` : null;
}

/**
 * The Bilibili part (`p`) the session is on. The server connector only imports part 1, so a later part
 * can be saved from its captions but never by URL. Non-Bilibili videos are part 1.
 */
function bilibiliPart(session: VideoSession, page: PageCapture | null, tabUrl: string | null): number {
  if (session.platform !== "bilibili") return 1;
  const url = tabUrl ?? page?.page_url ?? null;
  const ref = url ? bilibiliVideoRef(url) : null;
  if (ref) return ref.part;
  const fromKey = /^bilibili:[^:]+:p([1-9][0-9]*)$/.exec(session.key)?.[1];
  return fromKey ? Number(fromKey) : 1;
}

/** The URL handed to the server's batch import, or null when the server cannot import this platform. */
function importUrl(session: VideoSession, page: PageCapture | null, tabUrl: string | null): string | null {
  const platform: string = page?.platform ?? session.platform;
  if (!URL_IMPORT_PLATFORMS.has(platform)) return null;
  return page?.canonical_url ?? youtubeUrl(session.key) ?? tabUrl;
}

export function useCollect(host: CollectHost): CollectController {
  const hostRef = useRef(host);
  hostRef.current = host;
  const pollerRef = useRef<Poller | null>(null);
  const aliveRef = useRef(true);

  const stopPolling = useCallback(() => {
    pollerRef.current?.stop();
    pollerRef.current = null;
  }, []);

  const readEntry = useCallback((conversationId: string, entryId: string): CollectEntry | null => {
    const session = hostRef.current.sessionRef.current;
    if (!session || session.conversationId !== conversationId) return null;
    const entry = session.thread.find((candidate) => candidate.kind === "collect" && candidate.id === entryId);
    return entry?.kind === "collect" ? entry : null;
  }, []);

  const patchEntry = useCallback((conversationId: string, entryId: string, change: (entry: CollectEntry) => CollectEntry) => {
    hostRef.current.patchSession(conversationId, (session) => ({
      ...session,
      thread: session.thread.map((entry) => (entry.kind === "collect" && entry.id === entryId ? change(entry) : entry)),
    }));
  }, []);

  /** Polls the item until it ends, the cap passes, or the entry goes away. */
  const track = useCallback((conversationId: string, entryId: string) => {
    stopPolling();
    const entry = readEntry(conversationId, entryId);
    const itemId = entry?.itemPublicId;
    if (!entry || !itemId || !aliveRef.current) return;
    const deps = () => hostRef.current.depsRef.current;
    const settle = (videoKey: string) => { void deps().collectStore.clear(videoKey); };

    pollerRef.current = startPolling<LibraryItem>({
      fetch: () => deps().getItem(itemId),
      onResult: (item) => {
        const current = readEntry(conversationId, entryId);
        if (!current) return "stop";
        const next = applyItem(current, item, deps().now());
        patchEntry(conversationId, entryId, () => next);
        if (next.phase === "done") settle(next.videoKey);
        else if (progressSignature(next) !== progressSignature(current)) void deps().collectStore.save(next.videoKey, toStored(next));
        return next.phase === "done" ? "stop" : "continue";
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 401) {
          hostRef.current.onSessionInvalid();
          return "stop";
        }
        if (error instanceof ApiError && error.status === 404) {
          patchEntry(conversationId, entryId, (current) => finishEntry(current, "failed", "item_missing"));
          settle(entry.videoKey);
          return "stop";
        }
        patchEntry(conversationId, entryId, (current) => ({ ...current, retrying: true }));
        return "continue";
      },
      onDeadline: () => {
        patchEntry(conversationId, entryId, (current) => finishEntry(current, "timeout"));
        settle(entry.videoKey);
      },
      intervalMs: deps().pollIntervalMs,
      backoffMs: deps().pollBackoffMs,
      deadlineAt: entry.startedAt + deps().pollMaxMs,
      now: () => deps().now(),
      isVisible: () => deps().isVisible(),
    });
  }, [patchEntry, readEntry, stopPolling]);

  useEffect(() => {
    aliveRef.current = true;
    const onVisibility = () => {
      if (hostRef.current.depsRef.current.isVisible()) pollerRef.current?.nudge();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      aliveRef.current = false;
      document.removeEventListener("visibilitychange", onVisibility);
      stopPolling();
    };
  }, [stopPolling]);

  const collect = useCallback(async () => {
    const h = hostRef.current;
    const session = h.sessionRef.current;
    if (!session || session.thread.some(isActiveCollect)) return;
    const deps = h.depsRef.current;
    const { conversationId } = session;
    const entryId = deps.createId();
    const patch = (change: (entry: CollectEntry) => CollectEntry) => patchEntry(conversationId, entryId, change);
    const stillHere = () => h.sessionRef.current?.conversationId === conversationId;

    stopPolling();
    h.patchSession(conversationId, (current) => ({
      ...current,
      thread: [...current.thread, newCollectEntry(entryId, session.key, deps.now())],
    }));

    const page = await h.ensureRead(conversationId);
    if (!stillHere()) return;
    const captions = page?.caption.status === "available" ? page : null;
    const duration = page?.metadata.duration_sec ?? null;

    /** Follows a queued or already-present item. */
    const follow = (itemPublicId: string, existing: boolean) => {
      patch((entry) => ({ ...entry, phase: "tracking", itemPublicId, existing, durationSec: duration ?? entry.durationSec }));
      const tracked = readEntry(conversationId, entryId);
      if (tracked) void deps.collectStore.save(tracked.videoKey, toStored(tracked));
      track(conversationId, entryId);
    };

    let fallback: FallbackReason | null = null;
    if (!page) fallback = "read_failed";
    else if (!captions) fallback = "no_captions";
    else if (!h.pairedRef.current) fallback = "not_paired";

    if (captions && !fallback) {
      patch((entry) => ({
        ...entry,
        phase: "submitting",
        path: "capture",
        segments: captions.caption.cues.length,
        durationSec: duration,
      }));
      try {
        const result = await deps.worker.saveCapture(captions);
        if (!stillHere()) return;
        if (result.item_public_id) follow(result.item_public_id, result.status === "already_exists");
        else patch((entry) => finishEntry(entry, "submitted"));
        return;
      } catch (error) {
        if (!stillHere()) return;
        const code = errorCode(error);
        if (code === "quota_exceeded") { patch((entry) => finishEntry(entry, "quota_exceeded")); return; }
        if (code === "capture_conflict") { patch((entry) => finishEntry(entry, "submit_failed", code)); return; }
        if (isGrantRejected(code)) h.onGrantRejected();
        fallback = "capture_failed";
      }
    }

    // Server-side URL import: the browser captions are unavailable or could not be saved.
    const tabUrl = h.videoTabUrl(session);
    const url = importUrl(session, page, tabUrl);
    const unsupportedPart = bilibiliPart(session, page, tabUrl) > 1;
    patch((entry) => ({
      ...entry,
      phase: url && !unsupportedPart ? "submitting" : entry.phase,
      path: "url",
      fallback,
      captionless: page?.caption.status === "unavailable",
      durationSec: duration,
    }));
    if (unsupportedPart) {
      patch((entry) => finishEntry(entry, "unsupported_part"));
      return;
    }
    if (!url) {
      patch((entry) => finishEntry(entry, "unsupported_url"));
      return;
    }
    try {
      const response = await deps.submitBatch({ urls: [url] });
      if (!stillHere()) return;
      const result = response.results[0];
      if (!result) { patch((entry) => finishEntry(entry, "submit_failed", "request_failed")); return; }
      switch (result.status) {
        case "queued":
        case "already_exists":
          if (result.item_public_id) follow(result.item_public_id, result.status === "already_exists");
          else patch((entry) => finishEntry(entry, "submitted"));
          break;
        case "quota_exceeded":
          patch((entry) => finishEntry(entry, "quota_exceeded"));
          break;
        case "unsupported_url":
        case "invalid_url":
          patch((entry) => finishEntry(entry, "unsupported_url"));
          break;
        default:
          patch((entry) => finishEntry(entry, "submit_failed", result.safe_error_code ?? result.status));
      }
    } catch (error) {
      if (!stillHere()) return;
      if (error instanceof ApiError && error.status === 401) h.onSessionInvalid();
      const code = error instanceof ApiError ? (error.status === 401 ? "session_invalid" : "request_failed") : "network_unavailable";
      patch((entry) => finishEntry(entry, "submit_failed", code));
    }
  }, [patchEntry, readEntry, stopPolling, track]);

  const retry = useCallback(async (entryId: string) => {
    const h = hostRef.current;
    const session = h.sessionRef.current;
    const entry = session?.thread.find((candidate): candidate is CollectEntry => candidate.kind === "collect" && candidate.id === entryId);
    if (!session || !entry || entry.phase !== "done" || !entry.retryable || !entry.itemPublicId) return;
    if (session.thread.some(isActiveCollect)) return;
    const deps = h.depsRef.current;
    const { conversationId } = session;
    const patch = (change: (current: CollectEntry) => CollectEntry) => patchEntry(conversationId, entryId, change);
    const stillHere = () => h.sessionRef.current?.conversationId === conversationId;

    patch((current) => ({ ...current, retryState: "pending" }));
    try {
      const item = await deps.retryItem(entry.itemPublicId);
      if (!stillHere()) return;
      const started = deps.now();
      patch((current) => applyItem(restartTracking(current, started), item, started));
      const next = readEntry(conversationId, entryId);
      if (!next) return;
      if (next.phase === "done") {
        void deps.collectStore.clear(next.videoKey);
        return;
      }
      void deps.collectStore.save(next.videoKey, toStored(next));
      track(conversationId, entryId);
    } catch (error) {
      if (!stillHere()) return;
      if (error instanceof ApiError && error.status === 401) h.onSessionInvalid();
      const quota = error instanceof ApiError && error.status === 429;
      patch((current) => ({ ...current, retryState: quota ? "quota_exceeded" : "failed" }));
    }
  }, [patchEntry, readEntry, track]);

  const resume = useCallback(async (conversationId: string, videoKey: string) => {
    const h = hostRef.current;
    const deps = h.depsRef.current;
    let stored: CollectEntry | null;
    try { stored = parseStored(await deps.collectStore.load(videoKey)); } catch { return; }
    if (!stored || stored.videoKey !== videoKey || !aliveRef.current) return;
    const session = h.sessionRef.current;
    if (!session || session.conversationId !== conversationId) return;
    if (deps.now() - stored.startedAt >= deps.pollMaxMs) {
      void deps.collectStore.clear(videoKey);
      return;
    }
    if (session.thread.some(isActiveCollect)) return;
    const entry = stored;
    h.patchSession(conversationId, (current) => ({ ...current, thread: [...current.thread, entry] }));
    track(conversationId, entry.id);
  }, [track]);

  return { collect, retry, resume, stopPolling };
}
