import { runInNewContext } from "node:vm";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bilibiliVideoRef,
  captureActivePage,
  captureBilibiliPage,
  captureYouTubePage,
  classifyPageUrl,
  controlPlayer,
  isSeekSeconds,
  normalizePageCapture,
  readPlaybackSeconds,
  seekPlayback,
  selectBestCapture,
  type CaptureAttempt,
  type PlayerProbe,
} from "./page-capture.js";
import type { Cue, PageCapture } from "./protocol.js";

const YOUTUBE_ID = "abcdefghijk";
const OTHER_YOUTUBE_ID = "zyxwvutsrqp";
const YOUTUBE_URL = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;

function availableCapture(cues: Cue[] = [{ start_sec: 1, end_sec: 2, text: "cue" }]): PageCapture {
  return {
    platform: "youtube",
    platform_id: YOUTUBE_ID,
    canonical_url: YOUTUBE_URL,
    page_url: YOUTUBE_URL,
    metadata: {
      title: "A video",
      author: null,
      duration_sec: 120,
      language: "en",
      description: null,
      cover_url: null,
      tags: [],
      chapters: [],
    },
    caption: { status: "available", source: "official_cc", language: "en", cues },
  };
}

function unavailableCapture(title: string | null = "A video", duration: number | null = null): PageCapture {
  return {
    platform: "youtube",
    platform_id: YOUTUBE_ID,
    canonical_url: YOUTUBE_URL,
    page_url: YOUTUBE_URL,
    metadata: {
      title,
      author: null,
      duration_sec: duration,
      language: null,
      description: null,
      cover_url: null,
      tags: [],
      chapters: [],
    },
    caption: { status: "unavailable", source: null, language: null, cues: [] },
  };
}

function youtubeResponse(
  videoId = YOUTUBE_ID,
  tracks: Array<Record<string, unknown>> = [],
  renderer: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    videoDetails: { videoId, title: "Current title", author: "Author", lengthSeconds: "120" },
    captions: { playerCaptionsTracklistRenderer: { ...renderer, captionTracks: tracks } },
  };
}

function json3(text = "Hello", startMs = 1000): { events: Array<Record<string, unknown>> } {
  return { events: [{ tStartMs: startMs, dDurationMs: 1500, segs: [{ utf8: text }] }] };
}

