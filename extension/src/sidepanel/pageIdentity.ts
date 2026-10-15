import { classifyPageUrl } from "../page-capture";
import type { Platform } from "../protocol";

export interface PageIdentity {
  platform: Platform;
  /**
   * Stable per video. YouTube: the video id. NTULearn/Kaltura: the tab URL
   * without its fragment, because the Kaltura entry is only known after a read.
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
  return { platform, key: `ntu_kaltura:${parsed.origin}${parsed.pathname}${parsed.search}` };
}

/** `page-` plus 32 lowercase hex characters, the server's page thread id shape. */
export function newConversationId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `page-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}
