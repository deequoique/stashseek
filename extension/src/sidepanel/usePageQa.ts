import { useCallback, useEffect, useRef, useState } from "react";

import type { ConversationCitation, PageQaMessageInput } from "../api/contracts";
import type { PageCapture, Platform } from "../protocol";
import type { CollectEntry } from "./collectModel";
import { askFailure, errorCode } from "./copy";
import type { PanelDeps, PanelTab } from "./deps";
import { captureIdentityKey, identifyPage, type PageIdentity } from "./pageIdentity";
import { useCollect } from "./useCollect";

export type ReadStatus = "idle" | "reading" | "ready" | "error";

/** What the client read before asking, shown as the first step of the turn. Counts only, never caption text. */
export interface QaReadInfo {
  captioned: boolean;
  segments: number;
  language: string | null;
}

export interface QaTurn {
  kind: "qa";
  id: string;
  question: string;
  answer: string;
  citations: ConversationCitation[];
  status: "streaming" | "done" | "failed" | "stopped";
  /** The server answered but found nothing in the transcript. */
  notFound: boolean;
  error: string | null;
  reading: QaReadInfo;
  /** Playback position sent with the question: `undefined` while being read, null when unavailable. */
  playback: number | null | undefined;
}

/** The panel thread: Q&A turns and actions such as 「收藏视频」, in the order they happened. */
export type ThreadEntry = QaTurn | CollectEntry;

/** Everything held in panel memory for one video. A new video starts a new session and thread. */
export interface VideoSession {
  key: string;
  platform: Platform;
  conversationId: string;
  readStatus: ReadStatus;
  /** The captions read for this video; reused by every follow-up and by save. Never persisted. */
  page: PageCapture | null;
  readError: string | null;
  thread: ThreadEntry[];
  asking: boolean;
}

export interface PageQaController {
  /** `undefined` until the first tab report; `null` when there is no readable tab. */
  tab: PanelTab | null | undefined;
  /** The session for the video in the active tab, or null on an unsupported page. */
  session: VideoSession | null;
  notice: string | null;
  ask(question: string): void;
  retry(turnId: string): void;
  stop(): void;
  reread(): void;
  /** 「收藏视频」: save the current video to the library by the best available path. */
  collect(): void;
  openCitation(citation: ConversationCitation): Promise<void>;
}

/** Player not loaded yet: worth reading again shortly. */
const RETRYABLE_READ_ERRORS = new Set(["stale_player_response", "kaltura_entry_missing", "caption_player_not_ready"]);
const MAX_READ_RETRIES = 3;

function emptySession(identity: PageIdentity, conversationId: string): VideoSession {
  return {
    key: identity.key,
    platform: identity.platform,
    conversationId,
    readStatus: "idle",
    page: null,
    readError: null,
    thread: [],
    asking: false,
  };
}

export function buildPageQuestion(
  messageId: string,
  question: string,
  playbackSec: number | null,
  page: PageCapture,
): PageQaMessageInput {
  return {
    message_id: messageId,
    question,
    playback_sec: playbackSec,
    page: {
      platform: page.platform,
      platform_id: page.platform_id,
      canonical_url: page.canonical_url,
      page_url: page.page_url,
      metadata: page.metadata,
      caption: page.caption,
    },
  };
}

/** Why the server cannot answer about this page, or null when a question may be asked. */
export function questionBlock(page: PageCapture): string | null {
  if (page.caption.status === "available") return null;
  if (page.platform !== "youtube" && page.platform !== "bilibili") return "该视频没有可用字幕，暂时无法提问";
  const { title, description, chapters } = page.metadata;
  return title || description || chapters.length > 0 ? null : "该视频没有字幕，也没有可用的标题和简介，暂时无法提问";
}

function isHttps(url: string): boolean {
  try { return new URL(url).protocol === "https:"; } catch { return false; }
}

export interface PairingState {
  /** The capture device grant exists, so captions can be saved from the browser. */
  paired: boolean;
  /** The grant was rejected while saving; the panel should treat the device as not connected. */
  onGrantRejected: () => void;
}

const NOT_PAIRED: PairingState = { paired: false, onGrantRejected: () => undefined };

