import { bilibiliVideoRef, classifyPageUrl } from "../page-capture";
import type { PageCapture, Platform } from "../protocol";

export interface PageIdentity {
  platform: Platform;
  /**
   * Stable per video. YouTube: the video id. Bilibili: the `BV…`/`av…` id plus
   * the part index, because every part has its own subtitles and thread.
   * NTULearn/Kaltura: the tab URL without its fragment, because the Kaltura
   * entry is only known after a read.
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
  const parsed = new URL(url);
  if (platform === "youtube") {
    const id = youtubeVideoId(parsed);
    return id ? { platform, key: `youtube:${id}` } : null;
  }
  if (platform === "bilibili") {
    const ref = bilibiliVideoRef(url);
    return ref ? { platform, key: `bilibili:${ref.id}:p${ref.part}` } : null;
  }
  return { platform, key: `ntu_kaltura:${parsed.origin}${parsed.pathname}${parsed.search}` };
}

/**
 * The `identifyPage` key of the video a read capture describes, or null for a
 * platform whose key is not derivable from the capture (Kaltura). A read right
 * after an SPA navigation can still describe the previous video; comparing this
 * with the tab's key detects it.
 */
export function captureIdentityKey(page: PageCapture): string | null {
  if (page.platform === "youtube") return `youtube:${page.platform_id}`;
  if (page.platform === "bilibili") {
    const part = Number(/[?&]p=([0-9]+)(?:&|$)/.exec(page.page_url)?.[1] ?? 1);
    return `bilibili:${page.platform_id}:p${Number.isInteger(part) && part > 1 ? part : 1}`;
  }
  return null;
}

/** `page-` plus 32 lowercase hex characters, the server's page thread id shape. */
export function newConversationId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `page-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}