function recreateAdapter(
  adapter: () => Promise<CaptureAttempt>,
  context: Record<string, unknown>,
): () => Promise<CaptureAttempt> {
  return runInNewContext(`(${adapter.toString()})`, context) as () => Promise<CaptureAttempt>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("capture selection and coordinator", () => {
  it("prefers a valid caption capture over a capture without captions", () => {
    const selected = selectBestCapture([unavailableCapture(), availableCapture()]);
    expect(selected?.caption.status).toBe("available");
  });

  it("prefers a titled capture with a duration when neither has captions", () => {
    const selected = selectBestCapture([unavailableCapture(null), unavailableCapture("A lecture", 3600)]);
    expect(selected?.metadata.title).toBe("A lecture");
    expect(selected?.metadata.duration_sec).toBe(3600);
  });

  it("breaks equal-score ties independently of frame result order", () => {
    const first = availableCapture();
    const second = availableCapture();
    first.metadata.title = "Zulu lecture";
    second.metadata.title = "Alpha lecture";

    expect(selectBestCapture([first, second])).toEqual(selectBestCapture([second, first]));
  });

  it("routes YouTube to the top frame and aggregates a structured result", async () => {
    const executeScript = vi.fn(async (options: { target: { tabId: number; allFrames?: boolean } }) => {
      expect(options.target).toEqual({ tabId: 7, allFrames: false });
      return [{ result: { status: "captured", capture: availableCapture() } }];
    });
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(7, YOUTUBE_URL);

    expect(result.platform).toBe("youtube");
    expect(executeScript).toHaveBeenCalledOnce();
  });

  it("lets a valid result beat a failed one", async () => {
    const executeScript = vi.fn(async () => [
      { result: { status: "failed", error: "capture_unavailable" } },
      { result: { status: "captured", capture: availableCapture() } },
    ]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(8, YOUTUBE_URL);

    expect(result.caption.status).toBe("available");
  });

  it("ignores a browser-level frame error when another result captured subtitles", async () => {
    const executeScript = vi.fn(async () => [
      { frameId: 1, error: "Frame URL contained ks=private-signature" },
      { frameId: 2, result: { status: "captured", capture: availableCapture() } },
    ]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(8, YOUTUBE_URL);

    expect(result.caption.status).toBe("available");
    expect(JSON.stringify(result)).not.toContain("private-signature");
  });

  it("surfaces a caption read failure instead of returning unavailable", async () => {
    const executeScript = vi.fn(async () => [
      { result: { status: "no_caption", capture: unavailableCapture() } },
      { result: { status: "failed", error: "caption_fetch_failed" } },
    ]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(captureActivePage(8, YOUTUBE_URL)).rejects.toThrow("caption_fetch_failed");
  });

  it("reports a rejected injection as a stable error without a second attempt", async () => {
    const executeScript = vi.fn().mockRejectedValue(new Error("inaccessible tab"));
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(captureActivePage(8, YOUTUBE_URL)).rejects.toThrow("capture_unavailable");
    expect(executeScript).toHaveBeenCalledOnce();
  });

  it("returns an unavailable capture when the page has no captions", async () => {
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "no_caption", capture: unavailableCapture() } }]) } });

    const result = await captureActivePage(8, YOUTUBE_URL);

    expect(result.caption.status).toBe("unavailable");
  });

  it("rejects unsupported hosts, malformed results, and secret-bearing public URLs", async () => {
    await expect(captureActivePage(1, "http://www.youtube.com/watch?v=abcdefghijk")).rejects.toThrow("unsupported_page");
    await expect(captureActivePage(1, "https://example.com/video")).rejects.toThrow("unsupported_page");

    const secret = availableCapture();
    secret.canonical_url += "&ks=private-signature";
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "captured", capture: secret } }]) } });
    await expect(captureActivePage(1, YOUTUBE_URL)).rejects.toThrow("capture_unavailable");
  });

  it("rejects page-world captures that violate the backend runtime contract", async () => {
    const invalidId = availableCapture() as PageCapture;
    invalidId.platform_id = "entry_id";
    invalidId.canonical_url = "https://www.youtube.com/watch?v=entry_id";
    const unsorted = availableCapture([
      { start_sec: 2, end_sec: 3, text: "later" },
      { start_sec: 1, end_sec: 2, text: "earlier" },
    ]);
    const malformedChapter = availableCapture() as PageCapture;
    malformedChapter.metadata.chapters = [{ title: "bad", start_sec: 2, end_sec: 1 }];
    // A platform this build no longer reads (NTULearn/Kaltura) is not a valid capture either.
    const retiredPlatform = { ...availableCapture(), platform: "ntu_kaltura" } as unknown as PageCapture;
    const values = [invalidId, unsorted, malformedChapter, retiredPlatform];
    const executeScript = vi.fn(async () => [{ result: { status: "captured", capture: values.shift() } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    for (let index = 0; index < 4; index += 1) {
      await expect(captureActivePage(1, YOUTUBE_URL)).rejects.toThrow("capture_unavailable");
    }
  });

  it("rebuilds accepted captures from an allowlist and drops secret-bearing extras", async () => {
    const capture = availableCapture() as PageCapture & Record<string, unknown>;
    capture.ks = "private-signature";
    (capture.metadata as PageCapture["metadata"] & Record<string, unknown>).authorization = "Bearer private";
    (capture.caption as PageCapture["caption"] & Record<string, unknown>).caption_url = "https://www.youtube.com/api/timedtext?signature=private";
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "captured", capture } }]) } });

    const result = await captureActivePage(1, YOUTUBE_URL);
    const serialized = JSON.stringify(result);

    expect(result.caption.status).toBe("available");
    expect(serialized).not.toContain("private");
    expect(serialized).not.toContain("authorization");
    expect(serialized).not.toContain("caption_url");
  });

  it("turns a throwing injection result into a stable safe error", async () => {
    const injectionResult = Object.defineProperty({}, "result", {
      get: () => { throw new Error("ks=private-signature"); },
    });
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [injectionResult]) } });

    await expect(captureActivePage(1, YOUTUBE_URL)).rejects.toThrow("capture_unavailable");
  });

  it("routes supported watch, shorts, and short-link shapes only", async () => {
    const executeScript = vi.fn(async () => [{ result: { status: "captured", capture: availableCapture() } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    for (const url of [
      YOUTUBE_URL,
      `https://www.youtube.com/shorts/${YOUTUBE_ID}`,
      `https://youtu.be/${YOUTUBE_ID}?si=tracking-value`,
    ]) {
      await expect(captureActivePage(7, url)).resolves.toMatchObject({ platform_id: YOUTUBE_ID });
    }
    await expect(captureActivePage(7, `https://www.youtube.com/embed/${YOUTUBE_ID}`)).rejects.toThrow("unsupported_page");
    expect(executeScript).toHaveBeenCalledTimes(3);
  });

  it("treats NTULearn and Kaltura pages as unsupported without injecting anything", async () => {
    const executeScript = vi.fn();
    vi.stubGlobal("chrome", { scripting: { executeScript } });
    for (const url of [
      "https://ntulearn.ntu.edu.sg/ultra/courses/example",
      "https://ntulearnvideo.ntu.edu.sg/media/52_6ce010d",
      "https://ntulearnv1.ntu.edu.sg/media/52_6ce010d",
      "https://cdnapisec.kaltura.com/p/123/embed?entry_id=52_6ce010d",
    ]) {
      expect(classifyPageUrl(url)).toBeNull();
      await expect(captureActivePage(9, url)).rejects.toThrow("unsupported_page");
    }
    expect(executeScript).not.toHaveBeenCalled();
  });
});


describe("YouTube caption adapter", () => {
  function stubYouTube(href = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`): void {
    vi.stubGlobal("location", { href, hostname: new URL(href).hostname, pathname: new URL(href).pathname });
    vi.stubGlobal("document", { title: "Current title - YouTube" });
  }

  it("uses the current player response and prefers manual captions over ASR", async () => {
    stubYouTube();
    const manual = "https://www.youtube.com/api/timedtext?lang=en&kind=manual";
    const auto = "https://www.youtube.com/api/timedtext?lang=en&kind=asr";
    vi.stubGlobal("movie_player", { getPlayerResponse: () => youtubeResponse(YOUTUBE_ID, [
      { kind: "asr", languageCode: "en", baseUrl: auto },
      { languageCode: "en", baseUrl: manual },
    ]) });
    const fetchStub = vi.fn(async () => ({ ok: true, json: async () => json3("manual <b>caption</b>") }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.source).toBe("official_cc");
      expect(result.capture.caption.cues[0]?.text).toBe("manual caption");
      expect(result.capture.metadata.title).toBe("Current title");
    }
    expect(fetchStub).toHaveBeenCalledWith(`${manual}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
  });

  it("honors the current player track before the response default and fallback order", async () => {
    stubYouTube();
    const tracks = [
      { languageCode: "ar", baseUrl: "https://www.youtube.com/api/timedtext?lang=ar" },
      { languageCode: "zh-CN", baseUrl: "https://www.youtube.com/api/timedtext?lang=zh-CN" },
      { languageCode: "fr", baseUrl: "https://www.youtube.com/api/timedtext?lang=fr" },
    ];
    vi.stubGlobal("movie_player", {
      getPlayerResponse: () => youtubeResponse(YOUTUBE_ID, tracks, {
        audioTracks: [{ defaultCaptionTrackIndex: 1 }],
      }),
      getOption: () => ({ languageCode: "fr" }),
    });
    const fetchStub = vi.fn(async () => ({ ok: true, json: async () => json3("français") }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("fr");
    expect(fetchStub).toHaveBeenCalledWith(`${tracks[2]!.baseUrl}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
  });

  it("uses a valid audio-track default before the first manual language", async () => {
    stubYouTube();
    const tracks = [
      { languageCode: "ar", baseUrl: "https://www.youtube.com/api/timedtext?lang=ar" },
      { languageCode: "zh-CN", baseUrl: "https://www.youtube.com/api/timedtext?lang=zh-CN" },
    ];
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, tracks, {
      audioTracks: [{ defaultCaptionTrackIndex: 1 }],
    }));
    const fetchStub = vi.fn(async () => ({ ok: true, json: async () => json3("默认字幕") }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("zh-CN");
    expect(fetchStub).toHaveBeenCalledWith(`${tracks[1]!.baseUrl}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
  });

  it("reads a JSON player_response string from the current player config", async () => {
    stubYouTube();
    vi.stubGlobal("ytplayer", { config: { args: { player_response: JSON.stringify(youtubeResponse(YOUTUBE_ID, [{ languageCode: "zh-Hans", baseUrl: "https://www.youtube.com/api/timedtext?lang=zh" }])) } } });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => json3("你好") })));

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("zh-Hans");
  });

  it("falls back from an unreadable manual track to a working ASR track", async () => {
    stubYouTube();
    const manual = "https://www.youtube.com/api/timedtext?kind=manual";
    const auto = "https://www.youtube.com/api/timedtext?kind=asr";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [
      { languageCode: "en", baseUrl: manual },
      { kind: "asr", languageCode: "en", baseUrl: auto },
    ]));
    const fetchStub = vi.fn(async (url: string) => url.includes("manual")
      ? { ok: false, json: async () => ({}) }
      : { ok: true, json: async () => json3("automatic") });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.source).toBe("auto_caption");
    expect(fetchStub).toHaveBeenCalledTimes(2);
  });

  it("rejects a successful caption response that resolves after SPA navigation", async () => {
    const locationState = {
      href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
      hostname: "www.youtube.com",
      pathname: "/watch",
    };
    vi.stubGlobal("location", locationState);
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => {
        locationState.href = `https://www.youtube.com/watch?v=${OTHER_YOUTUBE_ID}`;
        return json3("old video caption");
      },
    })));

    await expect(captureYouTubePage()).resolves.toEqual({
      status: "failed",
      error: "stale_player_response",
    });
  });

  it("falls back from an unreadable JSON3 response to WebVTT on the same trusted track", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => { throw new SyntaxError("empty body"); } }
      : { ok: true, text: async () => "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nFallback caption" });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.language).toBe("en");
      expect(result.capture.caption.cues).toEqual([{ start_sec: 1, end_sec: 3, text: "Fallback caption" }]);
    }
    expect(fetchStub).toHaveBeenNthCalledWith(1, `${baseUrl}&fmt=json3`, expect.objectContaining({ credentials: "include" }));
    expect(fetchStub).toHaveBeenNthCalledWith(2, `${baseUrl}&fmt=vtt`, expect.objectContaining({ credentials: "include" }));
  });

  it("falls back through empty VTT to the original YouTube XML transcript", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      if (url.includes("fmt=vtt")) return { ok: true, text: async () => "" };
      return { ok: true, text: async () => "<?xml version=\"1.0\"?><transcript><text start=\"2.5\" dur=\"1.25\">Raw &amp; valid</text></transcript>" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 2.5, end_sec: 3.75, text: "Raw & valid" }]);
    }
    expect(fetchStub).toHaveBeenNthCalledWith(3, baseUrl, expect.objectContaining({ credentials: "include" }));
  });

  it("falls back to the current-video official transcript endpoint when timed-text formats are empty", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`video:${YOUTUBE_ID}`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    const fetchStub = vi.fn(async (url: string, init?: RequestInit) => {
      void init;
      if (url.includes("/youtubei/v1/get_transcript")) return {
        ok: true,
        url: "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        json: async () => ({ actions: [{ transcriptSegmentRenderer: { startMs: "1000", endMs: "2500", snippet: { runs: [{ text: "Official transcript cue" }] } } }] }),
      };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 1, end_sec: 2.5, text: "Official transcript cue" }]);
    }
    expect(fetchStub).toHaveBeenCalledTimes(4);
    expect(fetchStub.mock.calls[3]?.[0]).toContain("/youtubei/v1/get_transcript");
    expect(fetchStub.mock.calls[3]?.[1]).toEqual(expect.objectContaining({
      method: "POST",
      credentials: "include",
      headers: expect.objectContaining({ "X-YouTube-Client-Version": "1.2.3" }),
    }));
  });

  it("decodes percent-encoded transcript params before binding them to the current video", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = encodeURIComponent(Buffer.from(`video:${YOUTUBE_ID}`).toString("base64"));
    expect(params).toContain("%3D");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("/youtubei/v1/get_transcript")) return {
        ok: true,
        url: "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        json: async () => ({ actions: [{ transcriptSegmentRenderer: { startMs: "500", endMs: "1500", snippet: { simpleText: "Encoded params cue" } } }] }),
      };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 0.5, end_sec: 1.5, text: "Encoded params cue" }]);
    }
    const transcriptCall = fetchStub.mock.calls.find(([url]) => String(url).includes("/youtubei/v1/get_transcript"));
    expect(transcriptCall).toBeDefined();
    const transcriptInit = (transcriptCall as unknown as [string, RequestInit?] | undefined)?.[1];
    expect(JSON.parse(String(transcriptInit?.body))).toEqual(expect.objectContaining({
      params: Buffer.from(`video:${YOUTUBE_ID}`).toString("base64"),
    }));
  });

  it("binds a real nested length-delimited protobuf transcript param to the current video", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const videoBytes = Array.from(YOUTUBE_ID, (character) => character.charCodeAt(0));
    const nestedMessage = [0x0a, videoBytes.length, ...videoBytes];
    const protobuf = Uint8Array.from([0x0a, nestedMessage.length, ...nestedMessage]);
    const params = Buffer.from(protobuf).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("/youtubei/v1/get_transcript")) return {
        ok: true,
        url: "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        json: async () => ({ actions: [{ transcriptSegmentRenderer: { startMs: "750", endMs: "1750", snippet: { simpleText: "Protobuf-bound cue" } } }] }),
      };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 0.75, end_sec: 1.75, text: "Protobuf-bound cue" }]);
    }
    const transcriptCall = fetchStub.mock.calls.find(([url]) => String(url).includes("/youtubei/v1/get_transcript"));
    const transcriptInit = (transcriptCall as unknown as [string, RequestInit?] | undefined)?.[1];
    expect(JSON.parse(String(transcriptInit?.body))).toEqual(expect.objectContaining({ params }));
  });

  it("does not call the transcript endpoint for malformed percent-encoded params", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params: "%not-valid" } });
    vi.stubGlobal("ytcfg", { get: vi.fn(() => "configured") });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(fetchStub.mock.calls.some(([url]) => String(url).includes("/youtubei/v1/get_transcript"))).toBe(false);
  });

  it("uses YouTube's rendered transcript UI when direct caption endpoints reject the request", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en&kind=asr";
    const params = encodeURIComponent(Buffer.from(`video:${YOUTUBE_ID}`).toString("base64"));
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ kind: "asr", languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { engagementPanels: [{ clickTrackingParams: "tracking", getTranscriptEndpoint: { params } }] });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: { clientName: "WEB", clientVersion: "1.2.3" } },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    let transcriptVisible = false;
    const expandClick = vi.fn();
    const hiddenTranscriptClick = vi.fn();
    const transcriptClick = vi.fn(() => { transcriptVisible = true; });
    const hiddenTranscriptButton = { click: hiddenTranscriptClick, getClientRects: () => [] };
    const visibleTranscriptButton = { click: transcriptClick, getClientRects: () => [{ width: 100, height: 30 }] };
    const segments = [
      { data: { startMs: "0", endMs: "1000", snippet: { runs: [{ text: "First DOM cue" }] } } },
      { data: { startMs: "0", endMs: "1000", snippet: { runs: [{ text: "First DOM cue" }] } } },
      { data: { startMs: "1000", endMs: "2500", snippet: { simpleText: "Second DOM cue" } } },
    ];
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => null,
      querySelector: (selector: string) => {
        if (selector.includes("#expand")) return { click: expandClick };
        if (selector === "ytd-video-description-transcript-section-renderer button") return hiddenTranscriptButton;
        return null;
      },
      querySelectorAll: (selector: string) => {
        if (selector === "ytd-video-description-transcript-section-renderer button") {
          return [hiddenTranscriptButton, visibleTranscriptButton];
        }
        return selector === "ytd-transcript-segment-renderer" && transcriptVisible ? segments : [];
      },
    });
    const fetchStub = vi.fn(async (url: string) => {
      if (url.includes("/youtubei/v1/get_transcript")) return { ok: false, status: 400 };
      if (url.includes("fmt=json3")) return { ok: true, json: async () => ({ events: [] }) };
      return { ok: true, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption).toEqual({
        status: "available",
        source: "auto_caption",
        language: "en",
        cues: [
          { start_sec: 0, end_sec: 1, text: "First DOM cue" },
          { start_sec: 1, end_sec: 2.5, text: "Second DOM cue" },
        ],
      });
    }
    expect(expandClick).toHaveBeenCalledOnce();
    expect(transcriptClick).toHaveBeenCalledOnce();
    expect(hiddenTranscriptClick).not.toHaveBeenCalled();
  });

  it("immediately consumes an already-open rendered transcript without clicking the page", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    const click = vi.fn();
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => null,
      querySelector: () => ({ click }),
      querySelectorAll: (selector: string) => selector === "ytd-transcript-segment-renderer"
        ? [{ data: { startMs: "250", endMs: "1250", snippet: { simpleText: "Already rendered" } } }]
        : [],
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" }));

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.caption.cues).toEqual([{ start_sec: 0.25, end_sec: 1.25, text: "Already rendered" }]);
    }
    expect(click).not.toHaveBeenCalled();
  });

  it("abandons the rendered transcript fallback if YouTube navigates to another video", async () => {
    const locationState = {
      href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
      hostname: "www.youtube.com",
      pathname: "/watch",
    };
    vi.stubGlobal("location", locationState);
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`video:${YOUTUBE_ID}`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params } });
    vi.stubGlobal("ytcfg", { get: (key: string) => ({
      INNERTUBE_API_KEY: "public-client-key",
      INNERTUBE_CONTEXT: { client: {} },
      INNERTUBE_CLIENT_NAME: 1,
      INNERTUBE_CLIENT_VERSION: "1.2.3",
    } as Record<string, unknown>)[key] });
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => null,
      querySelector: (selector: string) => selector === "ytd-video-description-transcript-section-renderer button"
        ? { click: () => { locationState.href = `https://www.youtube.com/watch?v=${OTHER_YOUTUBE_ID}`; } }
        : null,
      querySelectorAll: () => [],
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : url.includes("/youtubei/v1/get_transcript")
        ? { ok: false, status: 400 }
        : { ok: true, text: async () => "" }));

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });
  });

  it("does not call the transcript endpoint when its opaque params belong to another video", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`video:${OTHER_YOUTUBE_ID}`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params } });
    vi.stubGlobal("ytcfg", { get: vi.fn(() => "configured") });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(fetchStub.mock.calls.some(([url]) => String(url).includes("/youtubei/v1/get_transcript"))).toBe(false);
  });

  it("does not bind a transcript endpoint to an incidental video ID substring", async () => {
    stubYouTube();
    const baseUrl = "https://www.youtube.com/api/timedtext?lang=en";
    const params = Buffer.from(`unrelated-prefix:${YOUTUBE_ID}:suffix`).toString("base64url");
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl }]));
    vi.stubGlobal("ytInitialData", { getTranscriptEndpoint: { params } });
    vi.stubGlobal("ytcfg", { get: vi.fn(() => "configured") });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, json: async () => ({ events: [] }) }
      : { ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(fetchStub.mock.calls.some(([url]) => String(url).includes("/youtubei/v1/get_transcript"))).toBe(false);
  });

  it("rejects stale responses after same-tab SPA navigation", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(OTHER_YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));

    const result = await captureYouTubePage();

    expect(result).toEqual({ status: "failed", error: "stale_player_response" });
  });

  it("does not trust an ID-less fallback when every identified response is stale", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(OTHER_YOUTUBE_ID));
    vi.stubGlobal("ytplayer", { config: { captions: { playerCaptionsTracklistRenderer: { captionTracks: [{ baseUrl: "https://www.youtube.com/api/timedtext" }] } } } });
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("does not let an ID-less fallback override an exact current response", async () => {
    stubYouTube();
    vi.stubGlobal("movie_player", { getPlayerResponse: () => youtubeResponse(YOUTUBE_ID) });
    vi.stubGlobal("ytplayer", { config: { captions: { playerCaptionsTracklistRenderer: { captionTracks: [{ baseUrl: "https://www.youtube.com/api/timedtext" }] } } } });
    const fetchStub = vi.fn();
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("no_caption");
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("distinguishes no captions from a discovered but unparsable caption", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID));
    const noCaption = await captureYouTubePage();
    expect(noCaption.status).toBe("no_caption");

    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ events: [{ tStartMs: "bad", segs: [] }] }) })));
    const malformed = await captureYouTubePage();
    expect(malformed).toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("bounds hanging YouTube caption requests", async () => {
    vi.useFakeTimers();
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    let observedSignal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
      observedSignal = init?.signal;
      return new Promise<Response>(() => undefined);
    }));

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(12_000);

    await expect(pending).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(observedSignal?.aborted).toBe(true);
  });

  it("bounds a response body that hangs after the caption headers arrive", async () => {
    vi.useFakeTimers();
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const cancel = vi.fn();
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      observedSignals.push(init?.signal);
      return {
        ok: true,
        body: {
          getReader: () => ({
            read: async () => new Promise<never>(() => undefined),
            cancel,
            releaseLock: vi.fn(),
          }),
        },
      };
    }));

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(12_000);

    await expect(pending).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(observedSignals.length).toBe(3);
    expect(observedSignals.every((signal) => signal?.aborted)).toBe(true);
    expect(cancel).toHaveBeenCalled();
  });

  it("rejects oversized YouTube response bodies before parsing them", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const oversized = "x".repeat(4_000_001);
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    const fetchStub = vi.fn(async (_url: string, init?: RequestInit) => {
      observedSignals.push(init?.signal);
      return { ok: true, text: async () => oversized };
    });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(observedSignals.every((signal) => signal?.aborted)).toBe(true);
  });

  it("cancels an oversized streamed YouTube response before parsing it", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const cancel = vi.fn();
    const oversized = new Uint8Array(4_000_001);
    const observedSignals: Array<AbortSignal | null | undefined> = [];
    const fetchStub = vi.fn(async (_url: string, init?: RequestInit) => {
      observedSignals.push(init?.signal);
      return {
        ok: true,
        body: {
          getReader: () => ({
            read: vi.fn()
              .mockResolvedValueOnce({ done: false, value: oversized })
              .mockResolvedValue({ done: true, value: undefined }),
            cancel,
            releaseLock: vi.fn(),
          }),
        },
      };
    });
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    expect(fetchStub).toHaveBeenCalledTimes(3);
    expect(observedSignals.every((signal) => signal?.aborted)).toBe(true);
    expect(cancel).toHaveBeenCalled();
  });

  it.skip("reads a bounded streamed YouTube response body", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    const encoded = new TextEncoder().encode(JSON.stringify(json3("streamed cue")));
    let readCount = 0;
    const fetchStub = vi.fn(async () => ({
      ok: true,
      url: "https://www.youtube.com/api/timedtext",
      body: {
        getReader: () => ({
          read: async () => readCount++ === 0
            ? { done: false, value: encoded }
            : { done: true, value: undefined },
          releaseLock: vi.fn(),
        }),
      },
    }));
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("streamed cue");
    expect(fetchStub).toHaveBeenCalledOnce();
  });

  it("does not accept untrusted caption endpoints or invalid timings", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://evil.example/caption.vtt" }]));
    const untrusted = await captureYouTubePage();
    expect(untrusted).toEqual({ status: "failed", error: "caption_fetch_failed" });

    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ events: [{ tStartMs: -1, dDurationMs: 2, segs: [{ utf8: "bad" }] }] }) })));
    const invalid = await captureYouTubePage();
    expect(invalid).toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("rejects a caption request redirected outside trusted YouTube hosts", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ baseUrl: "https://www.youtube.com/api/timedtext" }]));
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      url: "https://evil.example/caption",
      json: async () => json3("must not be consumed"),
    })));

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
  });

  it("survives the MV3 function serialization boundary without module closures", async () => {
    const response = youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?lang=en" }]);
    const adapter = recreateAdapter(captureYouTubePage, {
      Date,
      clearTimeout,
      URL,
      location: { href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`, hostname: "www.youtube.com", pathname: "/watch" },
      document: { title: "Serialized player - YouTube" },
      setTimeout,
      ytInitialPlayerResponse: response,
      fetch: async () => ({ ok: true, json: async () => json3("serialized cue") }),
    });

    const result = await adapter();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("serialized cue");
  });
});

describe("YouTube caption proof-of-origin token (pot)", () => {
  const POT = "SECRET_POT_VALUE";
  const OTHER_POT = "OTHER_VIDEO_POT";
  const bareUrl = (lang: string, kind?: string): string => `https://www.youtube.com/api/timedtext?v=${YOUTUBE_ID}&ei=TRACK_EI&caps=asr&opi=1&exp=xbt&xoaf=5&hl=en&ip=0.0.0.0&ipbits=0&expire=1&sparams=ip%2Cipbits%2Cexpire%2Cv&signature=TRACK_SIGNATURE&key=yt8${kind ? `&kind=${kind}` : ""}&lang=${lang}`;
  // The player's own request: same video, plus pot/potc and client parameters.
  const potUrl = (videoId = YOUTUBE_ID, pot = POT): string => `https://www.youtube.com/api/timedtext?v=${videoId}&ei=OBSERVED_EI&lang=en&fmt=json3&potc=1&pot=${pot}&xorb=2&xobt=3&xovt=3&cbrand=apple&cbr=Chrome&cbrver=140.0.0.0&c=WEB&cver=2.20261016.00.00&cplayer=UNIPLAYER&cos=Macintosh&cosver=10_15_7&cplatform=DESKTOP&unrelated=drop-me`;

  type FakeObserverCallback = (list: { getEntries: () => Array<{ name: string }> }) => void;
  class FakeObserver {
    static instances: FakeObserver[] = [];
    observed: unknown[] = [];
    disconnected = false;
    constructor(readonly callback: FakeObserverCallback) { FakeObserver.instances.push(this); }
    observe(options: unknown): void { this.observed.push(options); }
    disconnect(): void { this.disconnected = true; }
    emit(name: string): void { this.callback({ getEntries: () => [{ name }] }); }
  }

  type Fixture = {
    tracks: Array<Record<string, unknown>>;
    /** What `getOption("captions", "track")` returns; `{}` means captions are off. */
    captionTrack?: Record<string, unknown>;
    adShowing?: boolean;
    /** Resource-timeline entries that already exist. */
    entries?: string[];
    /** Requests the player makes (seen by the observer) when its first track is set. */
    emitOnFirstSet?: string[];
  };

  function stubPlayerPage(fixture: Fixture) {
    FakeObserver.instances = [];
    const entries = fixture.entries ?? [];
    let sets = 0;
    const player = {
      classList: { contains: (name: string) => name === "ad-showing" && fixture.adShowing === true },
      getPlayerResponse: () => youtubeResponse(YOUTUBE_ID, fixture.tracks),
      getOption: vi.fn(() => fixture.captionTrack ?? {}),
      loadModule: vi.fn(),
      unloadModule: vi.fn(),
      toggleSubtitles: vi.fn(),
      toggleSubtitlesOn: vi.fn(),
      setOption: vi.fn<(namespace: string, option: string, value?: unknown) => void>(() => {
        sets += 1;
        if (sets === 1) {
          for (const name of fixture.emitOnFirstSet ?? []) FakeObserver.instances.forEach((observer) => observer.emit(name));
        }
      }),
    };
    vi.stubGlobal("location", {
      href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`,
      hostname: "www.youtube.com",
      pathname: "/watch",
    });
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: (id: string) => (id === "movie_player" ? player : null),
    });
    vi.stubGlobal("performance", { getEntriesByType: () => entries.map((name) => ({ name })) });
    vi.stubGlobal("PerformanceObserver", FakeObserver);
    return player;
  }

  /** Caption responses are empty unless the request carries the pot, like the real endpoint. */
  function potOnlyFetch() {
    return vi.fn(async (url: string) => url.includes(`pot=${POT}`)
      ? { ok: true, text: async () => JSON.stringify(json3("read with pot")) }
      : { ok: true, text: async () => "" });
  }

  const params = (call: unknown[]): URLSearchParams => new URL(String(call[0])).searchParams;

  it("reuses a matching pot request already in the resource timeline without touching the captions", async () => {
    const player = stubPlayerPage({
      tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }],
      entries: [potUrl(OTHER_YOUTUBE_ID, OTHER_POT), "https://www.youtube.com/api/stats/playback?pot=nope", potUrl()],
    });
    const fetchStub = potOnlyFetch();
    vi.stubGlobal("fetch", fetchStub);
    const consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((method) => vi.spyOn(console, method));

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("read with pot");
    expect(fetchStub).toHaveBeenCalledOnce();
    const query = params(fetchStub.mock.calls[0]!);
    expect(query.get("fmt")).toBe("json3");
    expect(query.get("pot")).toBe(POT);
    for (const [key, value] of Object.entries({
      potc: "1", c: "WEB", cver: "2.20261016.00.00", cplayer: "UNIPLAYER", cos: "Macintosh", cosver: "10_15_7",
      cplatform: "DESKTOP", cbr: "Chrome", cbrver: "140.0.0.0", cbrand: "apple", xorb: "2", xobt: "3", xovt: "3",
    })) expect(query.get(key)).toBe(value);
    // The track keeps its own identity and signature; nothing else is copied from the player's request.
    expect(query.get("ei")).toBe("TRACK_EI");
    expect(query.get("signature")).toBe("TRACK_SIGNATURE");
    expect(query.get("lang")).toBe("en");
    expect(query.get("unrelated")).toBeNull();
    expect(player.setOption).not.toHaveBeenCalled();
    expect(player.loadModule).not.toHaveBeenCalled();
    expect(player.unloadModule).not.toHaveBeenCalled();
    expect(player.toggleSubtitles).not.toHaveBeenCalled();
    expect(FakeObserver.instances).toHaveLength(0);
    expect(JSON.stringify(result)).not.toContain(POT);
    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled();
  });

  it("applies the pot to the WebVTT and original-URL fallbacks of the same track", async () => {
    stubPlayerPage({ tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }], entries: [potUrl()] });
    const fetchStub = vi.fn(async (url: string) => url.includes("fmt=json3")
      ? { ok: true, text: async () => "" }
      : url.includes("fmt=vtt")
        ? { ok: true, text: async () => "" }
        : { ok: true, text: async () => "<transcript><text start=\"1\" dur=\"2\">xml with pot</text></transcript>" });
    vi.stubGlobal("fetch", fetchStub);

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("xml with pot");
    expect(fetchStub).toHaveBeenCalledTimes(3);
    for (const call of fetchStub.mock.calls) expect(params(call).get("pot")).toBe(POT);
    expect(params(fetchStub.mock.calls[2]!).get("fmt")).toBeNull();
  });

  it("enables a track to observe the player's own request, then turns captions back off", async () => {
    vi.useFakeTimers();
    const player = stubPlayerPage({
      tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }],
      entries: [potUrl(OTHER_YOUTUBE_ID, OTHER_POT)],
      emitOnFirstSet: [potUrl(OTHER_YOUTUBE_ID, OTHER_POT), potUrl()],
    });
    const fetchStub = potOnlyFetch();
    vi.stubGlobal("fetch", fetchStub);
    const consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((method) => vi.spyOn(console, method));

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(2_000);
    const result = await pending;

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("read with pot");
    expect(player.loadModule).toHaveBeenCalledWith("captions");
    expect(player.setOption).toHaveBeenCalledOnce();
    expect(player.setOption).toHaveBeenCalledWith("captions", "track", { languageCode: "en" });
    expect(player.unloadModule).toHaveBeenCalledWith("captions");
    expect(player.loadModule.mock.invocationCallOrder[0]).toBeLessThan(player.setOption.mock.invocationCallOrder[0]!);
    expect(player.setOption.mock.invocationCallOrder[0]).toBeLessThan(player.unloadModule.mock.invocationCallOrder[0]!);
    expect(FakeObserver.instances).toHaveLength(1);
    expect(FakeObserver.instances[0]!.observed).toEqual([{ type: "resource" }]);
    expect(FakeObserver.instances[0]!.disconnected).toBe(true);
    // The other video's pot was never used; only this video's pot reached the caption fetch.
    expect(fetchStub).toHaveBeenCalledOnce();
    expect(JSON.stringify(fetchStub.mock.calls)).not.toContain(OTHER_POT);
    expect(params(fetchStub.mock.calls[0]!).get("pot")).toBe(POT);
    expect(JSON.stringify(result)).not.toContain(POT);
    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled();
  });

  it("asks for an auto-generated track by kind so the player requests the ASR captions", async () => {
    vi.useFakeTimers();
    const player = stubPlayerPage({
      tracks: [{ kind: "asr", languageCode: "en", baseUrl: bareUrl("en", "asr") }],
      emitOnFirstSet: [potUrl()],
    });
    vi.stubGlobal("fetch", potOnlyFetch());

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(2_000);
    const result = await pending;

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.source).toBe("auto_caption");
    expect(player.setOption).toHaveBeenCalledWith("captions", "track", { languageCode: "en", kind: "asr" });
  });

  it("restores the viewer's original caption track when captions were on", async () => {
    vi.useFakeTimers();
    const original = { languageCode: "fr", languageName: "Français", vss_id: ".fr" };
    const player = stubPlayerPage({
      tracks: [
        { languageCode: "en", baseUrl: bareUrl("en") },
        { languageCode: "fr", baseUrl: bareUrl("fr") },
      ],
      captionTrack: original,
      emitOnFirstSet: [potUrl()],
    });
    const fetchStub = potOnlyFetch();
    vi.stubGlobal("fetch", fetchStub);

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(2_000);
    const result = await pending;

    // A different track is requested so the player has to fetch again; the viewer's track is read.
    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.language).toBe("fr");
    expect(player.setOption).toHaveBeenCalledTimes(2);
    expect(player.setOption.mock.calls[0]).toEqual(["captions", "track", { languageCode: "en" }]);
    expect(player.setOption.mock.calls[1]![2]).toBe(original);
    expect(player.unloadModule).not.toHaveBeenCalled();
    expect(params(fetchStub.mock.calls[0]!).get("lang")).toBe("fr");
  });

  it("fails with caption_player_not_ready when the player never requests captions, and still restores them", async () => {
    vi.useFakeTimers();
    const player = stubPlayerPage({
      tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }],
      // A request for another video must not satisfy the wait.
      emitOnFirstSet: [potUrl(OTHER_YOUTUBE_ID, OTHER_POT)],
    });
    const fetchStub = potOnlyFetch();
    vi.stubGlobal("fetch", fetchStub);

    const pending = captureYouTubePage();
    await vi.advanceTimersByTimeAsync(12_000);
    const result = await pending;

    expect(result).toEqual({ status: "failed", error: "caption_player_not_ready" });
    // The track request is repeated once, then captions go back to off.
    expect(player.setOption).toHaveBeenCalledTimes(2);
    expect(player.unloadModule).toHaveBeenCalledOnce();
    expect(FakeObserver.instances[0]!.disconnected).toBe(true);
    // Without a pot only the empty bare responses were seen.
    expect(fetchStub).toHaveBeenCalled();
    expect(JSON.stringify(fetchStub.mock.calls)).not.toContain(OTHER_POT);
    expect(JSON.stringify(result)).not.toContain(OTHER_POT);
  });

  it("does not toggle the captions while an ad is showing and asks to retry", async () => {
    const player = stubPlayerPage({
      tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }],
      adShowing: true,
      emitOnFirstSet: [potUrl()],
    });
    const fetchStub = potOnlyFetch();
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_player_not_ready" });

    expect(player.getOption).not.toHaveBeenCalledWith("captions", "tracklist");
    expect(player.setOption).not.toHaveBeenCalled();
    expect(player.loadModule).not.toHaveBeenCalled();
    expect(player.unloadModule).not.toHaveBeenCalled();
    expect(player.toggleSubtitles).not.toHaveBeenCalled();
    expect(player.toggleSubtitlesOn).not.toHaveBeenCalled();
    expect(FakeObserver.instances).toHaveLength(0);
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("still reads captions during an ad when this video's pot request is already in the timeline", async () => {
    const player = stubPlayerPage({
      tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }],
      adShowing: true,
      entries: [potUrl()],
    });
    vi.stubGlobal("fetch", potOnlyFetch());

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    expect(player.setOption).not.toHaveBeenCalled();
  });

  it("reports a player that is on the page but exposes no caption API as not ready", async () => {
    vi.stubGlobal("location", { href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`, hostname: "www.youtube.com", pathname: "/watch" });
    vi.stubGlobal("document", {
      title: "Current title - YouTube",
      getElementById: () => ({ classList: { contains: () => false } }),
    });
    vi.stubGlobal("performance", { getEntriesByType: () => [] });
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl: bareUrl("en") }]));
    vi.stubGlobal("fetch", potOnlyFetch());

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_player_not_ready" });
  });

  it("keeps the legacy bare-URL read when there is no player at all", async () => {
    vi.stubGlobal("location", { href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`, hostname: "www.youtube.com", pathname: "/watch" });
    vi.stubGlobal("document", { title: "Current title - YouTube" });
    vi.stubGlobal("performance", { getEntriesByType: () => [] });
    vi.stubGlobal("ytInitialPlayerResponse", youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl: bareUrl("en") }]));
    const fetchStub = vi.fn(async () => ({ ok: true, text: async () => "" }));
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
    for (const call of fetchStub.mock.calls) expect(params(call).get("pot")).toBeNull();
  });

  it("does not use a pot request that points at an untrusted host or path", async () => {
    stubPlayerPage({
      tracks: [{ languageCode: "en", baseUrl: bareUrl("en") }],
      entries: [
        `https://evil.example/api/timedtext?v=${YOUTUBE_ID}&pot=${POT}`,
        `https://www.youtube.com/api/timedtext/other?v=${YOUTUBE_ID}&pot=${POT}`,
        `http://www.youtube.com/api/timedtext?v=${YOUTUBE_ID}&pot=${POT}`,
      ],
      adShowing: true,
    });
    const fetchStub = potOnlyFetch();
    vi.stubGlobal("fetch", fetchStub);

    await expect(captureYouTubePage()).resolves.toEqual({ status: "failed", error: "caption_player_not_ready" });
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("surfaces caption_player_not_ready through the coordinator as a stable error", async () => {
    const executeScript = vi.fn(async () => [{ result: { status: "failed", error: "caption_player_not_ready" } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(captureActivePage(7, `https://www.youtube.com/watch?v=${YOUTUBE_ID}`)).rejects.toThrow("caption_player_not_ready");
  });

  it("survives the MV3 function serialization boundary on the pot path", async () => {
    const observers: FakeObserver[] = [];
    class SerializedObserver extends FakeObserver {
      constructor(callback: FakeObserverCallback) { super(callback); observers.push(this); }
    }
    const player = {
      classList: { contains: () => false },
      getOption: () => ({}),
      loadModule: vi.fn(),
      unloadModule: vi.fn(),
      setOption: vi.fn(() => observers.forEach((observer) => observer.emit(potUrl()))),
    };
    const fetchStub = potOnlyFetch();
    const adapter = recreateAdapter(captureYouTubePage, {
      Date,
      clearTimeout,
      URL,
      location: { href: `https://www.youtube.com/watch?v=${YOUTUBE_ID}`, hostname: "www.youtube.com", pathname: "/watch" },
      document: { title: "Serialized player - YouTube", getElementById: () => player },
      setTimeout,
      performance: { getEntriesByType: () => [] },
      PerformanceObserver: SerializedObserver,
      ytInitialPlayerResponse: youtubeResponse(YOUTUBE_ID, [{ languageCode: "en", baseUrl: bareUrl("en") }]),
      fetch: fetchStub,
    });

    const result = await adapter();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("read with pot");
    expect(player.setOption).toHaveBeenCalledOnce();
    expect(player.unloadModule).toHaveBeenCalledWith("captions");
    expect(observers[0]?.disconnected).toBe(true);
    expect(JSON.stringify(result)).not.toContain(POT);
  });
});

describe("capture attempt shape", () => {
  it("keeps the public PageCapture shape inside captured attempts", () => {
    const attempt: CaptureAttempt = { status: "captured", capture: availableCapture() };
    expect(attempt.capture).not.toHaveProperty("captionUrl");
    expect(attempt.capture).not.toHaveProperty("ks");
  });
});

describe("YouTube description and chapters", () => {
  function stubYouTube(): void {
    const href = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
    vi.stubGlobal("location", { href, hostname: "www.youtube.com", pathname: "/watch" });
    vi.stubGlobal("document", { title: "Current title - YouTube" });
  }

  function markers(chapters: Array<{ title: unknown; startMs: unknown }>, key = "DESCRIPTION_CHAPTERS") {
    return {
      playerOverlays: { playerOverlayRenderer: { decoratedPlayerBarRenderer: { decoratedPlayerBarRenderer: { playerBar: {
        multiMarkersPlayerBarRenderer: {
          markersMap: [{ key, value: { chapters: chapters.map((chapter) => ({
            chapterRenderer: { title: chapter.title, timeRangeStartMillis: chapter.startMs },
          })) } }],
        },
      } } } } },
    };
  }

  function stubNoCaptionVideo(extra: Record<string, unknown> = {}, details: Record<string, unknown> = {}): void {
    const response = youtubeResponse(YOUTUBE_ID, []);
    vi.stubGlobal("ytInitialPlayerResponse", {
      ...response,
      videoDetails: { ...(response.videoDetails as Record<string, unknown>), ...details },
    });
    if (Object.keys(extra).length) vi.stubGlobal("ytInitialData", extra);
  }

  async function readMetadata(): Promise<PageCapture["metadata"]> {
    const result = await captureYouTubePage();
    expect(result.status).toBe("no_caption");
    if (result.status !== "no_caption") throw new Error("unreachable");
    expect(normalizePageCapture(result.capture)).not.toBeNull();
    return result.capture.metadata;
  }

  it("reads the description keeping its lines and dropping control characters", async () => {
    stubYouTube();
    stubNoCaptionVideo({}, { shortDescription: "  Intro line\r\nSecond\u0000 line\u0007\n\nLast  " });

    const metadata = await readMetadata();

    expect(metadata.description).toBe("Intro line\nSecond line\n\nLast");
  });

  it("leaves the description null when absent or blank and bounds it to 50,000 characters", async () => {
    stubYouTube();
    stubNoCaptionVideo({}, { shortDescription: "   " });
    expect((await readMetadata()).description).toBeNull();

    vi.unstubAllGlobals();
    stubYouTube();
    stubNoCaptionVideo();
    expect((await readMetadata()).description).toBeNull();

    vi.unstubAllGlobals();
    stubYouTube();
    stubNoCaptionVideo({}, { shortDescription: "x".repeat(80_000) });
    expect((await readMetadata()).description).toHaveLength(50_000);
  });

  it("reads player-bar chapters with end times from the next start and the duration", async () => {
    stubYouTube();
    stubNoCaptionVideo({
      currentVideoEndpoint: { watchEndpoint: { videoId: YOUTUBE_ID } },
      ...markers([
        { title: { simpleText: "Intro" }, startMs: 0 },
        { title: { runs: [{ text: "Main " }, { text: "part" }] }, startMs: "30000" },
        { title: { simpleText: "Outro" }, startMs: 90_500 },
      ]),
    });

    const metadata = await readMetadata();

    expect(metadata.chapters).toEqual([
      { title: "Intro", start_sec: 0, end_sec: 30 },
      { title: "Main part", start_sec: 30, end_sec: 90.5 },
      { title: "Outro", start_sec: 90.5, end_sec: 120 },
    ]);
  });

  it("prefers description chapters over auto chapters and sorts, dedupes and bounds them", async () => {
    stubYouTube();
    const many = Array.from({ length: 1_200 }, (_value, index) => ({ title: { simpleText: `Chapter ${index}` }, startMs: (index + 1) * 1_000 }));
    stubNoCaptionVideo({
      currentVideoEndpoint: { watchEndpoint: { videoId: YOUTUBE_ID } },
      ...markers([{ title: { simpleText: "Auto" }, startMs: 0 }], "AUTO_CHAPTERS"),
      engagementPanels: [markers([
        { title: { simpleText: "B" }, startMs: 20_000 },
        { title: { simpleText: "A" }, startMs: 0 },
        { title: { simpleText: "A again" }, startMs: 0 },
        { title: { simpleText: "   " }, startMs: 5_000 },
        { title: { simpleText: "Bad time" }, startMs: -1 },
        { title: { simpleText: "No time" }, startMs: null },
      ])],
    });
    let metadata = await readMetadata();
    expect(metadata.chapters.map((chapter) => chapter.title)).toEqual(["A", "B"]);
    expect(metadata.chapters[0]).toEqual({ title: "A", start_sec: 0, end_sec: 20 });

    vi.unstubAllGlobals();
    stubYouTube();
    stubNoCaptionVideo({
      currentVideoEndpoint: { watchEndpoint: { videoId: YOUTUBE_ID } },
      ...markers([{ title: { simpleText: "x".repeat(900) }, startMs: 0 }, ...many]),
    });
    metadata = await readMetadata();
    expect(metadata.chapters).toHaveLength(1_000);
    expect(metadata.chapters[0]?.title).toHaveLength(500);
    expect(metadata.chapters.every((chapter) => chapter.title.length <= 500 && (chapter.end_sec === null || chapter.end_sec >= chapter.start_sec))).toBe(true);
    // The duration (120 s) precedes the last kept chapter, so its end stays unknown.
    expect(metadata.chapters.at(-1)?.end_sec).toBeNull();
  });

  it("returns no chapters when the shape is missing or the data belongs to another video", async () => {
    stubYouTube();
    stubNoCaptionVideo({ currentVideoEndpoint: { watchEndpoint: { videoId: YOUTUBE_ID } }, contents: { unrelated: true } });
    expect((await readMetadata()).chapters).toEqual([]);

    vi.unstubAllGlobals();
    stubYouTube();
    stubNoCaptionVideo({
      currentVideoEndpoint: { watchEndpoint: { videoId: OTHER_YOUTUBE_ID } },
      ...markers([{ title: { simpleText: "Stale" }, startMs: 0 }]),
    });
    expect((await readMetadata()).chapters).toEqual([]);

    vi.unstubAllGlobals();
    stubYouTube();
    stubNoCaptionVideo(markers([{ title: { simpleText: "Unverifiable" }, startMs: 0 }]));
    expect((await readMetadata()).chapters).toEqual([]);

    vi.unstubAllGlobals();
    stubYouTube();
    stubNoCaptionVideo({ currentVideoEndpoint: { watchEndpoint: { videoId: YOUTUBE_ID } }, ...markers([{ title: { simpleText: "Bad" }, startMs: "soon" }]) });
    expect((await readMetadata()).chapters).toEqual([]);
  });

  it("carries the description and chapters on a captioned capture too", async () => {
    stubYouTube();
    vi.stubGlobal("ytInitialPlayerResponse", {
      videoDetails: { videoId: YOUTUBE_ID, title: "Current title", author: "Author", lengthSeconds: "120", shortDescription: "About this video" },
      captions: { playerCaptionsTracklistRenderer: { captionTracks: [{ languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?lang=en" }] } },
    });
    vi.stubGlobal("ytInitialData", {
      currentVideoEndpoint: { watchEndpoint: { videoId: YOUTUBE_ID } },
      ...markers([{ title: { simpleText: "Only" }, startMs: 0 }]),
    });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => json3("hello") })));

    const result = await captureYouTubePage();

    expect(result.status).toBe("captured");
    if (result.status === "captured") {
      expect(result.capture.metadata.description).toBe("About this video");
      expect(result.capture.metadata.chapters).toEqual([{ title: "Only", start_sec: 0, end_sec: 120 }]);
      expect(normalizePageCapture(result.capture)).not.toBeNull();
    }
  });
});

describe("page URL classification", () => {
  it("accepts YouTube watch, shorts and youtu.be pages", () => {
    expect(classifyPageUrl(`https://www.youtube.com/watch?v=${YOUTUBE_ID}`)).toBe("youtube");
    expect(classifyPageUrl(`https://youtube.com/shorts/${YOUTUBE_ID}`)).toBe("youtube");
    expect(classifyPageUrl(`https://youtu.be/${YOUTUBE_ID}`)).toBe("youtube");
  });

  it("rejects other hosts, non-HTTPS URLs, the YouTube home page and malformed input", () => {
    expect(classifyPageUrl("https://www.youtube.com/")).toBeNull();
    expect(classifyPageUrl("https://www.youtube.com/watch?v=short")).toBeNull();
    expect(classifyPageUrl(`http://www.youtube.com/watch?v=${YOUTUBE_ID}`)).toBeNull();
    expect(classifyPageUrl("https://example.com/watch?v=abcdefghijk")).toBeNull();
    expect(classifyPageUrl("https://ntulearn.ntu.edu.sg/ultra/courses/x")).toBeNull();
    expect(classifyPageUrl("not a url")).toBeNull();
  });
});

type FakeVideo = {
  readyState: number;
  duration: number;
  paused: boolean;
  ended: boolean;
  currentTime: number;
};

function fakeVideo(overrides: Partial<FakeVideo> = {}): FakeVideo {
  return { readyState: 4, duration: 600, paused: false, ended: false, currentTime: 12, ...overrides };
}

function stubPlayerDocument(options: {
  main?: FakeVideo[];
  inPlayer?: FakeVideo[];
  all?: FakeVideo[];
  moviePlayer?: { classList: { contains: (name: string) => boolean }; seekTo?: (seconds: number, allow: boolean) => void } | null;
}): void {
  vi.stubGlobal("document", {
    querySelectorAll: (selector: string) => {
      if (selector === "video.html5-main-video") return options.main ?? [];
      if (selector === "#movie_player video") return options.inPlayer ?? [];
      if (selector === "video") return options.all ?? [];
      return [];
    },
    getElementById: (id: string) => (id === "movie_player" ? options.moviePlayer ?? null : null),
  });
}

describe("controlPlayer (injected player probe and seek)", () => {
  it("reads the YouTube main video and ignores hover-preview videos", () => {
    const main = fakeVideo({ currentTime: 83.5 });
    const preview = fakeVideo({ currentTime: 3 });
    stubPlayerDocument({ main: [main], all: [preview, main], moviePlayer: { classList: { contains: () => false } } });

    expect(controlPlayer(null, "youtube")).toEqual({ status: "video", current_sec: 83.5, ready: true, playing: true, seeked: false });
    expect(main.currentTime).toBe(83.5);
  });

  it("falls back to a video inside the movie player and reports a missing video", () => {
    stubPlayerDocument({ inPlayer: [fakeVideo({ paused: true, currentTime: 7 })] });
    expect(controlPlayer(null, "youtube")).toEqual({ status: "video", current_sec: 7, ready: true, playing: false, seeked: false });

    vi.unstubAllGlobals();
    stubPlayerDocument({});
    expect(controlPlayer(null, "youtube")).toEqual({ status: "no_video" });
  });

  it("treats a playing ad as no video so the ad is never read or seeked", () => {
    const main = fakeVideo({ currentTime: 4 });
    const seekTo = vi.fn();
    stubPlayerDocument({ main: [main], moviePlayer: { classList: { contains: (name) => name === "ad-showing" }, seekTo } });

    expect(controlPlayer(null, "youtube")).toEqual({ status: "no_video" });
    expect(controlPlayer(90, "youtube")).toEqual({ status: "no_video" });
    expect(seekTo).not.toHaveBeenCalled();
    expect(main.currentTime).toBe(4);
  });

  it("seeks YouTube through movie_player.seekTo with seek-ahead", () => {
    const main = fakeVideo();
    const seekTo = vi.fn();
    stubPlayerDocument({ main: [main], moviePlayer: { classList: { contains: () => false }, seekTo } });

    const result = controlPlayer(125.5, "youtube");

    expect(seekTo).toHaveBeenCalledWith(125.5, true);
    expect(result).toMatchObject({ status: "video", seeked: true });
    expect(main.currentTime).toBe(12);
  });

  it("falls back to the media element when seekTo is missing or throws, and clamps to the duration", () => {
    const withoutSeekTo = fakeVideo();
    stubPlayerDocument({ main: [withoutSeekTo], moviePlayer: { classList: { contains: () => false } } });
    expect(controlPlayer(125.5, "youtube")).toMatchObject({ status: "video", seeked: true, current_sec: 125.5 });
    expect(withoutSeekTo.currentTime).toBe(125.5);

    vi.unstubAllGlobals();
    const throwing = fakeVideo({ duration: 100 });
    stubPlayerDocument({ main: [throwing], moviePlayer: { classList: { contains: () => false }, seekTo: () => { throw new Error("player replaced"); } } });
    expect(controlPlayer(500, "youtube")).toMatchObject({ status: "video", seeked: true, current_sec: 100 });
    expect(throwing.currentTime).toBe(100);
  });

  it("reports an unseeked probe when the element refuses the assignment", () => {
    const refusing = Object.defineProperty(fakeVideo(), "currentTime", { get: () => 9, set: () => { throw new Error("refused"); } });
    stubPlayerDocument({ main: [refusing] });

    expect(controlPlayer(30, "youtube")).toMatchObject({ status: "video", seeked: false, current_sec: 9 });
  });

  it("picks the ready, playing video among several and seeks only that one", () => {
    const idle = fakeVideo({ readyState: 0, duration: Number.NaN, currentTime: 0 });
    const paused = fakeVideo({ paused: true, currentTime: 30 });
    const playing = fakeVideo({ currentTime: 61.25 });
    stubPlayerDocument({ main: [idle, paused, playing] });

    expect(controlPlayer(null, "youtube")).toEqual({ status: "video", current_sec: 61.25, ready: true, playing: true, seeked: false });

    const result = controlPlayer(300, "youtube");

    expect(result).toMatchObject({ status: "video", seeked: true, current_sec: 300 });
    expect(playing.currentTime).toBe(300);
    expect(paused.currentTime).toBe(30);
    expect(idle.currentTime).toBe(0);
  });

  it("reports a not-ready video honestly and survives a hostile document", () => {
    stubPlayerDocument({ main: [fakeVideo({ readyState: 0, currentTime: 0 })] });
    expect(controlPlayer(null, "youtube")).toEqual({ status: "video", current_sec: 0, ready: false, playing: false, seeked: false });

    vi.unstubAllGlobals();
    vi.stubGlobal("document", { querySelectorAll: () => { throw new Error("hostile"); } });
    expect(controlPlayer(null, "youtube")).toEqual({ status: "no_video" });
  });

  it("stays self-contained when serialised into another JavaScript context", () => {
    const video = fakeVideo({ currentTime: 20 });
    const moviePlayer = { classList: { contains: () => false }, seekTo: vi.fn() };
    const injected = runInNewContext(`(${controlPlayer.toString()})`, {
      document: {
        querySelectorAll: (selector: string) => (selector === "video.html5-main-video" ? [video] : []),
        getElementById: () => moviePlayer,
      },
    }) as (seekSec: number | null, kind: string) => PlayerProbe;

    expect(injected(null, "youtube")).toMatchObject({ status: "video", current_sec: 20 });
    expect(injected(44, "youtube")).toMatchObject({ status: "video", seeked: true });
    expect(moviePlayer.seekTo).toHaveBeenCalledWith(44, true);
  });
});

describe("playback coordinator", () => {
  const youtubeUrl = `https://www.youtube.com/watch?v=${YOUTUBE_ID}`;
  const video = (overrides: Partial<Extract<PlayerProbe, { status: "video" }>> = {}): PlayerProbe => ({
    status: "video", current_sec: 10, ready: true, playing: true, seeked: false, ...overrides,
  });

  it("validates seek positions", () => {
    expect(isSeekSeconds(0)).toBe(true);
    expect(isSeekSeconds(3600.5)).toBe(true);
    for (const value of [-1, Number.NaN, Number.POSITIVE_INFINITY, 31_536_001, "5", null, undefined]) {
      expect(isSeekSeconds(value)).toBe(false);
    }
  });

  it("reads YouTube playback from the top frame only, passing the arguments to the injected function", async () => {
    const executeScript = vi.fn(async () => [{ frameId: 0, result: video({ current_sec: 83.45678 }) }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(readPlaybackSeconds(9, youtubeUrl)).resolves.toBe(83.457);

    expect(executeScript).toHaveBeenCalledOnce();
    expect(executeScript).toHaveBeenCalledWith({ target: { tabId: 9, allFrames: false }, world: "MAIN", func: controlPlayer, args: [null, "youtube"] });
  });

  it("returns null playback when there is no video, it is not ready, or the injection fails", async () => {
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ frameId: 0, result: { status: "no_video" } }]) } });
    await expect(readPlaybackSeconds(9, youtubeUrl)).resolves.toBeNull();

    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ frameId: 0, result: video({ ready: false, current_sec: 0 }) }]) } });
    await expect(readPlaybackSeconds(9, youtubeUrl)).resolves.toBeNull();

    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => { throw new Error("no access"); }) } });
    await expect(readPlaybackSeconds(9, youtubeUrl)).resolves.toBeNull();
  });

  it("rejects unsupported pages for both operations", async () => {
    const executeScript = vi.fn();
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(readPlaybackSeconds(9, "https://example.com/")).rejects.toThrow("unsupported_page");
    await expect(seekPlayback(9, "https://www.youtube.com/", 5)).rejects.toThrow("unsupported_page");
    expect(executeScript).not.toHaveBeenCalled();
  });

  it("skips malformed result entries and reads the first usable probe", async () => {
    const executeScript = vi.fn(async () => [
      { frameId: 9, error: "Frame URL contained ks=private-signature" },
      { frameId: 11, result: { status: "video", current_sec: "bad" } },
      null,
      { frameId: 0, result: video({ current_sec: 321.5 }) },
    ]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(readPlaybackSeconds(4, youtubeUrl)).resolves.toBe(321.5);
    expect(executeScript).toHaveBeenCalledOnce();
  });

  it("seeks YouTube in the top frame and fails when no player accepted the position", async () => {
    const executeScript = vi.fn(async () => [{ frameId: 0, result: video({ seeked: true, current_sec: 125.5 }) }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(seekPlayback(9, youtubeUrl, 125.5)).resolves.toBeUndefined();
    expect(executeScript).toHaveBeenCalledOnce();
    expect(executeScript).toHaveBeenCalledWith({ target: { tabId: 9, allFrames: false }, world: "MAIN", func: controlPlayer, args: [125.5, "youtube"] });

    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ frameId: 0, result: { status: "no_video" } }]) } });
    await expect(seekPlayback(9, youtubeUrl, 5)).rejects.toThrow("seek_failed");

    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => { throw new Error("no access"); }) } });
    await expect(seekPlayback(9, youtubeUrl, 5)).rejects.toThrow("seek_failed");
  });

  it("rejects invalid seek positions before touching the page", async () => {
    const executeScript = vi.fn();
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    for (const value of [-1, Number.NaN, Number.POSITIVE_INFINITY, 31_536_001]) {
      await expect(seekPlayback(9, youtubeUrl, value)).rejects.toThrow("invalid_command");
    }
    expect(executeScript).not.toHaveBeenCalled();
  });

  it("treats NTULearn and Kaltura pages as unsupported for playback and seeking", async () => {
    const executeScript = vi.fn();
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(readPlaybackSeconds(4, "https://ntulearn.ntu.edu.sg/ultra/courses/example")).rejects.toThrow("unsupported_page");
    await expect(seekPlayback(4, "https://ntulearnv1.ntu.edu.sg/media/52_6ce010d", 30)).rejects.toThrow("unsupported_page");
    expect(executeScript).not.toHaveBeenCalled();
  });
});

