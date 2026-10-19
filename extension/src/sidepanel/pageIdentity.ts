import { bilibiliVideoRef, classifyPageUrl } from "../page-capture";
import type { PageCapture, Platform } from "../protocol";

export interface PageIdentity {
  platform: Platform;
  /**
   * Stable per video. YouTube: the video id. Bilibili: the `BV…`/`av…` id plus
   * the part index, because every part has its own subtitles and thread.
   */
  key: string;
}

function youtubeVideoId(url: URL): string | null {
  if (url.hostname.toLowerCase() === "youtu.be") {
    return /^\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1] ?? null;
  }
  const shorts = /^\/shorts\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1];
  return shorts ?? (url.pathname === "/watch" ? url.searchParams.get("v") : null);
}

/** Identifies the video a tab shows, or null on an unsupported page. */
export function identifyPage(url: string | null | undefined): PageIdentity | null {
  if (!url) return null;
  const platform = classifyPageUrl(url);
  if (!platform) return null;
  if (platform === "youtube") {
    const id = youtubeVideoId(new URL(url));
    return id ? { platform, key: `youtube:${id}` } : null;
  }
  const ref = bilibiliVideoRef(url);
  return ref ? { platform, key: `bilibili:${ref.id}:p${ref.part}` } : null;
}

/**
 * The `identifyPage` key of the video a read capture describes. A read right
 * after an SPA navigation can still describe the previous video; comparing this
 * with the tab's key detects it.
 */
export function captureIdentityKey(page: PageCapture): string {
  if (page.platform === "youtube") return `youtube:${page.platform_id}`;
  const part = Number(/[?&]p=([0-9]+)(?:&|$)/.exec(page.page_url)?.[1] ?? 1);
  return `bilibili:${page.platform_id}:p${Number.isInteger(part) && part > 1 ? part : 1}`;
}

/** `page-` plus 32 lowercase hex characters, the server's page thread id shape. */
export function newConversationId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `page-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}
