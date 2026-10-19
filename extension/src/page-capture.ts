import type { Cue, PageCapture, Platform } from "./protocol.js";

type SafeCaptureError =
  | "unsupported_page"
  | "capture_unavailable"
  | "caption_fetch_failed"
  | "caption_parse_failed"
  | "caption_player_not_ready"
  | "stale_player_response";

export type CaptureAttempt =
  | { status: "captured"; capture: PageCapture }
  | { status: "no_caption"; capture: PageCapture }
  | { status: "failed"; error: SafeCaptureError };

type ScriptResult = { result?: unknown };

const SAFE_ERRORS = new Set<SafeCaptureError>([
  "unsupported_page",
  "capture_unavailable",
  "caption_fetch_failed",
  "caption_parse_failed",
  "caption_player_not_ready",
  "stale_player_response",
]);

function isSafeError(value: unknown): value is SafeCaptureError {
  return typeof value === "string" && SAFE_ERRORS.has(value as SafeCaptureError);
}

function isFiniteCue(value: unknown): value is Cue {
  if (!value || typeof value !== "object") return false;
  const cue = value as Record<string, unknown>;
  return typeof cue.text === "string"
    && cue.text.trim().length > 0
    && cue.text.length <= 10_000
    && typeof cue.start_sec === "number"
    && Number.isFinite(cue.start_sec)
    && cue.start_sec >= 0
    && typeof cue.end_sec === "number"
    && Number.isFinite(cue.end_sec)
    && cue.end_sec >= cue.start_sec;
}

function isHttpsUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function isPublicCaptureUrl(
  value: unknown,
  platform: PageCapture["platform"],
  platformId: string,
  field: "canonical" | "page",
): value is string {
  if (!isHttpsUrl(value)) return false;
  try {
    const parsed = new URL(value);
    if (parsed.hash || parsed.username || parsed.password) return false;
    if (platform === "youtube") {
      if (parsed.hostname !== "www.youtube.com") return false;
      let onlyVideoId = true;
      parsed.searchParams.forEach((_value, key) => { if (key !== "v") onlyVideoId = false; });
      return parsed.pathname === "/watch" && parsed.searchParams.get("v") === platformId && onlyVideoId;
    }
    if (platform === "bilibili") {
      if (parsed.hostname !== "www.bilibili.com" || parsed.pathname !== `/video/${platformId}`) return false;
      if (field === "canonical") return !parsed.search;
      // A page URL keeps only a part index above 1.
      return !parsed.search || /^\?p=(?:[2-9]|[1-9][0-9]{1,3})$/.test(parsed.search);
    }
    return false;
  } catch {
    return false;
  }
}

function isPageCapture(value: unknown): value is PageCapture {
  if (!value || typeof value !== "object") return false;
  const capture = value as Record<string, unknown>;
  if (capture.platform !== "youtube" && capture.platform !== "bilibili") return false;
  if (typeof capture.platform_id !== "string") return false;
  if (capture.platform === "youtube" && !/^[A-Za-z0-9_-]{11}$/.test(capture.platform_id)) return false;
  if (capture.platform === "bilibili" && !/^(?:BV[0-9A-Za-z]{10}|av[0-9]{1,20})$/.test(capture.platform_id)) return false;
  if (!isPublicCaptureUrl(capture.canonical_url, capture.platform, capture.platform_id, "canonical")
    || !isPublicCaptureUrl(capture.page_url, capture.platform, capture.platform_id, "page")) return false;

  const metadata = capture.metadata;
  if (!metadata || typeof metadata !== "object") return false;
  const metadataRecord = metadata as Record<string, unknown>;
  if (metadataRecord.title !== null && (typeof metadataRecord.title !== "string" || metadataRecord.title.length > 1_000)) return false;
  if (metadataRecord.author !== null && (typeof metadataRecord.author !== "string" || metadataRecord.author.length > 1_000)) return false;
  if (metadataRecord.duration_sec !== null
    && (typeof metadataRecord.duration_sec !== "number" || !Number.isInteger(metadataRecord.duration_sec)
      || metadataRecord.duration_sec < 0 || metadataRecord.duration_sec > 31_536_000)) return false;
  if (metadataRecord.language !== null && (typeof metadataRecord.language !== "string" || metadataRecord.language.length > 64)) return false;
  if (metadataRecord.description !== null && (typeof metadataRecord.description !== "string" || metadataRecord.description.length > 50_000)) return false;
  if (metadataRecord.cover_url !== null && !isHttpsUrl(metadataRecord.cover_url)) return false;
  if (metadataRecord.cover_url !== null) {
    try {
      const cover = new URL(metadataRecord.cover_url as string);
      const expectedHost = (capture.platform === "youtube" && cover.hostname === "i.ytimg.com")
        || (capture.platform === "bilibili" && (cover.hostname === "hdslb.com" || cover.hostname.endsWith(".hdslb.com")));
      if (!expectedHost || cover.search || cover.hash || cover.username || cover.password) return false;
    } catch {
      return false;
    }
  }
  if (!Array.isArray(metadataRecord.tags) || metadataRecord.tags.length > 100
    || !metadataRecord.tags.every((tag) => typeof tag === "string" && tag.trim().length > 0 && tag.length <= 200)) return false;
  if (!Array.isArray(metadataRecord.chapters) || metadataRecord.chapters.length > 1_000
    || !metadataRecord.chapters.every((value) => {
      if (!value || typeof value !== "object") return false;
      const chapter = value as Record<string, unknown>;
      return typeof chapter.title === "string" && chapter.title.trim().length > 0 && chapter.title.length <= 500
        && typeof chapter.start_sec === "number" && Number.isFinite(chapter.start_sec) && chapter.start_sec >= 0
        && (chapter.end_sec === null || (typeof chapter.end_sec === "number" && Number.isFinite(chapter.end_sec)
          && chapter.end_sec >= chapter.start_sec));
    })) return false;

  const caption = capture.caption;
  if (!caption || typeof caption !== "object") return false;
  const captionRecord = caption as Record<string, unknown>;
  if (!Array.isArray(captionRecord.cues) || captionRecord.cues.length > 50_000 || !captionRecord.cues.every(isFiniteCue)) return false;
  let priorStart = -1;
  let textLength = 0;
  for (const value of captionRecord.cues) {
    const cue = value as Cue;
    if (cue.start_sec < priorStart) return false;
    priorStart = cue.start_sec;
    textLength += cue.text.length;
    if (textLength > 1_000_000) return false;
  }
  if (captionRecord.status === "available") {
    return (captionRecord.source === "official_cc" || captionRecord.source === "auto_caption")
      && typeof captionRecord.language === "string"
      && captionRecord.language.trim().length > 0
      && captionRecord.cues.length > 0;
  }
  return captionRecord.status === "unavailable"
    && captionRecord.source === null
    && captionRecord.language === null
    && captionRecord.cues.length === 0;
}

/** Validates and rebuilds a capture so only the public capture.v1 fields survive. */
export function normalizePageCapture(value: unknown): PageCapture | null {
  try {
    const cloned = structuredClone(value);
    if (!isPageCapture(cloned)) return null;
    const metadata = cloned.metadata;
    const caption: PageCapture["caption"] = cloned.caption.status === "available"
      ? {
        status: "available",
        source: cloned.caption.source,
        language: cloned.caption.language,
        cues: cloned.caption.cues.map((cue) => ({ start_sec: cue.start_sec, end_sec: cue.end_sec, text: cue.text })),
      }
      : { status: "unavailable", source: null, language: null, cues: [] };
    return {
      platform: cloned.platform,
      platform_id: cloned.platform_id,
      canonical_url: cloned.canonical_url,
      page_url: cloned.page_url,
      metadata: {
        title: metadata.title,
        author: metadata.author,
        duration_sec: metadata.duration_sec,
        language: metadata.language,
        description: metadata.description,
        cover_url: metadata.cover_url,
        tags: [...metadata.tags],
        chapters: metadata.chapters.map((chapter) => ({
          title: chapter.title,
          start_sec: chapter.start_sec,
          end_sec: chapter.end_sec,
        })),
      },
      caption,
    };
  } catch {
    return null;
  }
}

function captureScore(capture: PageCapture): number {
  const title = capture.metadata.title?.trim().toLowerCase() ?? "";
  return (capture.caption.status === "available" ? 10_000 + capture.caption.cues.length : 0)
    + (capture.metadata.duration_sec !== null ? 200 : 0)
    + (title ? 50 : 0);
}

export function selectBestCapture(captures: PageCapture[]): PageCapture | undefined {
  return captures.map(normalizePageCapture).filter((capture): capture is PageCapture => capture !== null).reduce<PageCapture | undefined>((best, candidate) => (
    !best || captureScore(candidate) > captureScore(best)
      || (captureScore(candidate) === captureScore(best) && JSON.stringify(candidate) < JSON.stringify(best)) ? candidate : best
  ), undefined);
}

function normalizeAttempt(value: unknown): CaptureAttempt | null {
  try {
    const directCapture = normalizePageCapture(value);
    if (directCapture) {
      return { status: directCapture.caption.status === "available" ? "captured" : "no_caption", capture: directCapture };
    }
    if (!value || typeof value !== "object") return null;
    const attempt = structuredClone(value) as Record<string, unknown>;
    if (attempt.status === "captured" || attempt.status === "no_caption") {
      const capture = normalizePageCapture(attempt.capture);
      if (!capture) return null;
      if (attempt.status === "captured" && capture.caption.status !== "available") return null;
      if (attempt.status === "no_caption" && capture.caption.status !== "unavailable") return null;
      return { status: attempt.status, capture };
    }
    if (attempt.status === "failed" && isSafeError(attempt.error)) return { status: "failed", error: attempt.error };
    return null;
  } catch {
    return null;
  }
}