const BVID = "BV1xx411c7mD";
const BILIBILI_URL = `https://www.bilibili.com/video/${BVID}`;

describe("Bilibili page classification", () => {
  it("accepts BV and av video pages on www.bilibili.com, with any part", () => {
    expect(classifyPageUrl(BILIBILI_URL)).toBe("bilibili");
    expect(classifyPageUrl(`${BILIBILI_URL}/?p=3&spm_id_from=333.1007`)).toBe("bilibili");
    expect(classifyPageUrl("https://www.bilibili.com/video/av170001")).toBe("bilibili");
    expect(classifyPageUrl("https://www.bilibili.com/video/bv1xx411c7mD")).toBe("bilibili");
  });

  it("rejects other Bilibili pages, near-hosts, plain HTTP and malformed ids", () => {
    for (const url of [
      "https://www.bilibili.com/",
      "https://www.bilibili.com/bangumi/play/ep1",
      "https://www.bilibili.com/video/BV1xx411c7m",
      "https://www.bilibili.com/video/BV1xx411c7mDx",
      "https://www.bilibili.com/video/av",
      "https://www.bilibili.com/video/BV1xx411c7mD/extra",
      "https://m.bilibili.com/video/BV1xx411c7mD",
      "https://bilibili.com/video/BV1xx411c7mD",
      "https://www.bilibili.com.evil.example/video/BV1xx411c7mD",
      "https://evilbilibili.com/video/BV1xx411c7mD",
      "http://www.bilibili.com/video/BV1xx411c7mD",
    ]) {
      expect(classifyPageUrl(url), url).toBeNull();
    }
  });

  it("derives the connector's normalized id and the part from the URL", () => {
    expect(bilibiliVideoRef(BILIBILI_URL)).toEqual({ id: BVID, part: 1 });
    expect(bilibiliVideoRef("https://www.bilibili.com/video/bv1xx411c7mD/?p=2")).toEqual({ id: BVID, part: 2 });
    expect(bilibiliVideoRef("https://www.bilibili.com/video/AV170001?p=12")).toEqual({ id: "av170001", part: 12 });
    for (const bad of ["0", "abc", "-1", "10000", "01", ""]) {
      expect(bilibiliVideoRef(`${BILIBILI_URL}?p=${bad}`)?.part, bad).toBe(1);
    }
    expect(bilibiliVideoRef(`${BILIBILI_URL}?p=2&p=3`)?.part).toBe(1);
    expect(bilibiliVideoRef("https://example.com/video/BV1xx411c7mD")).toBeNull();
  });
});

