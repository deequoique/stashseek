import { useCallback, useEffect, useRef, useState } from "react";

import type { ConversationCitation, PageQaMessageInput } from "../api/contracts";
import type { PageCapture, Platform } from "../protocol";
import { askFailure, errorCode, isGrantRejected } from "./copy";
import type { PanelDeps, PanelTab } from "./deps";
import { identifyPage, type PageIdentity } from "./pageIdentity";
import type { SaveResult } from "./workerApi";

export type ReadStatus = "idle" | "reading" | "ready" | "error";

export interface QaTurn {
  id: string;
  question: string;
  answer: string;
  citations: ConversationCitation[];
  status: "streaming" | "done" | "failed" | "stopped";
  /** The server answered but found nothing in the transcript. */
  notFound: boolean;
  error: string | null;
}

export type SaveState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved"; lifecycle: string }
  | { status: "error"; code: string };

/** Everything held in panel memory for one video. A new video starts a new session and thread. */
export interface VideoSession {
  key: string;
  platform: Platform;
  conversationId: string;
  readStatus: ReadStatus;
  /** The captions read for this video; reused by every follow-up and by save. Never persisted. */
  page: PageCapture | null;
  readError: string | null;
  turns: QaTurn[];
  asking: boolean;
  save: SaveState;
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
  save(): Promise<{ ok: true; result: SaveResult } | { ok: false; code: string }>;
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
    turns: [],
    asking: false,
    save: { status: "idle" },
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
  if (page.platform !== "youtube") return "该视频没有可用字幕，暂时无法提问";
  const { title, description, chapters } = page.metadata;
  return title || description || chapters.length > 0 ? null : "该视频没有字幕，也没有可用的标题和简介，暂时无法提问";
}

function isHttps(url: string): boolean {
  try { return new URL(url).protocol === "https:"; } catch { return false; }
}

export function usePageQa(deps: PanelDeps, onSessionInvalid: () => void): PageQaController {
  const depsRef = useRef(deps);
  depsRef.current = deps;
  const invalidRef = useRef(onSessionInvalid);
  invalidRef.current = onSessionInvalid;

  const [tab, setTab] = useState<PanelTab | null | undefined>(undefined);
  const [session, setSession] = useState<VideoSession | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const tabRef = useRef<PanelTab | null | undefined>(undefined);
  const sessionRef = useRef<VideoSession | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const readTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const replaceSession = useCallback((next: VideoSession | null) => {
    sessionRef.current = next;
    setSession(next);
  }, []);

  /** Applies a change to the session it was started for; the result of a superseded video is dropped. */
  const patchSession = useCallback((conversationId: string, change: (current: VideoSession) => VideoSession) => {
    const current = sessionRef.current;
    if (!current || current.conversationId !== conversationId) return;
    replaceSession(change(current));
  }, [replaceSession]);

  const patchTurn = useCallback((conversationId: string, turnId: string, change: (turn: QaTurn) => QaTurn) => {
    patchSession(conversationId, (current) => ({
      ...current,
      turns: current.turns.map((turn) => (turn.id === turnId ? change(turn) : turn)),
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
      return;
    }
    patchSession(conversationId, (value) => ({ ...value, readStatus: "reading", readError: null }));
    try {
      const page = await depsRef.current.worker.readPage(activeTab.id);
      // A page read straight after a YouTube navigation may still describe the previous video.
      if (current.platform === "youtube" && `youtube:${page.platform_id}` !== current.key) {
        throw new Error("stale_player_response");
      }
      patchSession(conversationId, (value) => ({ ...value, readStatus: "ready", page, readError: null }));
    } catch (error) {
      const code = errorCode(error);
      if (RETRYABLE_READ_ERRORS.has(code) && attempt < MAX_READ_RETRIES) {
        readTimerRef.current = setTimeout(() => { void runRead(conversationId, attempt + 1); }, depsRef.current.retryDelayMs);
        return;
      }
      patchSession(conversationId, (value) => ({ ...value, readStatus: "error", readError: code }));
    }
  }, [patchSession]);

  const scheduleRead = useCallback((conversationId: string, delayMs: number) => {
    clearReadTimer();
    readTimerRef.current = setTimeout(() => { void runRead(conversationId, 0); }, delayMs);
  }, [clearReadTimer, runRead]);

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
    const fresh = emptySession(identity, depsRef.current.createConversationId());
    replaceSession(fresh);
    setNotice(null);
    scheduleRead(fresh.conversationId, depsRef.current.settleDelayMs);
  }, [replaceSession, scheduleRead]);

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
    replaceSession({
      ...current,
      asking: true,
      turns: [...current.turns, { id: turnId, question, answer: "", citations: [], status: "streaming", notFound: false, error: null }],
    });

    void (async () => {
      let playbackSec: number | null = null;
      const activeTab = tabRef.current;
      if (activeTab && identifyPage(activeTab.url)?.key === current.key) {
        try { playbackSec = await depsRef.current.worker.playback(activeTab.id); } catch { /* The position only improves the context. */ }
      }
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
    const turn = current?.turns.find((entry) => entry.id === turnId);
    if (!current || !turn || current.asking || turn.status === "streaming") return;
    replaceSession({ ...current, turns: current.turns.filter((entry) => entry.id !== turnId) });
    ask(turn.question);
  }, [ask, replaceSession]);

  const stop = useCallback(() => { abortRef.current?.abort(); }, []);

  const reread = useCallback(() => {
    const current = sessionRef.current;
    if (current) scheduleRead(current.conversationId, 0);
  }, [scheduleRead]);

  const save = useCallback(async () => {
    const current = sessionRef.current;
    if (!current?.page) return { ok: false as const, code: "capture_unavailable" };
    const { conversationId, page } = current;
    patchSession(conversationId, (value) => ({ ...value, save: { status: "saving" } }));
    try {
      const result = await depsRef.current.worker.saveCapture(page);
      patchSession(conversationId, (value) => ({ ...value, save: { status: "saved", lifecycle: String(result.lifecycle ?? "queued") } }));
      return { ok: true as const, result };
    } catch (error) {
      const code = errorCode(error);
      patchSession(conversationId, (value) => ({ ...value, save: isGrantRejected(code) ? { status: "idle" } : { status: "error", code } }));
      return { ok: false as const, code };
    }
  }, [patchSession]);

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
  return { tab, session: visible, notice, ask, retry, stop, reread, save, openCitation };
}