function aggregateAttempts(values: unknown[]): PageCapture {
  const attempts = values.map(normalizeAttempt).filter((attempt): attempt is CaptureAttempt => attempt !== null);
  const captures = attempts.flatMap((attempt) => (attempt.status === "captured" || attempt.status === "no_caption") ? [attempt.capture] : []);
  const selected = selectBestCapture(captures);
  if (selected?.caption.status === "available") return selected;

  const failures = attempts.flatMap((attempt) => attempt.status === "failed" ? [attempt.error] : []);
  const retryableFailure = failures.find((error) => error === "caption_fetch_failed" || error === "caption_parse_failed");
  if (retryableFailure) throw new Error(retryableFailure);
  if (failures.includes("stale_player_response")) throw new Error("stale_player_response");
  if (selected) return selected;
  throw new Error(failures[0] ?? "capture_unavailable");
}

/** Maps a tab URL to the supported media adapter, or null for any other page. */
export function classifyPageUrl(url: string): Platform | null {
  let parsed: URL;
  try { parsed = new URL(url); } catch { return null; }
  if (parsed.protocol !== "https:") return null;

  const host = parsed.hostname.toLowerCase();
  const youtubeHost = host === "www.youtube.com" || host === "youtube.com" || host === "youtu.be";
  const youtubeShape = host === "youtu.be"
    ? /^\/[A-Za-z0-9_-]{11}(?:\/)?$/.test(parsed.pathname)
    : (parsed.pathname === "/watch" && /^[A-Za-z0-9_-]{11}$/.test(parsed.searchParams.get("v") ?? ""))
      || /^\/shorts\/[A-Za-z0-9_-]{11}(?:\/)?$/.test(parsed.pathname);
  if (youtubeHost && youtubeShape) return "youtube";
  return bilibiliVideoRef(url) ? "bilibili" : null;
}

/**
 * The normalized id (`BV…` or `av…`, as the server connector derives it) and the
 * 1-based part of a Bilibili video page URL, or null for any other URL. An absent
 * or malformed `p` means part 1, as on Bilibili itself.
 */
export function bilibiliVideoRef(url: string): { id: string; part: number } | null {
  let parsed: URL;
  try { parsed = new URL(url); } catch { return null; }
  if (parsed.protocol !== "https:" || parsed.hostname.toLowerCase() !== "www.bilibili.com") return null;
  const match = /^\/video\/((?:[aA][vV])[0-9]{1,20}|(?:[bB][vV])[0-9A-Za-z]{10})\/?$/.exec(parsed.pathname);
  if (!match) return null;
  const raw = match[1]!;
  const id = (raw.slice(0, 2).toLowerCase() === "bv" ? "BV" : "av") + raw.slice(2);
  const values = parsed.searchParams.getAll("p");
  const part = values.length === 1 && /^[1-9][0-9]{0,3}$/.test(values[0]!) ? Number(values[0]) : 1;
  return { id, part };
}

export async function captureActivePage(tabId: number, url: string): Promise<PageCapture> {
  const kind = classifyPageUrl(url);
  if (!kind) throw new Error("unsupported_page");
  const injection = kind === "youtube" ? captureYouTubePage : captureBilibiliPage;

  // YouTube's and Bilibili's players and page state live in the top frame.
  let results: unknown[];
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      world: "MAIN",
      func: injection,
    }) as unknown as unknown[];
  } catch {
    throw new Error("capture_unavailable");
  }
  const values = results.flatMap((value) => {
    try {
      if (!value || typeof value !== "object") return [];
      return [(value as ScriptResult).result];
    } catch {
      return [];
    }
  });
  return aggregateAttempts(values);
}