describe("Bilibili capture coordinator", () => {
  function bilibiliCapture(overrides: Partial<PageCapture> = {}): PageCapture {
    return {
      platform: "bilibili",
      platform_id: BVID,
      canonical_url: BILIBILI_URL,
      page_url: BILIBILI_URL,
      metadata: {
        title: "梯度下降入门",
        author: "UP主",
        duration_sec: 600,
        language: "zh",
        description: null,
        cover_url: "https://i0.hdslb.com/bfs/archive/abc.jpg",
        tags: [],
        chapters: [],
      },
      caption: { status: "available", source: "auto_caption", language: "zh", cues: [{ start_sec: 1, end_sec: 2, text: "你好" }] },
      ...overrides,
    };
  }

  it("injects the Bilibili adapter into the top frame only", async () => {
    const executeScript = vi.fn(async () => [{ result: { status: "captured", capture: bilibiliCapture() } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    const result = await captureActivePage(7, `${BILIBILI_URL}?p=1&spm_id_from=x`);

    expect(result.platform).toBe("bilibili");
    expect(executeScript).toHaveBeenCalledOnce();
    expect(executeScript).toHaveBeenCalledWith({ target: { tabId: 7, allFrames: false }, world: "MAIN", func: captureBilibiliPage });
  });

  it("keeps a later part in the page URL and rebuilds the capture from an allowlist", async () => {
    const capture = bilibiliCapture({ page_url: `${BILIBILI_URL}?p=3` }) as PageCapture & Record<string, unknown>;
    capture.auth_key = "private-signature";
    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ result: { status: "captured", capture } }]) } });

    const result = await captureActivePage(7, `${BILIBILI_URL}?p=3`);

    expect(result.page_url).toBe(`${BILIBILI_URL}?p=3`);
    expect(JSON.stringify(result)).not.toContain("private-signature");
  });

  it("rejects captures that break the Bilibili runtime contract", async () => {
    const withMetadata = (change: Partial<PageCapture["metadata"]>) => {
      const capture = bilibiliCapture();
      capture.metadata = { ...capture.metadata, ...change };
      return capture;
    };
    const invalid: PageCapture[] = [
      bilibiliCapture({ platform_id: "dQw4w9WgXcQ" }),
      bilibiliCapture({ platform_id: "bv1xx411c7mD" }),
      bilibiliCapture({ canonical_url: `${BILIBILI_URL}?p=2` }),
      bilibiliCapture({ canonical_url: "https://www.bilibili.com/video/BV1xx411c7mE" }),
      bilibiliCapture({ canonical_url: "https://m.bilibili.com/video/" + BVID }),
      bilibiliCapture({ page_url: `${BILIBILI_URL}?p=1` }),
      bilibiliCapture({ page_url: `${BILIBILI_URL}?p=2&spm=x` }),
      bilibiliCapture({ page_url: `${BILIBILI_URL}#frag` }),
      bilibiliCapture({ page_url: "https://www.bilibili.com/video/BV1xx411c7mE" }),
      withMetadata({ cover_url: "http://i0.hdslb.com/bfs/archive/abc.jpg" }),
      withMetadata({ cover_url: "https://i0.hdslb.com/bfs/archive/abc.jpg?sign=secret" }),
      withMetadata({ cover_url: "https://hdslb.com.evil.example/abc.jpg" }),
      withMetadata({ cover_url: "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg" }),
    ];
    for (const capture of invalid) {
      expect(normalizePageCapture(capture), JSON.stringify([capture.platform_id, capture.canonical_url, capture.page_url, capture.metadata.cover_url])).toBeNull();
    }
    expect(normalizePageCapture(bilibiliCapture({ platform_id: "av170001", canonical_url: "https://www.bilibili.com/video/av170001", page_url: "https://www.bilibili.com/video/av170001?p=12" }))).not.toBeNull();
  });
});