export function usePageQa(deps: PanelDeps, onSessionInvalid: () => void, pairing: PairingState = NOT_PAIRED): PageQaController {
  const depsRef = useRef(deps);
  depsRef.current = deps;
  const invalidRef = useRef(onSessionInvalid);
  invalidRef.current = onSessionInvalid;
  const pairedRef = useRef(pairing.paired);
  pairedRef.current = pairing.paired;
  const grantRejectedRef = useRef(pairing.onGrantRejected);
  grantRejectedRef.current = pairing.onGrantRejected;

  const [tab, setTab] = useState<PanelTab | null | undefined>(undefined);
  const [session, setSession] = useState<VideoSession | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const tabRef = useRef<PanelTab | null | undefined>(undefined);
  const sessionRef = useRef<VideoSession | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const readTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const readWaitersRef = useRef<Array<{ conversationId: string; resolve: (page: PageCapture | null) => void }>>([]);

  /** Answers every pending `ensureRead` whose read has finished, or whose video is no longer the current one. */
  const settleReadWaiters = useCallback((abandon?: string) => {
    const current = sessionRef.current;
    readWaitersRef.current = readWaitersRef.current.filter((waiter) => {
      if (!current || current.conversationId !== waiter.conversationId || abandon === waiter.conversationId) {
        waiter.resolve(null);
        return false;
      }
      if (current.readStatus === "ready" && current.page) {
        waiter.resolve(current.page);
        return false;
      }
      if (current.readStatus === "error") {
        waiter.resolve(null);
        return false;
      }
      return true;
    });
  }, []);

  const replaceSession = useCallback((next: VideoSession | null) => {
    sessionRef.current = next;
    setSession(next);
    settleReadWaiters();
  }, [settleReadWaiters]);

  /** Applies a change to the session it was started for; the result of a superseded video is dropped. */
  const patchSession = useCallback((conversationId: string, change: (current: VideoSession) => VideoSession) => {
    const current = sessionRef.current;
    if (!current || current.conversationId !== conversationId) return;
    replaceSession(change(current));
  }, [replaceSession]);

  const patchTurn = useCallback((conversationId: string, turnId: string, change: (turn: QaTurn) => QaTurn) => {
    patchSession(conversationId, (current) => ({
      ...current,
      thread: current.thread.map((entry) => (entry.kind === "qa" && entry.id === turnId ? change(entry) : entry)),
    }));
  }, [patchSession]);

  const clearReadTimer = useCallback(() => {
    if (readTimerRef.current !== null) clearTimeout(readTimerRef.current);
    readTimerRef.current = null;
  }, []);

  const runRead = useCallback(async (conversationId: string, attempt: number): Promise<void> => {
    const current = sessionRef.current;
    if (!current || current.conversationId !== conversationId) return;
    const activeTab = tabRef.current;
    if (!activeTab || identifyPage(activeTab.url)?.key !== current.key) {
      // The video is not in view; the read resumes when its tab comes back.
      patchSession(conversationId, (value) => ({ ...value, readStatus: "idle" }));
      settleReadWaiters(conversationId);
      return;
    }
    patchSession(conversationId, (value) => ({ ...value, readStatus: "reading", readError: null }));
    try {
      const page = await depsRef.current.worker.readPage(activeTab.id);
      // A page read straight after an SPA navigation may still describe the previous video.
      const readKey = captureIdentityKey(page);
      if (readKey !== null && readKey !== current.key) throw new Error("stale_player_response");
      patchSession(conversationId, (value) => ({ ...value, readStatus: "ready", page, readError: null }));
    } catch (error) {
      const code = errorCode(error);
      if (RETRYABLE_READ_ERRORS.has(code) && attempt < MAX_READ_RETRIES) {
        readTimerRef.current = setTimeout(() => { void runRead(conversationId, attempt + 1); }, depsRef.current.retryDelayMs);
        return;
      }
      patchSession(conversationId, (value) => ({ ...value, readStatus: "error", readError: code }));
    }
  }, [patchSession, settleReadWaiters]);

  const scheduleRead = useCallback((conversationId: string, delayMs: number) => {
    clearReadTimer();
    readTimerRef.current = setTimeout(() => { void runRead(conversationId, 0); }, delayMs);
  }, [clearReadTimer, runRead]);

  /** The page read of a session, starting one if none is running; null when it fails, times out, or the video is left. */
  const ensureRead = useCallback((conversationId: string) => new Promise<PageCapture | null>((resolve) => {
    const current = sessionRef.current;
    if (!current || current.conversationId !== conversationId) { resolve(null); return; }
    if (current.readStatus === "ready" && current.page) { resolve(current.page); return; }
    if (current.readStatus === "error") { resolve(null); return; }
    const waiter = { conversationId, resolve: (page: PageCapture | null) => { clearTimeout(timer); resolve(page); } };
    const timer = setTimeout(() => {
      readWaitersRef.current = readWaitersRef.current.filter((candidate) => candidate !== waiter);
      resolve(null);
    }, depsRef.current.collectReadWaitMs);
    readWaitersRef.current.push(waiter);
    if (current.readStatus === "idle") scheduleRead(conversationId, 0);
  }), [scheduleRead]);

  const collector = useCollect({
    depsRef,
    sessionRef,
    patchSession,
    ensureRead,
    pairedRef,
    onGrantRejected: () => grantRejectedRef.current(),
    onSessionInvalid: () => invalidRef.current(),
    videoTabUrl: (value) => {
      const active = tabRef.current;
      return active?.url && identifyPage(active.url)?.key === value.key ? active.url : null;
    },
  });
  const { collect: runCollect, resume: resumeCollect, stopPolling: stopCollectPolling } = collector;

  const handleTab = useCallback((next: PanelTab | null) => {
    tabRef.current = next;
    setTab(next);
    const identity = identifyPage(next?.url);
    if (!identity) return; // The session is kept: coming back to the same video resumes its thread.
    const current = sessionRef.current;
    if (current?.key === identity.key) {
      if (current.readStatus === "idle") scheduleRead(current.conversationId, depsRef.current.settleDelayMs);
      return;
    }
    abortRef.current?.abort();
    stopCollectPolling();
    const fresh = emptySession(identity, depsRef.current.createConversationId());
    replaceSession(fresh);
    setNotice(null);
    scheduleRead(fresh.conversationId, depsRef.current.settleDelayMs);
    void resumeCollect(fresh.conversationId, identity.key);
  }, [replaceSession, resumeCollect, scheduleRead, stopCollectPolling]);

  useEffect(() => {
    let live = true;
    const unsubscribe = depsRef.current.tabs.subscribe((next) => { if (live) handleTab(next); });
    return () => {
      live = false;
      unsubscribe();
      clearReadTimer();
      abortRef.current?.abort();
    };
  }, [handleTab, clearReadTimer]);

  const ask = useCallback((rawQuestion: string) => {
    const question = rawQuestion.trim();
    const current = sessionRef.current;
    if (!question || !current || current.asking || current.readStatus !== "ready" || !current.page) return;
    if (questionBlock(current.page)) return;
    const { conversationId, page } = current;
    const turnId = depsRef.current.createId();
    const controller = new AbortController();
    abortRef.current = controller;
    setNotice(null);
    const captioned = page.caption.status === "available";
    replaceSession({
      ...current,
      asking: true,
      thread: [...current.thread, {
        kind: "qa",
        id: turnId,
        question,
        answer: "",
        citations: [],
        status: "streaming",
        notFound: false,
        error: null,
        reading: { captioned, segments: page.caption.cues.length, language: page.caption.language },
        playback: undefined,
      }],
    });

    void (async () => {
      let playbackSec: number | null = null;
      const activeTab = tabRef.current;
      if (activeTab && identifyPage(activeTab.url)?.key === current.key) {
        try { playbackSec = await depsRef.current.worker.playback(activeTab.id); } catch { /* The position only improves the context. */ }
      }
      patchTurn(conversationId, turnId, (turn) => ({ ...turn, playback: playbackSec }));
      try {
        const response = await depsRef.current.streamQuestion(
          conversationId,
          buildPageQuestion(turnId, question, playbackSec, page),
          { onTextDelta: (text) => patchTurn(conversationId, turnId, (turn) => ({ ...turn, answer: turn.answer + text })) },
          controller.signal,
        );
        patchTurn(conversationId, turnId, (turn) => ({
          ...turn,
          answer: response.text,
          citations: response.citations ?? [],
          status: "done",
          notFound: response.status === "not_found",
        }));
      } catch (error) {
        const failure = askFailure(error);
        // Streamed text is not durable until the server validates the answer, so a failed turn shows only the error.
        patchTurn(conversationId, turnId, (turn) => ({
          ...turn,
          answer: "",
          citations: [],
          status: failure.stopped ? "stopped" : "failed",
          error: failure.copy,
        }));
        if (failure.sessionInvalid) invalidRef.current();
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        patchSession(conversationId, (value) => ({ ...value, asking: false }));
      }
    })();
  }, [patchSession, patchTurn, replaceSession]);

  const retry = useCallback((turnId: string) => {
    const current = sessionRef.current;
    const turn = current?.thread.find((entry): entry is QaTurn => entry.kind === "qa" && entry.id === turnId);
    if (!current || !turn || current.asking || turn.status === "streaming") return;
    replaceSession({ ...current, thread: current.thread.filter((entry) => entry.kind !== "qa" || entry.id !== turnId) });
    ask(turn.question);
  }, [ask, replaceSession]);

  const stop = useCallback(() => { abortRef.current?.abort(); }, []);

  const reread = useCallback(() => {
    const current = sessionRef.current;
    if (current) scheduleRead(current.conversationId, 0);
  }, [scheduleRead]);

  const collect = useCallback(() => { void runCollect(); }, [runCollect]);

  const openCitation = useCallback(async (citation: ConversationCitation) => {
    const current = sessionRef.current;
    const startSec = citation.start_sec;
    let seeked = false;
    if (current && typeof startSec === "number" && Number.isFinite(startSec) && startSec >= 0) {
      let active: PanelTab | null = null;
      try { active = await depsRef.current.tabs.getActive(); } catch { /* Fall back to the link. */ }
      // Only seek the tab that still shows the video this answer was about.
      if (active && identifyPage(active.url)?.key === current.key) {
        try {
          await depsRef.current.worker.seek(active.id, startSec);
          seeked = true;
        } catch {
          setNotice("没能在页面里跳转，已改为在新标签页打开。");
        }
      }
    }
    if (!seeked && isHttps(citation.url)) {
      try { await depsRef.current.tabs.openUrl(citation.url); } catch { /* Nothing more to try. */ }
    }
  }, []);

  const visible = tab && identifyPage(tab.url) ? session : null;
  return { tab, session: visible, notice, ask, retry, stop, reread, collect, openCitation };
}