/** MAIN-world YouTube adapter; keep this function self-contained for MV3. */
export async function captureYouTubePage(): Promise<CaptureAttempt> {
  type PlayerResponse = Record<string, unknown>;
  const clean = (value: string) => value
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .replace(/\s+([,.;:!?])/g, "$1")
    .trim();
  const fail = (error: SafeCaptureError): CaptureAttempt => ({ status: "failed", error });
  try {
    const currentUrl = new URL(location.href);
    const id = currentUrl.hostname === "youtu.be"
      ? currentUrl.pathname.split("/").filter(Boolean)[0] ?? ""
      : currentUrl.searchParams.get("v") ?? currentUrl.pathname.split("/").filter(Boolean).at(-1) ?? "";
    if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return fail("unsupported_page");
    const captureDeadline = Date.now() + 10_000;
    const responseControllers = new WeakMap<Response, AbortController>();
    const abortResponse = (response: Response): void => {
      try { responseControllers.get(response)?.abort(); } catch { /* A test/page response may not be weak-map compatible. */ }
    };
    const fetchBeforeDeadline = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const remaining = captureDeadline - Date.now();
      if (remaining <= 0) throw new Error("caption_fetch_timeout");
      const controller = typeof AbortController === "function" ? new AbortController() : undefined;
      const requestInit = controller ? { ...(init ?? {}), signal: controller.signal } : init;
      let timedOut = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const response = await Promise.race<Response>([
          fetch(input, requestInit),
          new Promise<Response>((_resolve, reject) => {
            timer = setTimeout(() => {
              timedOut = true;
              try { controller?.abort(); } catch { /* AbortController is best effort in page shims. */ }
              reject(new Error("caption_fetch_timeout"));
            }, Math.min(1_200, remaining));
          }),
        ]);
        if (controller) responseControllers.set(response, controller);
        return response;
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        if (timedOut) {
          try { controller?.abort(); } catch { /* Already aborted or unavailable. */ }
        }
      }
    };
    const readResponseBeforeDeadline = async <T>(
      reader: () => Promise<T>,
      onTimeout?: () => void,
    ): Promise<T> => {
      const remaining = captureDeadline - Date.now();
      if (remaining <= 0) throw new Error("caption_body_timeout");
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        return await Promise.race<T>([
          Promise.resolve().then(reader),
          new Promise<T>((_resolve, reject) => {
            timer = setTimeout(
              () => {
                try { onTimeout?.(); } catch { /* Cleanup is best effort. */ }
                reject(new Error("caption_body_timeout"));
              },
              Math.min(1_200, remaining),
            );
          }),
        ]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
      }
    };
    const MAX_YOUTUBE_RESPONSE_BYTES = 4_000_000;
    const readResponseText = async (response: Response): Promise<string> => {
      const stream = response.body;
      if (stream && typeof stream.getReader === "function") {
        const streamReader = stream.getReader();
        const chunks: Uint8Array[] = [];
        let totalBytes = 0;
        const cancelReader = (): void => {
          try {
            const cancel = (streamReader as ReadableStreamDefaultReader<Uint8Array> & { cancel?: () => Promise<void> }).cancel;
            if (typeof cancel !== "function") return;
            const pending = cancel.call(streamReader);
            if (pending && typeof pending.then === "function") void pending.catch(() => undefined);
          } catch { /* A body can already be closed by the browser. */ }
        };
        try {
          while (true) {
            const next = await readResponseBeforeDeadline(
              () => streamReader.read(),
              () => {
                abortResponse(response);
                cancelReader();
              },
            );
            if (next.done) break;
            const chunk = next.value;
            if (!(chunk instanceof Uint8Array)) {
              abortResponse(response);
              cancelReader();
              throw new Error("caption_body_unavailable");
            }
            totalBytes += chunk.byteLength;
            if (totalBytes > MAX_YOUTUBE_RESPONSE_BYTES) {
              abortResponse(response);
              cancelReader();
              throw new Error("caption_body_too_large");
            }
            chunks.push(chunk);
          }
        } finally {
          try { streamReader.releaseLock(); } catch { /* A consumed body is already released. */ }
        }
        const bytes = new Uint8Array(totalBytes);
        let offset = 0;
        for (const chunk of chunks) {
          bytes.set(chunk, offset);
          offset += chunk.byteLength;
        }
        const text = typeof TextDecoder === "function"
          ? new TextDecoder().decode(bytes)
          : String.fromCharCode(...bytes);
        if (text.length > MAX_YOUTUBE_RESPONSE_BYTES) {
          abortResponse(response);
          cancelReader();
          throw new Error("caption_body_too_large");
        }
        return text;
      }
      const reader = response as Response & { text?: () => Promise<string> };
      if (typeof reader.text !== "function") throw new Error("caption_body_unavailable");
      const text = await readResponseBeforeDeadline(() => reader.text!(), () => abortResponse(response));
      if (text.length > MAX_YOUTUBE_RESPONSE_BYTES) {
        abortResponse(response);
        throw new Error("caption_body_too_large");
      }
      return text;
    };
    const readResponseJson = async (response: Response): Promise<unknown> => {
      const reader = response as Response & { text?: () => Promise<string>; json?: () => Promise<unknown> };
      // Real browser Responses are read as text first so a trusted endpoint
      // cannot hand an unbounded body straight to JSON.parse. The JSON-only
      // fallback keeps the page-world adapter compatible with lightweight
      // test doubles and older embedded player shims.
      if (typeof reader.text === "function"
        || (reader.body && typeof reader.body.getReader === "function")) {
        const text = await readResponseText(response);
        return JSON.parse(text) as unknown;
      }
      if (typeof reader.json === "function") {
        return readResponseBeforeDeadline(() => reader.json!(), () => abortResponse(response));
      }
      throw new Error("caption_body_unavailable");
    };
    const currentVideoId = (): string => {
      try {
        const current = new URL(location.href);
        return current.hostname === "youtu.be"
          ? current.pathname.split("/").filter(Boolean)[0] ?? ""
          : current.searchParams.get("v") ?? current.pathname.split("/").filter(Boolean).at(-1) ?? "";
      } catch {
        return "";
      }
    };
    const isCurrentVideo = (): boolean => currentVideoId() === id;

    const page = globalThis as typeof globalThis & {
      ytInitialPlayerResponse?: unknown;
      ytplayer?: { config?: unknown; getPlayerResponse?: () => unknown };
      ytPlayer?: { config?: unknown; getPlayerResponse?: () => unknown };
      movie_player?: { getPlayerResponse?: () => unknown };
      ytdPlayer?: { getPlayerResponse?: () => unknown };
      ytplayerConfig?: unknown;
      ytInitialData?: unknown;
      ytcfg?: { get?: (key: string) => unknown };
    };
    const responses: PlayerResponse[] = [];
    const addResponse = (value: unknown, depth = 0) => {
      if (depth > 2 || value === null || value === undefined) return;
      let parsed: unknown = value;
      if (typeof parsed === "string") {
        try { parsed = JSON.parse(parsed); } catch { return; }
      }
      if (!parsed || typeof parsed !== "object") return;
      const record = parsed as PlayerResponse;
      if (record.videoDetails || record.captions) responses.push(record);
      const args = record.args && typeof record.args === "object" ? record.args as Record<string, unknown> : null;
      const playerVars = record.PLAYER_VARS && typeof record.PLAYER_VARS === "object" ? record.PLAYER_VARS as Record<string, unknown> : null;
      for (const nested of [record.playerResponse, record.player_response, record.raw_player_response, args?.player_response, args?.playerResponse, args?.raw_player_response, playerVars?.player_response]) addResponse(nested, depth + 1);
    };
    const playerElement = (() => {
      try {
        if (typeof document.getElementById === "function") {
          const moviePlayer = document.getElementById("movie_player");
          if (moviePlayer) return moviePlayer;
        }
        if (typeof document.querySelector === "function") return document.querySelector("ytd-player");
      } catch { /* The player element may be replaced during navigation. */ }
      return null;
    })();
    const holders = [page.movie_player, page.ytdPlayer, page.ytplayer, page.ytPlayer, playerElement];
    const selectedTrackHints: PlayerResponse[] = [];
    for (const holder of holders) {
      try {
        const player = holder as { getPlayerResponse?: () => unknown; getOption?: (namespace: string, option: string) => unknown } | null;
        if (player && typeof player.getPlayerResponse === "function") addResponse(player.getPlayerResponse());
        if (player && typeof player.getOption === "function") {
          const selected = player.getOption("captions", "track");
          if (selected && typeof selected === "object") {
            selectedTrackHints.push(selected as PlayerResponse);
            const nested = (selected as PlayerResponse).track;
            if (nested && typeof nested === "object") selectedTrackHints.push(nested as PlayerResponse);
          }
        }
      } catch { /* Player can be replaced during SPA navigation. */ }
    }
    for (const holder of [page.ytplayer?.config, page.ytPlayer?.config, page.ytplayerConfig]) addResponse(holder);
    addResponse(page.ytInitialPlayerResponse);

    const responseId = (response: PlayerResponse): string | null => {
      const details = response.videoDetails;
      if (!details || typeof details !== "object") return null;
      const value = (details as Record<string, unknown>).videoId;
      return typeof value === "string" ? value : null;
    };
    const accepted = responses.filter((response) => {
      const candidateId = responseId(response);
      return candidateId === null || candidateId === id;
    });
    if (!responses.length) return fail("capture_unavailable");
    if (!accepted.length) return fail("stale_player_response");
    const identifiedResponses = responses.filter((candidate) => responseId(candidate) !== null);
    if (identifiedResponses.length && !identifiedResponses.some((candidate) => responseId(candidate) === id)) {
      return fail("stale_player_response");
    }
    const captionRenderer = (response: PlayerResponse): Record<string, unknown> | null => {
      const captions = response.captions;
      if (!captions || typeof captions !== "object") return null;
      const renderer = (captions as Record<string, unknown>).playerCaptionsTracklistRenderer;
      return renderer && typeof renderer === "object" ? renderer as Record<string, unknown> : null;
    };
    const trackList = (response: PlayerResponse): Array<Record<string, unknown>> => {
      const tracks = captionRenderer(response)?.captionTracks;
      return Array.isArray(tracks) ? tracks.filter((track): track is Record<string, unknown> => Boolean(track && typeof track === "object")) : [];
    };
    const exactResponses = accepted.filter((candidate) => responseId(candidate) === id);
    const responsePool = exactResponses.length ? exactResponses : accepted;
    const response = responsePool.find((candidate) => trackList(candidate).length > 0) ?? responsePool[0]!;
    const tracks = trackList(response);
    const details = response.videoDetails && typeof response.videoDetails === "object" ? response.videoDetails as Record<string, unknown> : {};
    const title = typeof details.title === "string"
      ? clean(details.title).slice(0, 1000)
      : clean(typeof document.title === "string" ? document.title.replace(/\s*-\s*YouTube\s*$/i, "") : "").slice(0, 1000) || null;
    const author = typeof details.author === "string" ? clean(details.author).slice(0, 1000) || null : null;
    const durationValue = Number(details.lengthSeconds);
    const duration = Number.isFinite(durationValue) && durationValue >= 0 ? Math.floor(durationValue) : null;
    // The description keeps its line structure; only control characters are dropped.
    const description = (() => {
      if (typeof details.shortDescription !== "string") return null;
      const text = Array.from(details.shortDescription.replace(/\r\n?/g, "\n"))
        .filter((character) => {
          const code = character.codePointAt(0) ?? 0;
          return code === 9 || code === 10 || (code >= 32 && code !== 127);
        })
        .join("")
        .trim()
        .slice(0, 50_000)
        .trim();
      return text || null;
    })();
    // Best-effort chapters from the player-bar markers. YouTube's SPA navigation
    // does not refresh `ytInitialData`, so it is only trusted while it still
    // describes this video; any other shape yields no chapters.
    const chapters = ((): PageCapture["metadata"]["chapters"] => {
      try {
        const data = page.ytInitialData;
        if (!data || typeof data !== "object") return [];
        const endpoint = (data as Record<string, unknown>).currentVideoEndpoint;
        const watchEndpoint = endpoint && typeof endpoint === "object"
          ? (endpoint as Record<string, unknown>).watchEndpoint
          : null;
        const dataVideoId = watchEndpoint && typeof watchEndpoint === "object"
          ? (watchEndpoint as Record<string, unknown>).videoId
          : null;
        if (dataVideoId !== id) return [];
        const markerLists: Array<{ key: string; items: unknown[] }> = [];
        const queue: unknown[] = [data];
        const seen = new Set<unknown>();
        for (let inspected = 0; inspected < queue.length && inspected < 50_000; inspected += 1) {
          const value = queue[inspected];
          if (!value || typeof value !== "object" || seen.has(value)) continue;
          seen.add(value);
          const record = value as Record<string, unknown>;
          if (Array.isArray(record.markersMap)) {
            for (const entry of record.markersMap) {
              if (!entry || typeof entry !== "object") continue;
              const marker = entry as Record<string, unknown>;
              const markerValue = marker.value && typeof marker.value === "object" ? marker.value as Record<string, unknown> : null;
              if (typeof marker.key === "string" && markerValue && Array.isArray(markerValue.chapters)) {
                markerLists.push({ key: marker.key, items: markerValue.chapters });
              }
            }
          }
          if (Array.isArray(value)) queue.push(...value);
          else queue.push(...Object.values(record));
        }
        const preferred = markerLists.find((list) => list.key === "DESCRIPTION_CHAPTERS")
          ?? markerLists.find((list) => list.key === "AUTO_CHAPTERS")
          ?? markerLists[0];
        if (!preferred) return [];
        const parsed: Array<{ title: string; start_sec: number }> = [];
        for (const item of preferred.items.slice(0, 2_000)) {
          const renderer = item && typeof item === "object" ? (item as Record<string, unknown>).chapterRenderer : null;
          if (!renderer || typeof renderer !== "object") continue;
          const fields = renderer as Record<string, unknown>;
          const titleObject = fields.title && typeof fields.title === "object" ? fields.title as Record<string, unknown> : {};
          const runs = Array.isArray(titleObject.runs) ? titleObject.runs as Array<Record<string, unknown> | null> : [];
          const rawTitle = typeof titleObject.simpleText === "string"
            ? titleObject.simpleText
            : runs.map((run) => typeof run?.text === "string" ? run.text : "").join("");
          const chapterTitle = clean(rawTitle).slice(0, 500).trim();
          const rawStart = fields.timeRangeStartMillis;
          const startMs = typeof rawStart === "number" ? rawStart
            : typeof rawStart === "string" && /^\d+$/.test(rawStart) ? Number(rawStart) : Number.NaN;
          if (!chapterTitle || !Number.isFinite(startMs) || startMs < 0) continue;
          parsed.push({ title: chapterTitle, start_sec: startMs / 1000 });
        }
        const ordered = parsed
          .sort((left, right) => left.start_sec - right.start_sec)
          .filter((chapter, index, all) => index === 0 || chapter.start_sec > all[index - 1]!.start_sec)
          .slice(0, 1_000);
        return ordered.map((chapter, index) => ({
          title: chapter.title,
          start_sec: chapter.start_sec,
          end_sec: ordered[index + 1]?.start_sec ?? (duration !== null && duration >= chapter.start_sec ? duration : null),
        }));
      } catch {
        return [];
      }
    })();
    const baseCapture = (caption: PageCapture["caption"]): PageCapture => ({
      platform: "youtube",
      platform_id: id,
      canonical_url: `https://www.youtube.com/watch?v=${id}`,
      page_url: `https://www.youtube.com/watch?v=${id}`,
      metadata: { title, author, duration_sec: duration, language: caption.status === "available" ? caption.language : null, description, cover_url: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`, tags: [], chapters },
      caption,
    });
    if (!tracks.length) return { status: "no_caption", capture: baseCapture({ status: "unavailable", source: null, language: null, cues: [] }) };

    const normalizeCues = (cues: Cue[]): Cue[] => cues
      .filter((cue) => Number.isFinite(cue.start_sec) && Number.isFinite(cue.end_sec)
        && cue.start_sec >= 0 && cue.end_sec >= cue.start_sec && clean(cue.text))
      .map((cue) => ({ start_sec: cue.start_sec, end_sec: cue.end_sec, text: clean(cue.text) }))
      .sort((left, right) => left.start_sec - right.start_sec);
    const transcriptRendererCues = (renderers: Array<Record<string, unknown>>): Cue[] => {
      const unique = new Map<string, Cue>();
      for (const renderer of renderers) {
        const startMs = Number(renderer.startMs);
        const endMs = Number(renderer.endMs);
        const snippet = renderer.snippet && typeof renderer.snippet === "object"
          ? renderer.snippet as Record<string, unknown>
          : {};
        const runs = Array.isArray(snippet.runs) ? snippet.runs as Array<Record<string, unknown>> : [];
        const text = clean(runs.map((run) => typeof run.text === "string" ? run.text : "").join("")
          || (typeof snippet.simpleText === "string" ? snippet.simpleText : ""));
        if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs < 0 || endMs < startMs || !text) continue;
        const cue = { start_sec: startMs / 1000, end_sec: endMs / 1000, text };
        unique.set(`${startMs}\u0000${endMs}\u0000${text}`, cue);
      }
      return normalizeCues([...unique.values()]);
    };
    const parseJson3 = (body: unknown): Cue[] => {
      const events = body && typeof body === "object" && Array.isArray((body as Record<string, unknown>).events)
        ? (body as Record<string, unknown>).events as Array<Record<string, unknown>>
        : [];
      return normalizeCues(events.flatMap((event) => {
        if (!event || typeof event !== "object") return [];
        const startMs = Number(event.tStartMs);
        const durationMs = Number(event.dDurationMs ?? 0);
        const segments = Array.isArray(event.segs) ? event.segs as Array<Record<string, unknown>> : [];
        const text = clean(segments.map((segment) => typeof segment.utf8 === "string" ? segment.utf8 : "").join(""));
        const start = startMs / 1000;
        const end = (startMs + durationMs) / 1000;
        return Number.isFinite(startMs) && Number.isFinite(durationMs) && startMs >= 0 && durationMs >= 0
          && Number.isFinite(end) && end >= start && text ? [{ start_sec: start, end_sec: end, text }] : [];
      }));
    };
    const parseClock = (value: string): number => {
      const parts = value.trim().replace(",", ".").split(":").map(Number);
      if (!parts.length || parts.some((part) => !Number.isFinite(part) || part < 0)) return Number.NaN;
      return parts.reduce((total, part) => total * 60 + part, 0);
    };
    const parseTimedText = (value: string): Cue[] => {
      const text = value.replace(/\r/g, "").trim();
      if (!text) return [];
      if (text.startsWith("{")) {
        try {
          const jsonCues = parseJson3(JSON.parse(text));
          if (jsonCues.length) return jsonCues;
        } catch { /* Continue with text formats. */ }
      }
      const blockCues = text.split(/\n{2,}/).flatMap((block) => {
        const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
        const timingIndex = lines.findIndex((line) => line.includes("-->"));
        if (timingIndex < 0) return [];
        const match = lines[timingIndex]!.match(/^([^\s]+)\s+-->\s+([^\s]+)/);
        if (!match) return [];
        const start = parseClock(match[1]!);
        const end = parseClock(match[2]!);
        const cueText = clean(lines.slice(timingIndex + 1).join(" "));
        return Number.isFinite(start) && Number.isFinite(end) && end >= start && cueText
          ? [{ start_sec: start, end_sec: end, text: cueText }]
          : [];
      });
      const xmlCues = Array.from(text.matchAll(/<text\b([^>]*)>([\s\S]*?)<\/text>/gi)).flatMap((match) => {
        const attributes = match[1] ?? "";
        const attribute = (name: string) => {
          const found = attributes.match(new RegExp(`${name}\\s*=\\s*["']([^"']+)["']`, "i"));
          return found?.[1] ?? "";
        };
        const start = Number(attribute("start"));
        const duration = Number(attribute("dur"));
        const end = start + duration;
        const cueText = clean(match[2] ?? "");
        return Number.isFinite(start) && Number.isFinite(duration) && start >= 0 && duration >= 0
          && Number.isFinite(end) && end >= start && cueText
          ? [{ start_sec: start, end_sec: end, text: cueText }]
          : [];
      });
      return normalizeCues([...blockCues, ...xmlCues]);
    };

    const isAuto = (track: Record<string, unknown>) => track.kind === "asr" || (typeof track.vssId === "string" && track.vssId.startsWith("a."));
    const sameTrack = (hint: PlayerResponse, track: PlayerResponse): boolean => {
      if (typeof hint.baseUrl === "string") return hint.baseUrl === track.baseUrl;
      if (typeof hint.vssId === "string") return hint.vssId === track.vssId;
      if (typeof hint.languageCode !== "string" || hint.languageCode !== track.languageCode) return false;
      return hint.kind === undefined || hint.kind === track.kind;
    };
    const preferredIndices: number[] = [];
    const addPreferredIndex = (value: unknown) => {
      const index = typeof value === "number" ? value
        : typeof value === "string" && /^\d+$/.test(value) ? Number(value) : Number.NaN;
      if (Number.isInteger(index) && index >= 0 && index < tracks.length && !preferredIndices.includes(index)) preferredIndices.push(index);
    };
    for (const hint of selectedTrackHints) addPreferredIndex(tracks.findIndex((track) => sameTrack(hint, track)));
    const renderer = captionRenderer(response);
    addPreferredIndex(renderer?.defaultCaptionTrackIndex);
    const audioTracks = Array.isArray(renderer?.audioTracks)
      ? renderer.audioTracks.filter((track): track is Record<string, unknown> => Boolean(track && typeof track === "object"))
      : [];
    const rawDefaultAudioIndex = renderer?.defaultAudioTrackIndex;
    const defaultAudioIndex = typeof rawDefaultAudioIndex === "number" ? rawDefaultAudioIndex
      : typeof rawDefaultAudioIndex === "string" && /^\d+$/.test(rawDefaultAudioIndex) ? Number(rawDefaultAudioIndex) : Number.NaN;
    if (Number.isInteger(defaultAudioIndex) && defaultAudioIndex >= 0 && defaultAudioIndex < audioTracks.length) {
      addPreferredIndex(audioTracks[defaultAudioIndex]?.defaultCaptionTrackIndex);
    }
    audioTracks.forEach((audioTrack) => addPreferredIndex(audioTrack.defaultCaptionTrackIndex));
    const fallbackIndices = tracks.map((_track, index) => index)
      .sort((left, right) => Number(isAuto(tracks[left]!)) - Number(isAuto(tracks[right]!)));
    const ordered = [...preferredIndices, ...fallbackIndices.filter((index) => !preferredIndices.includes(index))]
      .map((index) => tracks[index]!);
    const trustedCaptionResponse = (result: Response): boolean => {
      if (!result.ok) return false;
      if (!result.url) return true;
      try {
        const finalUrl = new URL(result.url);
        const finalHost = finalUrl.hostname.toLowerCase();
        const trustedFinalHost = finalHost === "youtube.com" || finalHost.endsWith(".youtube.com")
          || finalHost === "googlevideo.com" || finalHost.endsWith(".googlevideo.com");
        return finalUrl.protocol === "https:" && !finalUrl.username && !finalUrl.password && trustedFinalHost
          && /timedtext|caption|subtitle/i.test(finalUrl.pathname);
      } catch {
        return false;
      }
    };
    const captured = (track: Record<string, unknown>, cues: Cue[]): CaptureAttempt => {
      const languageValue = track.languageCode ?? track.languageName;
      const language = typeof languageValue === "string" && clean(languageValue).length > 0 ? clean(languageValue).slice(0, 64) : "und";
      return {
        status: "captured",
        capture: baseCapture({ status: "available", source: isAuto(track) ? "auto_caption" : "official_cc", language, cues }),
      };
    };
    // Caption URLs in the player response carry no proof-of-origin token (`pot`):
    // /api/timedtext answers them with 200 and an empty body. The player adds
    // `pot` and client parameters to its own timedtext requests, so lift them
    // from such a request for this video. The values stay inside this function:
    // they are applied to caption fetches and never returned, stored or logged.
    type PotParams = Array<[string, string]>;
    type CaptionPlayer = {
      getOption?: (namespace: string, option: string) => unknown;
      setOption?: (namespace: string, option: string, value?: unknown) => unknown;
      loadModule?: (name: string) => unknown;
      unloadModule?: (name: string) => unknown;
      toggleSubtitles?: () => unknown;
      toggleSubtitlesOn?: () => unknown;
    };
    const POT_CLIENT_KEYS = ["potc", "c", "cver", "cplayer", "cos", "cosver", "cplatform", "cbr", "cbrver", "cbrand", "xorb", "xobt", "xovt"];
    const MAX_POT_PARAM_CHARS = 2_048;
    const POT_WAIT_MS = 3_500;
    const POT_REAPPLY_AFTER_MS = 1_200;
    const potParamsFromUrl = (value: unknown): PotParams | null => {
      if (typeof value !== "string" || value.length > 16_384 || !value.includes("/api/timedtext")) return null;
      try {
        const url = new URL(value, currentUrl);
        const host = url.hostname.toLowerCase();
        if (url.protocol !== "https:" || url.username || url.password
          || !(host === "youtube.com" || host.endsWith(".youtube.com")) || url.pathname !== "/api/timedtext") return null;
        if (url.searchParams.get("v") !== id) return null;
        const pot = url.searchParams.get("pot");
        if (!pot || pot.length > MAX_POT_PARAM_CHARS) return null;
        const params: PotParams = [["pot", pot]];
        for (const key of POT_CLIENT_KEYS) {
          const found = url.searchParams.get(key);
          if (found !== null && found.length <= MAX_POT_PARAM_CHARS) params.push([key, found]);
        }
        return params;
      } catch {
        return null;
      }
    };
    const potFromResources = (): PotParams | null => {
      try {
        const timing = page.performance;
        if (!timing || typeof timing.getEntriesByType !== "function") return null;
        const entries = timing.getEntriesByType("resource");
        for (let index = entries.length - 1; index >= 0; index -= 1) {
          const params = potParamsFromUrl(entries[index]?.name);
          if (params) return params;
        }
      } catch { /* Resource timing is best effort. */ }
      return null;
    };
    const adShowing = (): boolean => {
      try {
        const element = typeof document.getElementById === "function" ? document.getElementById("movie_player") : null;
        return Boolean(element?.classList?.contains("ad-showing"));
      } catch {
        return false;
      }
    };
    const captionPlayer = ([playerElement, page.movie_player] as unknown[]).find(
      (holder): holder is CaptionPlayer => Boolean(holder) && typeof (holder as CaptionPlayer).setOption === "function",
    ) ?? null;
    const activeCaptionTrack = (player: CaptionPlayer): Record<string, unknown> | null => {
      try {
        const value = player.getOption?.("captions", "track");
        if (!value || typeof value !== "object") return null;
        const language = (value as Record<string, unknown>).languageCode;
        return typeof language === "string" && language.length > 0 ? value as Record<string, unknown> : null;
      } catch {
        return null;
      }
    };
    const pause = (milliseconds: number) => new Promise<void>((resolve) => { setTimeout(resolve, milliseconds); });
    // Briefly turns a caption track on so the player requests it, watches for that
    // request, then puts the viewer's caption state back (also works while paused).
    const discoverPotByEnablingCaptions = async (player: CaptionPlayer): Promise<PotParams | null> => {
      const previous = activeCaptionTrack(player);
      // With captions already on, ask for a different track so the player has to fetch again.
      const requested = (previous ? ordered.find((track) => track.languageCode !== previous.languageCode) : undefined)
        ?? ordered[0] ?? tracks[0]!;
      const requestedLanguage = typeof requested.languageCode === "string" && requested.languageCode.length > 0
        ? requested.languageCode
        : null;
      const trackOption = requestedLanguage
        ? { languageCode: requestedLanguage, ...(isAuto(requested) ? { kind: "asr" } : {}) }
        : null;
      if (!trackOption && typeof player.toggleSubtitlesOn !== "function") return null;
      const apply = (): void => {
        if (trackOption) player.setOption?.("captions", "track", trackOption);
        else player.toggleSubtitlesOn?.();
      };
      const seen: { params: PotParams | null } = { params: null };
      let observer: PerformanceObserver | null = null;
      let touched = false;
      try {
        const Observer = page.PerformanceObserver;
        if (typeof Observer === "function") {
          try {
            observer = new Observer((list) => {
              for (const entry of list.getEntries()) seen.params = potParamsFromUrl(entry.name) ?? seen.params;
            });
            observer.observe({ type: "resource" });
          } catch {
            observer = null;
          }
        }
        touched = true;
        try { player.loadModule?.("captions"); } catch { /* The module may already be loaded. */ }
        apply();
        const startedAt = Date.now();
        const waitUntil = Math.min(startedAt + POT_WAIT_MS, captureDeadline - 2_500);
        let reapplied = false;
        while (!seen.params && Date.now() < waitUntil && isCurrentVideo()) {
          await pause(100);
          seen.params = seen.params ?? potFromResources();
          if (!seen.params && !reapplied && Date.now() - startedAt >= POT_REAPPLY_AFTER_MS) {
            reapplied = true;
            try { apply(); } catch { /* Keep waiting for the first request. */ }
          }
        }
      } catch { /* Restore the caption state below whatever happened. */ } finally {
        try { observer?.disconnect(); } catch { /* Already disconnected. */ }
        if (touched) {
          try {
            if (previous) player.setOption?.("captions", "track", previous);
            else if (typeof player.unloadModule === "function") player.unloadModule("captions");
            else player.toggleSubtitles?.();
          } catch { /* Restoring the viewer's caption state is best effort. */ }
        }
      }
      return seen.params;
    };
    let potParams: PotParams | null = potFromResources();
    if (!potParams) {
      // Never touch the captions while an ad owns the player; a retry follows the ad.
      if (adShowing()) return fail("caption_player_not_ready");
      if (captionPlayer) potParams = await discoverPotByEnablingCaptions(captionPlayer);
    }
    if (!isCurrentVideo()) return fail("stale_player_response");
    const withPot = (url: URL): URL => {
      const next = new URL(url);
      for (const [key, value] of potParams ?? []) next.searchParams.set(key, value);
      return next;
    };

    const errors: SafeCaptureError[] = [];
    for (const track of ordered) {
      const rawUrl = typeof track.baseUrl === "string" ? track.baseUrl : "";
      let baseCaptionUrl: URL;
      try {
        baseCaptionUrl = new URL(rawUrl, currentUrl);
        const host = baseCaptionUrl.hostname.toLowerCase();
        const trustedHost = host === "youtube.com" || host.endsWith(".youtube.com") || host === "googlevideo.com" || host.endsWith(".googlevideo.com");
        if (baseCaptionUrl.protocol !== "https:" || baseCaptionUrl.username || baseCaptionUrl.password
          || !trustedHost || !/timedtext|caption|subtitle/i.test(baseCaptionUrl.pathname)) throw new Error("bad_url");
      } catch {
        errors.push("caption_fetch_failed");
        continue;
      }
      const jsonUrl = withPot(baseCaptionUrl);
      jsonUrl.searchParams.set("fmt", "json3");
      let jsonResponse: Response;
      try {
        jsonResponse = await fetchBeforeDeadline(jsonUrl.toString(), { credentials: "include" });
      } catch {
        errors.push("caption_fetch_failed");
        continue;
      }
      if (!trustedCaptionResponse(jsonResponse)) {
        errors.push("caption_fetch_failed");
        continue;
      }
      try {
        const cues = parseJson3(await readResponseJson(jsonResponse));
        if (cues.length) return isCurrentVideo() ? captured(track, cues) : fail("stale_player_response");
      } catch { /* Try text formats using fresh requests. */ }
      errors.push("caption_parse_failed");

      const vttUrl = withPot(baseCaptionUrl);
      vttUrl.searchParams.set("fmt", "vtt");
      const fallbackUrls = Array.from(new Set([vttUrl.toString(), withPot(baseCaptionUrl).toString()]));
      for (const fallbackUrl of fallbackUrls) {
        let fallbackResponse: Response;
        try {
          fallbackResponse = await fetchBeforeDeadline(fallbackUrl, { credentials: "include" });
        } catch {
          errors.push("caption_fetch_failed");
          continue;
        }
        if (!trustedCaptionResponse(fallbackResponse)) {
          errors.push("caption_fetch_failed");
          continue;
        }
        try {
          const cues = parseTimedText(await readResponseText(fallbackResponse));
          if (cues.length) return isCurrentVideo() ? captured(track, cues) : fail("stale_player_response");
        } catch { /* Continue to the next representation or track. */ }
        errors.push("caption_parse_failed");
      }
    }

    const findTranscriptCommand = (root: unknown): { params: string; clickTrackingParams: string | null } | null => {
      if (!root || typeof root !== "object") return null;
      const queue: unknown[] = [root];
      const seen = new Set<unknown>();
      for (let inspected = 0; queue.length && inspected < 50_000; inspected += 1) {
        const value = queue.shift();
        if (!value || typeof value !== "object" || seen.has(value)) continue;
        seen.add(value);
        const record = value as Record<string, unknown>;
        const endpoint = record.getTranscriptEndpoint;
        if (endpoint && typeof endpoint === "object") {
          const params = (endpoint as Record<string, unknown>).params;
          if (typeof params === "string" && params.length > 0 && params.length <= 2_000) {
            const clickTrackingParams = record.clickTrackingParams;
            return {
              params,
              clickTrackingParams: typeof clickTrackingParams === "string" && clickTrackingParams.length <= 2_000
                ? clickTrackingParams
                : null,
            };
          }
        }
        if (Array.isArray(value)) queue.push(...value);
        else queue.push(...Object.values(record));
      }
      return null;
    };
    const transcriptCommand = findTranscriptCommand(page.ytInitialData);
    const transcriptParams = transcriptCommand?.params ?? null;
    let decodedTranscriptParams: string | null = null;
    const transcriptParamsMatchCurrentVideo = (() => {
      if (!transcriptParams) return false;
      try {
        decodedTranscriptParams = decodeURIComponent(transcriptParams);
        const normalized = decodedTranscriptParams.replace(/-/g, "+").replace(/_/g, "/");
        const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
        const binary = atob(padded);
        const target = Array.from(id).map((character) => character.charCodeAt(0));
        const bytes = Array.from(binary, (character) => character.charCodeAt(0));
        const readVarint = (offset: number): { value: number; next: number } | null => {
          let value = 0;
          let shift = 0;
          let cursor = offset;
          while (cursor < bytes.length && shift <= 63) {
            const byte = bytes[cursor++]!;
            value += (byte & 0x7f) * 2 ** shift;
            if (!(byte & 0x80)) return { value, next: cursor };
            shift += 7;
          }
          return null;
        };
        const sameBytes = (start: number, end: number): boolean => {
          if (end - start !== target.length) return false;
          return target.every((byte, index) => bytes[start + index] === byte);
        };
        const containsDelimitedVideoId = (start: number, end: number, depth: number): boolean => {
          if (depth > 5) return false;
          let cursor = start;
          while (cursor < end) {
            const key = readVarint(cursor);
            if (!key || key.value < 8) return false;
            cursor = key.next;
            const wireType = key.value & 0x07;
            if (wireType === 0) {
              const value = readVarint(cursor);
              if (!value) return false;
              cursor = value.next;
              continue;
            }
            if (wireType === 1) {
              cursor += 8;
              if (cursor > end) return false;
              continue;
            }
            if (wireType === 2) {
              const length = readVarint(cursor);
              if (!length || length.value < 0 || length.value > end - length.next) return false;
              const valueStart = length.next;
              const valueEnd = valueStart + length.value;
              if (sameBytes(valueStart, valueEnd)) return true;
              if (containsDelimitedVideoId(valueStart, valueEnd, depth + 1)) return true;
              cursor = valueEnd;
              continue;
            }
            if (wireType === 5) {
              cursor += 4;
              if (cursor > end) return false;
              continue;
            }
            return false;
          }
          return false;
        };
        // The real endpoint uses a base64url-encoded protobuf. Require the
        // current ID as a complete length-delimited protobuf value (including
        // nested messages), rather than accepting an incidental substring.
        if (containsDelimitedVideoId(0, bytes.length, 0)) return true;
        // Keep the tiny marker form accepted by page-world test shims and old
        // embedded players, but bind it exactly instead of using substring
        // matching. Real YouTube params never rely on this compatibility path.
        return binary === `video:${id}`;
      } catch {
        return false;
      }
    })();
    const configGet = page.ytcfg && typeof page.ytcfg.get === "function" ? page.ytcfg.get.bind(page.ytcfg) : null;
    const apiKey = configGet?.("INNERTUBE_API_KEY");
    const context = configGet?.("INNERTUBE_CONTEXT");
    const clientName = configGet?.("INNERTUBE_CLIENT_NAME");
    const clientVersion = configGet?.("INNERTUBE_CLIENT_VERSION");
    if (isCurrentVideo() && transcriptParamsMatchCurrentVideo && typeof apiKey === "string" && apiKey.length > 0 && apiKey.length <= 256
      && context && typeof context === "object" && (typeof clientName === "string" || typeof clientName === "number")
      && typeof clientVersion === "string" && clientVersion.length > 0 && clientVersion.length <= 128) {
      const endpoint = new URL("/youtubei/v1/get_transcript", currentUrl.origin);
      endpoint.searchParams.set("prettyPrint", "false");
      endpoint.searchParams.set("key", apiKey);
      let transcriptResponse: Response | null = null;
      try {
        const requestContext = transcriptCommand?.clickTrackingParams
          ? { ...(context as Record<string, unknown>), clickTracking: { clickTrackingParams: transcriptCommand.clickTrackingParams } }
          : context;
        transcriptResponse = await fetchBeforeDeadline(endpoint.toString(), {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            "X-YouTube-Client-Name": String(clientName),
            "X-YouTube-Client-Version": clientVersion,
          },
          body: JSON.stringify({ context: requestContext, params: decodedTranscriptParams }),
        });
      } catch {
        errors.push("caption_fetch_failed");
      }
      if (transcriptResponse) {
        const validTranscriptResponse = (() => {
          if (!transcriptResponse?.ok) return false;
          if (!transcriptResponse.url) return true;
          try {
            const finalUrl = new URL(transcriptResponse.url);
            return finalUrl.origin === currentUrl.origin && finalUrl.pathname === "/youtubei/v1/get_transcript";
          } catch {
            return false;
          }
        })();
        if (!validTranscriptResponse) {
          errors.push("caption_fetch_failed");
        } else {
          try {
            const body = await readResponseJson(transcriptResponse);
            const renderers: Array<Record<string, unknown>> = [];
            const queue: unknown[] = [body];
            const seen = new Set<unknown>();
            for (let inspected = 0; queue.length && inspected < 100_000; inspected += 1) {
              const value = queue.shift();
              if (!value || typeof value !== "object" || seen.has(value)) continue;
              seen.add(value);
              const record = value as Record<string, unknown>;
              const renderer = record.transcriptSegmentRenderer;
              if (renderer && typeof renderer === "object") renderers.push(renderer as Record<string, unknown>);
              if (Array.isArray(value)) queue.push(...value);
              else queue.push(...Object.values(record));
            }
            const cues = transcriptRendererCues(renderers);
            if (cues.length) return isCurrentVideo()
              ? captured(ordered[0] ?? tracks[0]!, cues)
              : fail("stale_player_response");
            errors.push("caption_parse_failed");
          } catch {
            errors.push("caption_parse_failed");
          }
        }
      }
    }

    const renderedTranscriptCues = (): Cue[] => {
      try {
        if (typeof document.querySelectorAll !== "function") return [];
        const renderers = Array.from(document.querySelectorAll("ytd-transcript-segment-renderer")).flatMap((node) => {
          const data = (node as Element & { data?: unknown }).data;
          return data && typeof data === "object" ? [data as Record<string, unknown>] : [];
        });
        return transcriptRendererCues(renderers);
      } catch {
        return [];
      }
    };
    const clickNode = (selector: string): boolean => {
      try {
        if (typeof document.querySelector !== "function") return false;
        const candidates = typeof document.querySelectorAll === "function"
          ? Array.from(document.querySelectorAll(selector)) as Array<Element & { click?: () => void }>
          : [];
        const visible = candidates.find((candidate) => {
          try { return typeof candidate.getClientRects === "function" && candidate.getClientRects().length > 0; } catch { return false; }
        });
        const node = visible ?? candidates[0]
          ?? document.querySelector(selector) as (Element & { click?: () => void }) | null;
        if (!node || typeof node.click !== "function") return false;
        node.click();
        return true;
      } catch {
        return false;
      }
    };
    let domCues = renderedTranscriptCues();
    if (domCues.length) return isCurrentVideo() ? captured(ordered[0] ?? tracks[0]!, domCues) : fail("stale_player_response");
    const supportsTranscriptDom = typeof document.querySelector === "function"
      && typeof document.querySelectorAll === "function";
    if (!domCues.length && supportsTranscriptDom && isCurrentVideo()) {
      clickNode("#description-inline-expander #expand, ytd-text-inline-expander #expand, tp-yt-paper-button#expand");
      let transcriptUiRequested = false;
      for (let attempt = 0; attempt < 10 && Date.now() < captureDeadline && isCurrentVideo(); attempt += 1) {
        if (clickNode("ytd-video-description-transcript-section-renderer button")) {
          transcriptUiRequested = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      for (let attempt = 0; transcriptUiRequested && attempt < 40 && Date.now() < captureDeadline && isCurrentVideo(); attempt += 1) {
        domCues = renderedTranscriptCues();
        if (domCues.length) return isCurrentVideo() ? captured(ordered[0] ?? tracks[0]!, domCues) : fail("stale_player_response");
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    if (!isCurrentVideo()) return fail("stale_player_response");
    // Without a pot the timed-text endpoints return empty bodies, so a failure
    // here says nothing about the captions: the player just was not ready.
    if (!potParams && (captionPlayer || playerElement || page.movie_player)) return fail("caption_player_not_ready");
    return fail(errors.includes("caption_fetch_failed") ? "caption_fetch_failed" : "caption_parse_failed");
  } catch {
    return fail("capture_unavailable");
  }
}

/**
 * MAIN-world Bilibili adapter; keep this function self-contained for MV3.
 *
 * Everything is read inside the page with the viewer's own session: the video's
 * page state, the player API (which lists subtitles, including the AI subtitles
 * that need a login) and the subtitle JSON. Signed subtitle URLs and cookies stay
 * in this function; only cues and public metadata are returned.
 */
export async function captureBilibiliPage(): Promise<CaptureAttempt> {
  type Rec = Record<string, unknown>;
  type LocalCue = { start_sec: number; end_sec: number; text: string };
  const MAX_BODY_BYTES = 4_000_000;
  const MAX_CUES = 50_000;
  const MAX_TEXT_CHARS = 1_000_000;
  const MAX_SUBTITLE_TRIES = 3;
  const REQUEST_MS = 6_000;
  const deadline = Date.now() + 12_000;
  const fail = (error: SafeCaptureError): CaptureAttempt => ({ status: "failed", error });
  const asRecord = (value: unknown): Rec | null => (
    value && typeof value === "object" && !Array.isArray(value) ? value as Rec : null
  );
  const clean = (value: string) => value
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
  const positiveInteger = (value: unknown): number | null => {
    const number = typeof value === "number" ? value : typeof value === "string" && /^[0-9]{1,18}$/.test(value) ? Number(value) : Number.NaN;
    return Number.isSafeInteger(number) && number > 0 ? number : null;
  };
  const hdslbHost = (host: string) => host === "hdslb.com" || host.endsWith(".hdslb.com");

  try {
    // Identity comes from the URL, normalized exactly like the server connector's
    // platform id, so a captured video and a URL import reach one library item.
    const locate = (): { id: string; part: number } | null => {
      let url: URL;
      try { url = new URL(location.href); } catch { return null; }
      if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "www.bilibili.com") return null;
      const match = /^\/video\/((?:[aA][vV])[0-9]{1,20}|(?:[bB][vV])[0-9A-Za-z]{10})\/?$/.exec(url.pathname);
      if (!match) return null;
      const raw = match[1]!;
      const values = url.searchParams.getAll("p");
      return {
        id: (raw.slice(0, 2).toLowerCase() === "bv" ? "BV" : "av") + raw.slice(2),
        part: values.length === 1 && /^[1-9][0-9]{0,3}$/.test(values[0]!) ? Number(values[0]) : 1,
      };
    };
    const target = locate();
    if (!target) return fail("unsupported_page");
    const { id, part } = target;
    const isCurrent = (): boolean => {
      const now = locate();
      return now !== null && now.id === id && now.part === part;
    };

    const withTimeout = <T>(work: () => Promise<T>, onTimeout: () => void): Promise<T> => {
      const remaining = deadline - Date.now();
      if (remaining <= 0) return Promise.reject(new Error("timeout"));
      return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => {
          try { onTimeout(); } catch { /* Aborting is best effort. */ }
          reject(new Error("timeout"));
        }, Math.min(REQUEST_MS, remaining));
        Promise.resolve().then(work).then(
          (value) => { clearTimeout(timer); resolve(value); },
          (error: unknown) => { clearTimeout(timer); reject(error); },
        );
      });
    };
    const readBody = async (response: Response): Promise<string> => {
      const declared = Number(response.headers?.get?.("content-length"));
      if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) throw new Error("too_large");
      const stream = response.body;
      if (!stream || typeof stream.getReader !== "function") {
        const text = await response.text();
        if (text.length > MAX_BODY_BYTES) throw new Error("too_large");
        return text;
      }
      const reader = stream.getReader();
      const chunks: Uint8Array[] = [];
      let total = 0;
      try {
        for (;;) {
          const next = await reader.read();
          if (next.done) break;
          const chunk: unknown = next.value;
          if (!ArrayBuffer.isView(chunk)) throw new Error("body_unavailable");
          total += chunk.byteLength;
          if (total > MAX_BODY_BYTES) {
            try { void reader.cancel().catch(() => undefined); } catch { /* Already closed. */ }
            throw new Error("too_large");
          }
          chunks.push(new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength));
        }
      } finally {
        try { reader.releaseLock(); } catch { /* A consumed body is already released. */ }
      }
      const bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
      }
      return new TextDecoder().decode(bytes);
    };
    // One bounded request. `credentials` is "include" only for api.bilibili.com;
    // the signed subtitle CDN URL authenticates itself and is fetched without
    // cookies. Every redirect target is revalidated against the trusted host.
    const fetchText = async (url: URL, credentials: "include" | "omit", trusted: (host: string) => boolean): Promise<string> => {
      if (url.protocol !== "https:" || url.username || url.password || !trusted(url.hostname.toLowerCase())) {
        throw new Error("untrusted");
      }
      const controller = typeof AbortController === "function" ? new AbortController() : undefined;
      return withTimeout(async () => {
        const response = await fetch(url.toString(), { credentials, ...(controller ? { signal: controller.signal } : {}) });
        if (!response.ok) throw new Error("http");
        if (response.url) {
          const final = new URL(response.url);
          if (final.protocol !== "https:" || final.username || final.password || !trusted(final.hostname.toLowerCase())) {
            try { void response.body?.cancel().catch(() => undefined); } catch { /* Nothing to cancel. */ }
            throw new Error("untrusted");
          }
        }
        return readBody(response);
      }, () => controller?.abort());
    };
    const apiHost = (host: string) => host === "api.bilibili.com";
    const fetchApi = async (path: string): Promise<Rec | null> => {
      const text = await fetchText(new URL(`https://api.bilibili.com${path}`), "include", apiHost);
      return asRecord(JSON.parse(text));
    };

    // 1. The video's data. The page's own state is only trusted while it still
    // describes the video in the URL (Bilibili navigates between episodes without
    // reloading); otherwise the public view API for this very id supplies it.
    const sameVideo = (data: Rec | null): data is Rec => {
      if (!data) return false;
      return id.startsWith("BV") ? data.bvid === id : positiveInteger(data.aid) === Number(id.slice(2));
    };
    const readState = (): Rec | null => {
      try { return asRecord((globalThis as typeof globalThis & { __INITIAL_STATE__?: unknown }).__INITIAL_STATE__); } catch { return null; }
    };
    const state = readState();
    const stateVideo = asRecord(state?.videoData);
    let video: Rec | null = sameVideo(stateVideo) ? stateVideo : null;
    const fromState = video !== null;
    if (!video) {
      try {
        const query = id.startsWith("BV") ? `bvid=${id}` : `aid=${id.slice(2)}`;
        const viewed = await fetchApi(`/x/web-interface/view?${query}`);
        const data = viewed?.code === 0 ? asRecord(viewed.data) : null;
        if (sameVideo(data)) video = data;
      } catch { /* Reported below as a stale or missing page state. */ }
    }
    if (!isCurrent()) return fail("stale_player_response");
    if (!video) {
      // A state describing another video is stale; a state without video data is
      // not a page this adapter understands; no state yet means the page is loading.
      return fail(stateVideo ? "stale_player_response" : state ? "capture_unavailable" : "caption_player_not_ready");
    }

    const pages = Array.isArray(video.pages) ? video.pages : [];
    const partEntry = asRecord(pages[part - 1]);
    if (part > 1 && (!partEntry || (partEntry.page !== undefined && partEntry.page !== part))) {
      return fail("capture_unavailable");
    }
    const cid = positiveInteger(partEntry?.cid) ?? (part === 1 ? positiveInteger(video.cid) : null);
    if (cid === null) return fail("capture_unavailable");
    const aid = positiveInteger(video.aid);

    const cleanedTitle = typeof video.title === "string" ? clean(video.title) : "";
    const partName = typeof partEntry?.part === "string" ? clean(partEntry.part) : "";
    const title = (pages.length > 1
      ? `${cleanedTitle} p${String(part).padStart(2, "0")} ${partName}`.trim()
      : cleanedTitle || (typeof document.title === "string" ? clean(document.title.replace(/[_-]\s*哔哩哔哩.*$/u, "")) : "")
    ).slice(0, 1_000) || null;
    const owner = asRecord(video.owner);
    const uploader = fromState ? asRecord(state?.upData) : null;
    const authorValue = typeof owner?.name === "string" ? owner.name : typeof uploader?.name === "string" ? uploader.name : "";
    const author = clean(authorValue).slice(0, 1_000) || null;
    const durationValue = partEntry?.duration ?? video.duration;
    const duration = typeof durationValue === "number" && Number.isFinite(durationValue) && durationValue >= 0 && durationValue <= 31_536_000
      ? Math.floor(durationValue)
      : null;
    // Bilibili stores "-" when a video has no description; control characters go.
    const description = (() => {
      if (typeof video.desc !== "string") return null;
      const text = Array.from(video.desc.replace(/\r\n?/g, "\n"))
        .filter((character) => {
          const code = character.codePointAt(0) ?? 0;
          return code === 9 || code === 10 || (code >= 32 && code !== 127);
        })
        .join("")
        .trim()
        .slice(0, 50_000)
        .trim();
      return text && text !== "-" ? text : null;
    })();
    const coverUrl = (() => {
      if (typeof video.pic !== "string") return null;
      try {
        const cover = new URL(video.pic.trim().replace(/^http:/i, "https:"), "https://www.bilibili.com/");
        if (cover.protocol !== "https:" || cover.username || cover.password || !hdslbHost(cover.hostname.toLowerCase())) return null;
        const stripped = `https://${cover.hostname.toLowerCase()}${cover.pathname}`;
        return stripped.length <= 2_048 ? stripped : null;
      } catch {
        return null;
      }
    })();
    const tags = fromState && Array.isArray(state?.tags)
      ? (state!.tags as unknown[]).flatMap((entry) => {
        const name = asRecord(entry)?.tag_name;
        const tag = typeof name === "string" ? clean(name).slice(0, 200) : "";
        return tag ? [tag] : [];
      }).filter((tag, index, all) => all.indexOf(tag) === index).slice(0, 20)
      : [];

    // 2. The player API lists this part's subtitles and chapters. No request
    // signature is needed; the viewer's cookies unlock login-only subtitles.
    let info: Rec | null;
    try {
      const query = aid !== null ? `aid=${aid}` : `bvid=${encodeURIComponent(String(video.bvid))}`;
      info = await fetchApi(`/x/player/wbi/v2?${query}&cid=${cid}`);
    } catch (error) {
      return fail(error instanceof SyntaxError ? "caption_parse_failed" : "caption_fetch_failed");
    }
    if (!info || info.code !== 0) return fail("caption_fetch_failed");
    const data = asRecord(info.data);
    if (!isCurrent()) return fail("stale_player_response");

    const chapters = ((): PageCapture["metadata"]["chapters"] => {
      const points = Array.isArray(data?.view_points) ? data!.view_points as unknown[] : [];
      const parsed: Array<{ title: string; start_sec: number }> = [];
      for (const entry of points.slice(0, 2_000)) {
        const point = asRecord(entry);
        const chapterTitle = typeof point?.content === "string" ? clean(point.content).slice(0, 500) : "";
        const from = point?.from;
        if (!chapterTitle || typeof from !== "number" || !Number.isFinite(from) || from < 0) continue;
        parsed.push({ title: chapterTitle, start_sec: from });
      }
      const ordered = parsed
        .sort((left, right) => left.start_sec - right.start_sec)
        .filter((chapter, index, all) => index === 0 || chapter.start_sec > all[index - 1]!.start_sec)
        .slice(0, 1_000);
      return ordered.map((chapter, index) => ({
        title: chapter.title,
        start_sec: chapter.start_sec,
        end_sec: ordered[index + 1]?.start_sec ?? (duration !== null && duration >= chapter.start_sec ? duration : null),
      }));
    })();
    const baseCapture = (caption: PageCapture["caption"]): PageCapture => ({
      platform: "bilibili",
      platform_id: id,
      canonical_url: `https://www.bilibili.com/video/${id}`,
      page_url: part > 1 ? `https://www.bilibili.com/video/${id}?p=${part}` : `https://www.bilibili.com/video/${id}`,
      metadata: {
        title,
        author,
        duration_sec: duration,
        language: caption.status === "available" ? caption.language : null,
        description,
        cover_url: coverUrl,
        tags,
        chapters,
      },
      caption,
    });

    // 3. Pick subtitles: human before AI, Chinese before other languages.
    type Track = { url: URL; language: string; auto: boolean; rank: number };
    const subtitleBlock = asRecord(data?.subtitle);
    const listed = Array.isArray(subtitleBlock?.subtitles) ? subtitleBlock!.subtitles as unknown[] : [];
    const tracks: Track[] = [];
    let refused = 0;
    for (const entry of listed.slice(0, 50)) {
      const item = asRecord(entry);
      const lan = typeof item?.lan === "string" ? item.lan.trim().toLowerCase().replace(/_/g, "-") : "";
      const rawUrl = typeof item?.subtitle_url === "string" ? item.subtitle_url.trim() : "";
      if (!lan || !rawUrl || rawUrl.length > 4_096) continue;
      let url: URL;
      try {
        url = new URL(rawUrl.startsWith("//") ? `https:${rawUrl}` : rawUrl.replace(/^http:/i, "https:"));
      } catch {
        refused += 1;
        continue;
      }
      if (url.protocol !== "https:" || url.username || url.password || !hdslbHost(url.hostname.toLowerCase())) {
        refused += 1;
        continue;
      }
      const auto = lan.startsWith("ai-");
      const base = (auto ? lan.slice(3) : lan).split("-")[0] ?? "";
      tracks.push({ url, language: base || "und", auto, rank: (auto ? 2 : 0) + (base === "zh" ? 0 : 1) });
    }
    if (!tracks.length) {
      // Subtitles that were listed but not trusted are a failed read, not an absence.
      return refused > 0
        ? fail("caption_fetch_failed")
        : { status: "no_caption", capture: baseCapture({ status: "unavailable", source: null, language: null, cues: [] }) };
    }
    tracks.sort((left, right) => left.rank - right.rank);

    const parseSubtitle = (body: unknown): LocalCue[] => {
      const lines = Array.isArray(asRecord(body)?.body) ? asRecord(body)!.body as unknown[] : [];
      const cues: LocalCue[] = [];
      for (const entry of lines) {
        const line = asRecord(entry);
        const from = line?.from;
        const to = line?.to;
        const text = typeof line?.content === "string" ? clean(line.content) : "";
        if (typeof from !== "number" || !Number.isFinite(from) || from < 0 || !text || text.length > 10_000) continue;
        const end = typeof to === "number" && Number.isFinite(to) ? Math.max(to, from) : from;
        cues.push({ start_sec: from, end_sec: end, text });
      }
      return cues.sort((left, right) => left.start_sec - right.start_sec);
    };
    const errors: SafeCaptureError[] = [];
    for (const track of tracks.slice(0, MAX_SUBTITLE_TRIES)) {
      let text: string;
      try {
        text = await fetchText(track.url, "omit", hdslbHost);
      } catch {
        errors.push("caption_fetch_failed");
        continue;
      }
      let cues: LocalCue[];
      try {
        cues = parseSubtitle(JSON.parse(text));
      } catch {
        errors.push("caption_parse_failed");
        continue;
      }
      if (!cues.length) {
        errors.push("caption_parse_failed");
        continue;
      }
      if (cues.length > MAX_CUES || cues.reduce((total, cue) => total + cue.text.length, 0) > MAX_TEXT_CHARS) {
        errors.push("caption_parse_failed");
        continue;
      }
      if (!isCurrent()) return fail("stale_player_response");
      return {
        status: "captured",
        capture: baseCapture({ status: "available", source: track.auto ? "auto_caption" : "official_cc", language: track.language, cues }),
      };
    }
    return fail(errors.includes("caption_fetch_failed") ? "caption_fetch_failed" : "caption_parse_failed");
  } catch {
    return fail("capture_unavailable");
  }
}