describe("Bilibili caption adapter", () => {
  const AID = 170001;
  const CID = 1001;
  const SUBTITLE_PATH = "/bfs/subtitle/zh.json";
  const AI_SUBTITLE_URL = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/secret-path?auth_key=SECRET_AUTH_KEY";

  const videoData = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
    bvid: BVID,
    aid: AID,
    cid: CID,
    title: "梯度下降入门",
    desc: "本期介绍梯度下降",
    owner: { mid: 9, name: "UP主" },
    duration: 600,
    pic: "http://i0.hdslb.com/bfs/archive/abc.jpg@100w.jpg?share_source=copy_web",
    pages: [{ cid: CID, page: 1, part: "", duration: 600 }],
    ...overrides,
  });
  const initialState = (video: Record<string, unknown> | undefined = videoData(), extra: Record<string, unknown> = {}) => ({
    bvid: BVID,
    aid: AID,
    videoData: video,
    upData: { name: "UP主" },
    tags: [{ tag_name: "机器学习" }, { tag_name: "机器学习" }, { tag_name: "<b>教程</b>" }],
    p: 1,
    ...extra,
  });
  const track = (lan: string, url: string, extra: Record<string, unknown> = {}) => ({
    id: 1, lan, lan_doc: lan, is_lock: false, subtitle_url: url, type: lan.startsWith("ai-") ? 1 : 0, ...extra,
  });
  const playerInfo = (subtitles: unknown[], data: Record<string, unknown> = {}) => ({
    code: 0,
    message: "0",
    data: { aid: AID, cid: CID, need_login_subtitle: false, subtitle: { subtitles }, view_points: [], ...data },
  });
  const subtitleJson = (...lines: Array<[number, number, string]>) => ({
    body: lines.map(([from, to, content]) => ({ from, to, content, location: 2 })),
  });
  const json = (body: unknown, init: ResponseInit = {}): Response => new Response(JSON.stringify(body), { status: 200, ...init });

  type Route = (url: URL, init?: RequestInit) => Response | Promise<Response>;
  type RouteMap = Partial<Record<"view" | "player" | "subtitle", Route>>;
  function stubBilibili(options: {
    href?: string;
    state?: unknown;
    routes: RouteMap;
    title?: string;
  }): { fetchStub: ReturnType<typeof vi.fn>; urls: () => string[] } {
    const href = options.href ?? BILIBILI_URL;
    const parsed = new URL(href);
    vi.stubGlobal("location", { href, hostname: parsed.hostname, pathname: parsed.pathname });
    vi.stubGlobal("document", { title: options.title ?? "梯度下降入门_哔哩哔哩_bilibili" });
    vi.stubGlobal("__INITIAL_STATE__", options.state);
    const fetchStub = vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input);
      if (url.hostname === "api.bilibili.com" && url.pathname === "/x/web-interface/view") {
        return options.routes.view ? options.routes.view(url, init) : new Response("{}", { status: 404 });
      }
      if (url.hostname === "api.bilibili.com" && url.pathname === "/x/player/wbi/v2") {
        return options.routes.player ? options.routes.player(url, init) : new Response("{}", { status: 404 });
      }
      return options.routes.subtitle ? options.routes.subtitle(url, init) : new Response("{}", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchStub);
    return { fetchStub, urls: () => fetchStub.mock.calls.map(([input]) => String(input)) };
  }

  it("reads a human Chinese subtitle with the page's own state and keeps the capture secret-free", async () => {
    const { fetchStub, urls } = stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", `//i0.hdslb.com${SUBTITLE_PATH}?auth_key=SECRET_AUTH_KEY`)])),
        subtitle: () => json(subtitleJson([0, 1.5, "大家好"], [1.5, 4, "今天讲 <i>梯度</i> 下降 &amp; 学习率"])),
      },
    });

    const result = await captureBilibiliPage();

    expect(result).toEqual({
      status: "captured",
      capture: {
        platform: "bilibili",
        platform_id: BVID,
        canonical_url: BILIBILI_URL,
        page_url: BILIBILI_URL,
        metadata: {
          title: "梯度下降入门",
          author: "UP主",
          duration_sec: 600,
          language: "zh",
          description: "本期介绍梯度下降",
          cover_url: "https://i0.hdslb.com/bfs/archive/abc.jpg@100w.jpg",
          tags: ["机器学习", "教程"],
          chapters: [],
        },
        caption: {
          status: "available",
          source: "official_cc",
          language: "zh",
          cues: [
            { start_sec: 0, end_sec: 1.5, text: "大家好" },
            { start_sec: 1.5, end_sec: 4, text: "今天讲 梯度 下降 & 学习率" },
          ],
        },
      },
    });
    expect(urls()).toEqual([
      `https://api.bilibili.com/x/player/wbi/v2?aid=${AID}&cid=${CID}`,
      `https://i0.hdslb.com${SUBTITLE_PATH}?auth_key=SECRET_AUTH_KEY`,
    ]);
    // Cookies go to the player API only; the signed CDN URL is fetched without them.
    expect(fetchStub.mock.calls.map(([, init]) => (init as RequestInit).credentials)).toEqual(["include", "omit"]);
    expect(JSON.stringify(result)).not.toContain("SECRET_AUTH_KEY");
    // The result round-trips the extension's own validation.
    expect(normalizePageCapture((result as Extract<CaptureAttempt, { status: "captured" }>).capture)).not.toBeNull();
  });

  it("reads an AI subtitle behind the viewer's login and labels it as an automatic caption", async () => {
    const { urls } = stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("ai-zh", AI_SUBTITLE_URL)])),
        subtitle: () => json(subtitleJson([2, 3, "自动识别的字幕"])),
      },
    });

    const result = await captureBilibiliPage();

    expect(result.status).toBe("captured");
    if (result.status !== "captured") return;
    expect(result.capture.caption).toMatchObject({ status: "available", source: "auto_caption", language: "zh" });
    expect(urls()[1]).toBe("https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/secret-path?auth_key=SECRET_AUTH_KEY");
    expect(JSON.stringify(result)).not.toContain("SECRET_AUTH_KEY");
  });

  it("prefers human Chinese, then any human, then AI Chinese, then any AI", async () => {
    const pick = async (lans: string[]): Promise<string> => {
      vi.unstubAllGlobals();
      const { urls } = stubBilibili({
        state: initialState(),
        routes: {
          player: () => json(playerInfo(lans.map((lan) => track(lan, `//i0.hdslb.com/bfs/subtitle/${lan}.json`)))),
          subtitle: () => json(subtitleJson([0, 1, "字幕"])),
        },
      });
      await captureBilibiliPage();
      return urls()[1]!;
    };

    expect(await pick(["ai-zh", "en-US", "zh-Hans"])).toContain("/zh-Hans.json");
    expect(await pick(["ai-zh", "ja", "en-US"])).toContain("/ja.json");
    expect(await pick(["ai-en", "ai-zh"])).toContain("/ai-zh.json");
    expect(await pick(["ai-en", "ai-ja"])).toContain("/ai-en.json");
  });

  it("falls through an unreadable subtitle to the next candidate and gives up after three", async () => {
    let calls = 0;
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([
          track("zh-CN", "//i0.hdslb.com/a.json"),
          track("zh-TW", "//i1.hdslb.com/b.json"),
          track("ai-zh", "//i2.hdslb.com/c.json"),
        ])),
        subtitle: () => {
          calls += 1;
          return calls === 1 ? new Response("denied", { status: 403 }) : json(subtitleJson([0, 1, "第二条字幕"]));
        },
      },
    });

    const result = await captureBilibiliPage();

    expect(result.status).toBe("captured");
    expect(calls).toBe(2);

    vi.unstubAllGlobals();
    calls = 0;
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo(["a", "b", "c", "d"].map((name) => track("zh-CN", `//i0.hdslb.com/${name}.json`)))),
        subtitle: () => { calls += 1; return new Response("denied", { status: 403 }); },
      },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(calls).toBe(3);
  });

  it("reports unreadable subtitle bodies as parse failures", async () => {
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])),
        subtitle: () => new Response("<html>not json</html>", { status: 200 }),
      },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });

    vi.unstubAllGlobals();
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])),
        subtitle: () => json({ body: [{ from: "bad", to: 1, content: "x" }, { from: 2, to: 3, content: "   " }] }),
      },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("returns no captions with metadata and chapters when the video has no subtitles", async () => {
    const { fetchStub } = stubBilibili({
      state: initialState(videoData({ desc: "-" })),
      routes: {
        player: () => json(playerInfo(
          [track("ai-zh", "", { is_lock: true })],
          {
            need_login_subtitle: true,
            view_points: [
              { type: 2, from: 120, to: 300, content: "学习率" },
              { type: 2, from: 0, to: 120, content: "开场 <b>介绍</b>" },
              { type: 2, from: 120, to: 200, content: "重复起点" },
              { type: 2, from: -5, to: 1, content: "负数" },
              { type: 2, from: 300, to: 600, content: "" },
            ],
          },
        )),
      },
    });

    const result = await captureBilibiliPage();

    expect(result.status).toBe("no_caption");
    if (result.status !== "no_caption") return;
    expect(result.capture.caption).toEqual({ status: "unavailable", source: null, language: null, cues: [] });
    expect(result.capture.metadata).toMatchObject({
      title: "梯度下降入门",
      author: "UP主",
      duration_sec: 600,
      language: null,
      description: null,
      chapters: [
        { title: "开场 介绍", start_sec: 0, end_sec: 120 },
        { title: "学习率", start_sec: 120, end_sec: 600 },
      ],
    });
    expect(fetchStub).toHaveBeenCalledOnce();
    expect(normalizePageCapture(result.capture)).not.toBeNull();
  });

  it("selects the cid of the requested part and names it in the title", async () => {
    const pages = [
      { cid: 1001, page: 1, part: "第一讲", duration: 100 },
      { cid: 2002, page: 2, part: "第二讲 <b>续</b>", duration: 200 },
    ];
    const { urls } = stubBilibili({
      href: `${BILIBILI_URL}?p=2&spm_id_from=333`,
      state: initialState(videoData({ pages, duration: 300 })),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")], { cid: 2002 })),
        subtitle: () => json(subtitleJson([0, 1, "第二讲的内容"])),
      },
    });

    const result = await captureBilibiliPage();

    expect(urls()[0]).toBe(`https://api.bilibili.com/x/player/wbi/v2?aid=${AID}&cid=2002`);
    expect(result.status).toBe("captured");
    if (result.status !== "captured") return;
    expect(result.capture.page_url).toBe(`${BILIBILI_URL}?p=2`);
    expect(result.capture.canonical_url).toBe(BILIBILI_URL);
    expect(result.capture.metadata.title).toBe("梯度下降入门 p02 第二讲 续");
    expect(result.capture.metadata.duration_sec).toBe(200);
    expect(normalizePageCapture(result.capture)).not.toBeNull();
  });

  it("falls back to the first part's cid for single-part state and rejects an out-of-range part", async () => {
    const { urls } = stubBilibili({
      state: initialState(videoData({ pages: undefined })),
      routes: {
        player: () => json(playerInfo([track("zh", "//i0.hdslb.com/a.json")])),
        subtitle: () => json(subtitleJson([0, 1, "单P"])),
      },
    });
    await expect(captureBilibiliPage()).resolves.toMatchObject({ status: "captured" });
    expect(urls()[0]).toContain(`cid=${CID}`);

    vi.unstubAllGlobals();
    stubBilibili({
      href: `${BILIBILI_URL}?p=9`,
      state: initialState(),
      routes: { player: () => json(playerInfo([])) },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "capture_unavailable" });
  });

  it("identifies av pages by aid and keeps the normalized av id", async () => {
    stubBilibili({
      href: "https://www.bilibili.com/video/AV170001",
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh", "//i0.hdslb.com/a.json")])),
        subtitle: () => json(subtitleJson([0, 1, "av 页面"])),
      },
    });

    const result = await captureBilibiliPage();

    expect(result.status).toBe("captured");
    if (result.status !== "captured") return;
    expect(result.capture.platform_id).toBe("av170001");
    expect(result.capture.canonical_url).toBe("https://www.bilibili.com/video/av170001");
  });

  it("never uses a state that describes another video: it asks the view API for this one", async () => {
    const stale = initialState(videoData({ bvid: "BV1ab411c7mE", aid: 999, title: "上一个视频" }), { upData: { name: "旧UP主" }, tags: [{ tag_name: "旧标签" }] });
    const { urls } = stubBilibili({
      state: stale,
      routes: {
        view: () => json({ code: 0, data: videoData({ title: "当前视频", owner: { name: "新UP主" } }) }),
        player: () => json(playerInfo([track("zh", "//i0.hdslb.com/a.json")])),
        subtitle: () => json(subtitleJson([0, 1, "当前内容"])),
      },
    });

    const result = await captureBilibiliPage();

    expect(urls()[0]).toBe(`https://api.bilibili.com/x/web-interface/view?bvid=${BVID}`);
    expect(result.status).toBe("captured");
    if (result.status !== "captured") return;
    expect(result.capture.metadata).toMatchObject({ title: "当前视频", author: "新UP主", tags: [] });
    expect(JSON.stringify(result)).not.toContain("上一个视频");
    expect(JSON.stringify(result)).not.toContain("旧");
  });

  it("reports a stale page when the view API cannot confirm the video, and not-ready before any state exists", async () => {
    const stale = initialState(videoData({ bvid: "BV1ab411c7mE", aid: 999 }));
    stubBilibili({ state: stale, routes: { view: () => new Response("{}", { status: 412 }) } });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });

    vi.unstubAllGlobals();
    stubBilibili({ state: stale, routes: { view: () => json({ code: 0, data: videoData({ bvid: "BV1ab411c7mF", aid: 1000 }) }) } });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });

    vi.unstubAllGlobals();
    stubBilibili({ state: undefined, routes: { view: () => json({ code: -404 }) } });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_player_not_ready" });

    vi.unstubAllGlobals();
    stubBilibili({ state: { error: { trueCode: -404 } }, routes: {} });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "capture_unavailable" });
  });

  it("rejects the read when the tab navigates to another video or part mid-read", async () => {
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => {
          vi.stubGlobal("location", { href: `${BILIBILI_URL}?p=2`, hostname: "www.bilibili.com", pathname: `/video/${BVID}` });
          return json(playerInfo([track("zh", "//i0.hdslb.com/a.json")]));
        },
        subtitle: () => json(subtitleJson([0, 1, "不应返回"])),
      },
    });

    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });

    vi.unstubAllGlobals();
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh", "//i0.hdslb.com/a.json")])),
        subtitle: () => {
          vi.stubGlobal("location", { href: "https://www.bilibili.com/video/BV1ab411c7mE", hostname: "www.bilibili.com", pathname: "/video/BV1ab411c7mE" });
          return json(subtitleJson([0, 1, "不应返回"]));
        },
      },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "stale_player_response" });
  });

  it("refuses to run outside a Bilibili video URL", async () => {
    stubBilibili({ href: "https://www.bilibili.com/bangumi/play/ep1", state: initialState(), routes: {} });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "unsupported_page" });
  });

  it("never fetches a subtitle from an untrusted host and treats refused tracks as a failed read", async () => {
    for (const url of [
      "//evil.example/bfs/subtitle/a.json",
      "https://hdslb.com.evil.example/a.json",
      "https://user:pw@i0.hdslb.com/a.json",
      "javascript:alert(1)",
      "ftp://i0.hdslb.com/a.json",
    ]) {
      vi.unstubAllGlobals();
      const { urls } = stubBilibili({
        state: initialState(),
        routes: { player: () => json(playerInfo([track("zh-CN", url)])), subtitle: () => json(subtitleJson([0, 1, "x"])) },
      });
      await expect(captureBilibiliPage(), url).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
      expect(urls().filter((value) => !value.startsWith("https://api.bilibili.com/")), url).toEqual([]);
    }
  });

  it("rejects a subtitle response that was redirected outside hdslb.com", async () => {
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])),
        subtitle: () => {
          const response = json(subtitleJson([0, 1, "不应使用"]));
          Object.defineProperty(response, "url", { value: "https://evil.example/a.json" });
          return response;
        },
      },
    });

    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
  });

  it("maps player API failures to stable errors", async () => {
    const run = async (player: Route) => {
      vi.unstubAllGlobals();
      stubBilibili({ state: initialState(), routes: { player } });
      return captureBilibiliPage();
    };

    await expect(run(() => json({ code: -352, message: "risk control" }))).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    await expect(run(() => new Response("blocked", { status: 412 }))).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    await expect(run(() => { throw new TypeError("Failed to fetch"); })).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    await expect(run(() => new Response("<html>", { status: 200 }))).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("rejects oversized bodies before parsing them", async () => {
    const huge = "x".repeat(4_000_001);
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])),
        subtitle: () => new Response(huge, { status: 200 }),
      },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });

    vi.unstubAllGlobals();
    stubBilibili({
      state: initialState(),
      routes: { player: () => new Response("{}", { status: 200, headers: { "content-length": "9000000" } }) },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
  });

  it("rejects subtitle transcripts beyond the capture limits", async () => {
    const lines = Array.from({ length: 50_001 }, (_, index): [number, number, string] => [index, index + 1, "字"]);
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])),
        subtitle: () => json(subtitleJson(...lines)),
      },
    });
    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_parse_failed" });
  });

  it("bounds hanging requests and aborts them", async () => {
    vi.useFakeTimers();
    let observedSignal: AbortSignal | null | undefined;
    stubBilibili({
      state: initialState(),
      routes: {
        player: (_url, init) => {
          observedSignal = init?.signal;
          return new Promise<Response>(() => undefined);
        },
      },
    });

    const pending = captureBilibiliPage();
    await vi.advanceTimersByTimeAsync(12_000);

    await expect(pending).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(observedSignal?.aborted).toBe(true);
  });

  it("cancels an oversized streamed body", async () => {
    const cancel = vi.fn(async () => undefined);
    stubBilibili({
      state: initialState(),
      routes: {
        player: () => json(playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])),
        subtitle: () => {
          const response = new Response("", { status: 200 });
          Object.defineProperty(response, "body", {
            value: {
              getReader: () => ({
                read: async () => ({ done: false, value: new Uint8Array(4_000_001) }),
                cancel,
                releaseLock: vi.fn(),
              }),
            },
          });
          return response;
        },
      },
    });

    await expect(captureBilibiliPage()).resolves.toEqual({ status: "failed", error: "caption_fetch_failed" });
    expect(cancel).toHaveBeenCalled();
  });

  it("survives the MV3 function serialization boundary without module closures", async () => {
    const state = initialState();
    const adapter = runInNewContext(`(${captureBilibiliPage.toString()})`, {
      Date,
      clearTimeout,
      setTimeout,
      URL,
      AbortController,
      TextDecoder,
      Uint8Array,
      location: { href: BILIBILI_URL, hostname: "www.bilibili.com", pathname: `/video/${BVID}` },
      document: { title: "serialized" },
      __INITIAL_STATE__: state,
      fetch: async (input: string) => {
        const url = new URL(input);
        const body = url.hostname === "api.bilibili.com"
          ? playerInfo([track("zh-CN", "//i0.hdslb.com/a.json")])
          : subtitleJson([0, 1, "serialized cue"]);
        return { ok: true, url: input, text: async () => JSON.stringify(body) };
      },
    }) as () => Promise<CaptureAttempt>;

    const result = await adapter();

    expect(result.status).toBe("captured");
    if (result.status === "captured") expect(result.capture.caption.cues[0]?.text).toBe("serialized cue");
  });
});

