import { describe, expect, it } from "vitest";

import { captureRequest, contentHash, type PageCapture } from "./protocol.js";

describe("capture content hash", () => {
  it("matches the backend canonical cue representation", async () => {
    await expect(contentHash([{ start_sec: 1, end_sec: 2.5, text: " hello " }])).resolves.toBe("46ef429a49f8a883f1541705fdf216c206d5c2059e8b4b6962965b8bc9b317b2");
  });
});

describe("capture.v1 request", () => {
  it("carries a Bilibili capture with the platform-neutral cue hash", async () => {
    const capture: PageCapture = {
      platform: "bilibili",
      platform_id: "BV1xx411c7mD",
      canonical_url: "https://www.bilibili.com/video/BV1xx411c7mD",
      page_url: "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
      metadata: { title: "t", author: null, duration_sec: null, language: "zh", description: null, cover_url: null, tags: [], chapters: [] },
      caption: { status: "available", source: "auto_caption", language: "zh", cues: [{ start_sec: 1, end_sec: 2.5, text: " hello " }] },
    };

    const request = await captureRequest(capture);

    expect(request).toMatchObject({ protocol_version: "capture.v1", platform: "bilibili", platform_id: "BV1xx411c7mD", page_url: capture.page_url });
    expect(request.content_hash).toBe("46ef429a49f8a883f1541705fdf216c206d5c2059e8b4b6962965b8bc9b317b2");
  });
});