const MAX_SEEK_SEC = 31_536_000;

/** True for a playback position the player helpers accept: finite, non-negative, at most one year. */
export function isSeekSeconds(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= MAX_SEEK_SEC;
}

export type PlayerProbe =
  | { status: "no_video" }
  | { status: "video"; current_sec: number; ready: boolean; playing: boolean; seeked: boolean };

type VideoProbe = Extract<PlayerProbe, { status: "video" }>;

/**
 * MAIN-world player probe and seek; keep this function self-contained for MV3
 * (Chrome serialises `func`, so it may use only its own arguments and globals).
 * With `seekSec === null` it only reads the position of this frame's video.
 */
export function controlPlayer(seekSec: number | null, kind: Platform): PlayerProbe {
  type SeekablePlayer = { seekTo?: (seconds: number, allowSeekAhead: boolean) => void };
  type BilibiliPlayer = { seek?: (seconds: number) => unknown; getCurrentTime?: () => unknown };
  try {
    if (typeof document.querySelectorAll !== "function") return { status: "no_video" };
    const moviePlayer = kind === "youtube" && typeof document.getElementById === "function"
      ? document.getElementById("movie_player")
      : null;
    let candidates: HTMLVideoElement[];
    if (kind === "youtube") {
      // While an ad plays, the main video element holds the ad, not the page's video.
      if (moviePlayer?.classList?.contains("ad-showing")) return { status: "no_video" };
      // Hover previews elsewhere on the page also use <video>; only the main player counts.
      const main = Array.from(document.querySelectorAll("video.html5-main-video"));
      candidates = (main.length ? main : Array.from(document.querySelectorAll("#movie_player video"))) as HTMLVideoElement[];
    } else {
      // The player renders a <video> or a <bwp-video> element (both expose the media
      // API). Hover previews on other cards also use <video>, so the main player's
      // wrapper wins and the whole page is only a fallback.
      const main = Array.from(document.querySelectorAll(
        ".bpx-player-video-wrap video, .bpx-player-video-wrap bwp-video, #bilibili-player video, #bilibili-player bwp-video, #bilibiliPlayer video, #bilibiliPlayer bwp-video",
      ));
      candidates = (main.length ? main : Array.from(document.querySelectorAll("video, bwp-video"))) as HTMLVideoElement[];
    }
    const entries = candidates.map((video) => {
      const duration = Number(video.duration);
      const known = Number.isFinite(duration) && duration > 0;
      const ready = video.readyState >= 1 && known;
      return { video, duration: known ? duration : null, ready, playing: ready && !video.paused && !video.ended };
    });
    const rank = (entry: (typeof entries)[number]) => (entry.ready ? 2 : 0) + (entry.playing ? 1 : 0);
    const best = entries.reduce<(typeof entries)[number] | undefined>(
      (chosen, entry) => (!chosen || rank(entry) > rank(chosen) ? entry : chosen),
      undefined,
    );
    if (!best) return { status: "no_video" };

    let seeked = false;
    if (seekSec !== null) {
      const target = best.duration !== null ? Math.min(seekSec, best.duration) : seekSec;
      if (kind === "youtube") {
        const global = globalThis as typeof globalThis & { movie_player?: SeekablePlayer };
        const player = (moviePlayer as unknown as SeekablePlayer | null) ?? global.movie_player;
        try {
          if (player && typeof player.seekTo === "function") {
            player.seekTo(target, true);
            seeked = true;
          }
        } catch { /* Fall back to the media element below. */ }
      }
      if (kind === "bilibili") {
        const player = (globalThis as typeof globalThis & { player?: BilibiliPlayer }).player;
        try {
          if (player && typeof player.seek === "function") {
            player.seek(target);
            seeked = true;
          }
        } catch { /* Fall back to the media element below. */ }
      }
      if (!seeked) {
        try {
          best.video.currentTime = target;
          seeked = true;
        } catch { /* A page-owned element may refuse the assignment. */ }
      }
    }
    let current = Number(best.video.currentTime);
    if (kind === "bilibili" && seekSec === null) {
      try {
        const player = (globalThis as typeof globalThis & { player?: BilibiliPlayer }).player;
        const reported = typeof player?.getCurrentTime === "function" ? Number(player.getCurrentTime()) : Number.NaN;
        if (Number.isFinite(reported) && reported >= 0) current = reported;
      } catch { /* The media element's position stands. */ }
    }
    return {
      status: "video",
      current_sec: Number.isFinite(current) && current >= 0 ? current : 0,
      ready: best.ready,
      playing: best.playing,
      seeked,
    };
  } catch {
    return { status: "no_video" };
  }
}