describe("Bilibili player control", () => {
  function stubBilibiliDocument(options: { main?: FakeVideo[]; all?: FakeVideo[] }): void {
    vi.stubGlobal("document", {
      querySelectorAll: (selector: string) => {
        if (selector.includes("bpx-player-video-wrap")) return options.main ?? [];
        if (selector === "video, bwp-video") return options.all ?? [];
        return [];
      },
    });
  }

  it("reads the main player's video and ignores hover-preview videos elsewhere on the page", () => {
    const main = fakeVideo({ currentTime: 83.5 });
    const preview = fakeVideo({ currentTime: 3 });
    stubBilibiliDocument({ main: [main], all: [preview, main] });

    expect(controlPlayer(null, "bilibili")).toEqual({ status: "video", current_sec: 83.5, ready: true, playing: true, seeked: false });
  });

  it("falls back to any video or bwp-video element and reports a missing one", () => {
    stubBilibiliDocument({ all: [fakeVideo({ paused: true, currentTime: 7 })] });
    expect(controlPlayer(null, "bilibili")).toEqual({ status: "video", current_sec: 7, ready: true, playing: false, seeked: false });

    vi.unstubAllGlobals();
    stubBilibiliDocument({});
    expect(controlPlayer(null, "bilibili")).toEqual({ status: "no_video" });
  });

  it("prefers the bpx player API for the position and the seek", () => {
    const video = fakeVideo({ currentTime: 5 });
    const player = { getCurrentTime: vi.fn(() => 41.25), seek: vi.fn() };
    vi.stubGlobal("player", player);
    stubBilibiliDocument({ main: [video] });

    expect(controlPlayer(null, "bilibili")).toMatchObject({ status: "video", current_sec: 41.25, seeked: false });
    expect(controlPlayer(125.5, "bilibili")).toMatchObject({ status: "video", seeked: true });
    expect(player.seek).toHaveBeenCalledWith(125.5);
    expect(video.currentTime).toBe(5);
  });

  it("falls back to the media element when the player API is missing, throws or misreports", () => {
    const withoutApi = fakeVideo();
    stubBilibiliDocument({ main: [withoutApi] });
    expect(controlPlayer(90, "bilibili")).toMatchObject({ status: "video", seeked: true, current_sec: 90 });
    expect(withoutApi.currentTime).toBe(90);

    vi.unstubAllGlobals();
    const throwing = fakeVideo({ duration: 100, currentTime: 12 });
    vi.stubGlobal("player", { seek: () => { throw new Error("player replaced"); }, getCurrentTime: () => { throw new Error("gone"); } });
    stubBilibiliDocument({ main: [throwing] });
    expect(controlPlayer(500, "bilibili")).toMatchObject({ status: "video", seeked: true, current_sec: 100 });
    expect(throwing.currentTime).toBe(100);
    expect(controlPlayer(null, "bilibili")).toMatchObject({ status: "video", current_sec: 100 });

    vi.unstubAllGlobals();
    const odd = fakeVideo({ currentTime: 33 });
    vi.stubGlobal("player", { getCurrentTime: () => "soon" });
    stubBilibiliDocument({ main: [odd] });
    expect(controlPlayer(null, "bilibili")).toMatchObject({ status: "video", current_sec: 33 });
  });

  it("reports an unseeked probe when the element refuses the assignment and no player API exists", () => {
    const refusing = Object.defineProperty(fakeVideo(), "currentTime", { get: () => 9, set: () => { throw new Error("refused"); } });
    stubBilibiliDocument({ main: [refusing] });

    expect(controlPlayer(30, "bilibili")).toMatchObject({ status: "video", seeked: false, current_sec: 9 });
  });

  it("reads and seeks Bilibili in the top frame only, passing the platform to the injected function", async () => {
    const url = `${BILIBILI_URL}?p=2`;
    const executeScript = vi.fn(async () => [{ frameId: 0, result: { status: "video", current_sec: 61.5, ready: true, playing: true, seeked: true } }]);
    vi.stubGlobal("chrome", { scripting: { executeScript } });

    await expect(readPlaybackSeconds(5, url)).resolves.toBe(61.5);
    await expect(seekPlayback(5, url, 300)).resolves.toBeUndefined();

    expect(executeScript).toHaveBeenCalledTimes(2);
    expect(executeScript).toHaveBeenNthCalledWith(1, { target: { tabId: 5, allFrames: false }, world: "MAIN", func: controlPlayer, args: [null, "bilibili"] });
    expect(executeScript).toHaveBeenNthCalledWith(2, { target: { tabId: 5, allFrames: false }, world: "MAIN", func: controlPlayer, args: [300, "bilibili"] });

    vi.stubGlobal("chrome", { scripting: { executeScript: vi.fn(async () => [{ frameId: 0, result: { status: "no_video" } }]) } });
    await expect(seekPlayback(5, url, 5)).rejects.toThrow("seek_failed");
    await expect(readPlaybackSeconds(5, url)).resolves.toBeNull();
  });

  it("stays self-contained when serialised into another JavaScript context", () => {
    const video = fakeVideo({ currentTime: 20 });
    const player = { getCurrentTime: () => 21, seek: vi.fn() };
    const injected = runInNewContext(`(${controlPlayer.toString()})`, {
      document: { querySelectorAll: (selector: string) => (selector.includes("bpx-player-video-wrap") ? [video] : []) },
      player,
    }) as (seekSec: number | null, kind: string) => PlayerProbe;

    expect(injected(null, "bilibili")).toMatchObject({ status: "video", current_sec: 21 });
    expect(injected(44, "bilibili")).toMatchObject({ status: "video", seeked: true });
    expect(player.seek).toHaveBeenCalledWith(44);
  });
});
