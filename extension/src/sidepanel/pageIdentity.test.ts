import { describe, expect, it } from "vitest";

import { formatTimestamp, panelAnswerText } from "./copy";
import { captureIdentityKey, identifyPage, newConversationId } from "./pageIdentity";
import { questionBlock } from "./usePageQa";
import { noCaptionPage, youtubePage } from "../test/sidepanelEnvironment";

describe("page identity", () => {
  it("identifies YouTube videos by id across watch, short and youtu.be URLs", () => {
    expect(identifyPage("https://www.youtube.com/watch?v=abcdefghijk&t=30s")).toEqual({ platform: "youtube", key: "youtube:abcdefghijk" });
    expect(identifyPage("https://www.youtube.com/watch?v=abcdefghijk&list=PL1")?.key).toBe("youtube:abcdefghijk");
    expect(identifyPage("https://www.youtube.com/shorts/abcdefghijk")?.key).toBe("youtube:abcdefghijk");
    expect(identifyPage("https://youtu.be/abcdefghijk")?.key).toBe("youtube:abcdefghijk");
  });

  it("identifies Bilibili videos by normalized id and part, so every part has its own thread", () => {
    expect(identifyPage("https://www.bilibili.com/video/BV1xx411c7mD")).toEqual({ platform: "bilibili", key: "bilibili:BV1xx411c7mD:p1" });
    expect(identifyPage("https://www.bilibili.com/video/BV1xx411c7mD/?p=1&spm_id_from=333.788")?.key).toBe("bilibili:BV1xx411c7mD:p1");
    expect(identifyPage("https://www.bilibili.com/video/BV1xx411c7mD?p=3#reply")?.key).toBe("bilibili:BV1xx411c7mD:p3");
    expect(identifyPage("https://www.bilibili.com/video/bv1xx411c7mD")?.key).toBe("bilibili:BV1xx411c7mD:p1");
    expect(identifyPage("https://www.bilibili.com/video/AV170001?p=2")?.key).toBe("bilibili:av170001:p2");
    expect(identifyPage("https://www.bilibili.com/video/BV1xx411c7mD?p=0")?.key).toBe("bilibili:BV1xx411c7mD:p1");
    expect(identifyPage("https://www.bilibili.com/video/BV1xx411c7mE")?.key).not.toBe(identifyPage("https://www.bilibili.com/video/BV1xx411c7mD")?.key);
  });

  it("rejects Bilibili pages that are not a video", () => {
    expect(identifyPage("https://www.bilibili.com/")).toBeNull();
    expect(identifyPage("https://www.bilibili.com/bangumi/play/ep1")).toBeNull();
    expect(identifyPage("https://space.bilibili.com/1/video")).toBeNull();
    expect(identifyPage("https://www.bilibili.com/video/BV1xx411c7m")).toBeNull();
  });

  it("derives the tab key of a read capture so a stale read after navigation can be detected", () => {
    expect(captureIdentityKey(youtubePage("abcdefghijk"))).toBe("youtube:abcdefghijk");
    const bilibili = (pageUrl: string) => ({
      ...youtubePage(),
      platform: "bilibili" as const,
      platform_id: "BV1xx411c7mD",
      canonical_url: "https://www.bilibili.com/video/BV1xx411c7mD",
      page_url: pageUrl,
    });
    expect(captureIdentityKey(bilibili("https://www.bilibili.com/video/BV1xx411c7mD"))).toBe("bilibili:BV1xx411c7mD:p1");
    expect(captureIdentityKey(bilibili("https://www.bilibili.com/video/BV1xx411c7mD?p=4"))).toBe("bilibili:BV1xx411c7mD:p4");
    // The key of a captured part equals the key of the tab that showed it.
    expect(captureIdentityKey(bilibili("https://www.bilibili.com/video/BV1xx411c7mD?p=4")))
      .toBe(identifyPage("https://www.bilibili.com/video/BV1xx411c7mD?p=4&spm=x")?.key);
  });

  it("treats NTULearn and Kaltura pages as unsupported", () => {
    expect(identifyPage("https://ntulearnv1.ntu.edu.sg/media/t/1_abc?x=1#section")).toBeNull();
    expect(identifyPage("https://ntulearn.ntu.edu.sg/ultra/courses/example")).toBeNull();
  });

  it("rejects unsupported and missing URLs", () => {
    expect(identifyPage(null)).toBeNull();
    expect(identifyPage(undefined)).toBeNull();
    expect(identifyPage("https://www.youtube.com/")).toBeNull();
    expect(identifyPage("http://www.youtube.com/watch?v=abcdefghijk")).toBeNull();
    expect(identifyPage("https://example.com/watch?v=abcdefghijk")).toBeNull();
    expect(identifyPage("not a url")).toBeNull();
  });

  it("creates page thread ids in the server's shape", () => {
    const first = newConversationId();
    expect(first).toMatch(/^page-[0-9a-f]{32}$/);
    expect(newConversationId()).not.toBe(first);
  });
});

describe("panel copy helpers", () => {
  it("removes the appended source block and raw segment markers from answers", () => {
    expect(panelAnswerText("先看索引 [S12]，再看缓存[S4]。\n\n来源：\n- 视频\n  - [S12] https://x — 摘录", true)).toBe("先看索引，再看缓存。");
    expect(panelAnswerText("流式中途 [S1", false)).toBe("流式中途");
    expect(panelAnswerText("没有来源块\n\n来源：\n保留", false)).toContain("来源：");
  });

  it("formats timestamps with hours from one hour", () => {
    expect(formatTimestamp(0)).toBe("0:00");
    expect(formatTimestamp(65.9)).toBe("1:05");
    expect(formatTimestamp(3725)).toBe("1:02:05");
  });

  it("mirrors the server rule for pages without captions", () => {
    expect(questionBlock(youtubePage())).toBeNull();
    expect(questionBlock(noCaptionPage())).toBeNull();
    const bare = noCaptionPage();
    bare.metadata = { ...bare.metadata, title: null, description: null, chapters: [] };
    expect(questionBlock(bare)).toMatch(/没有可用的标题和简介/);
  });
});