function parseVideoProbe(value: unknown): VideoProbe | null {
  try {
    if (!value || typeof value !== "object") return null;
    const probe = value as Record<string, unknown>;
    if (probe.status !== "video") return null;
    if (!isSeekSeconds(probe.current_sec)) return null;
    if (typeof probe.ready !== "boolean" || typeof probe.playing !== "boolean" || typeof probe.seeked !== "boolean") return null;
    return { status: "video", current_sec: probe.current_sec, ready: probe.ready, playing: probe.playing, seeked: probe.seeked };
  } catch {
    return null;
  }
}

/**
 * Runs the player probe in the tab's top frame, where both supported players live.
 * Returns null when the page refuses the injection or holds no usable video.
 */
async function probePlayer(tabId: number, kind: Platform, seekSec: number | null): Promise<VideoProbe | null> {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: false },
      world: "MAIN",
      func: controlPlayer,
      args: [seekSec, kind],
    }) as unknown as unknown[];
    for (const entry of results) {
      try {
        if (!entry || typeof entry !== "object") continue;
        const probe = parseVideoProbe((entry as { result?: unknown }).result);
        if (probe) return probe;
      } catch { /* A hostile result entry is skipped. */ }
    }
  } catch { /* The page refused the injection. */ }
  return null;
}

/** Current playback position in seconds, or null when the page has no ready video. */
export async function readPlaybackSeconds(tabId: number, url: string): Promise<number | null> {
  const kind = classifyPageUrl(url);
  if (!kind) throw new Error("unsupported_page");
  const probe = await probePlayer(tabId, kind, null);
  return probe?.ready ? Math.round(probe.current_sec * 1000) / 1000 : null;
}

/** Seeks the page's player; rejects with `seek_failed` when no player accepted the position. */
export async function seekPlayback(tabId: number, url: string, startSec: number): Promise<void> {
  if (!isSeekSeconds(startSec)) throw new Error("invalid_command");
  const kind = classifyPageUrl(url);
  if (!kind) throw new Error("unsupported_page");
  if (!(await probePlayer(tabId, kind, startSec))?.seeked) throw new Error("seek_failed");
}
