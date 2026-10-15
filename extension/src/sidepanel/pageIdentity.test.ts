import { describe, expect, it } from "vitest";

import { formatTimestamp, panelAnswerText } from "./copy";
import { identifyPage, newConversationId } from "./pageIdentity";
import { questionBlock } from "./usePageQa";
import { noCaptionPage, youtubePage } from "../test/sidepanelEnvironment";

describe("page identity", () => {
  it("identifies YouTube videos by id across watch, short and youtu.be URLs", () => {
    expect(identifyPage("https://www.youtube.com/watch?v=abcdefghijk&t=30s")).toEqual({ platform: "youtube", key: "youtube:abcdefghijk" });
    expect(identifyPage("https://www.youtube.com/watch?v=abcdefghijk&list=PL1")?.key).toBe("youtube:abcdefghijk");
    expect(identifyPage("https://www.youtube.com/shorts/abcdefghijk")?.key).toBe("youtube:abcdefghijk");
    expect(identifyPage("https://youtu.be/abcdefghijk")?.key).toBe("youtube:abcdefghijk");
  });

  it("keys NTULearn pages by URL without the fragment", () => {
    const identity = identifyPage("https://ntulearnv1.ntu.edu.sg/media/t/1_abc?x=1#section");
    expect(identity).toEqual({ platform: "ntu_kaltura", key: "ntu_kaltura:https://ntulearnv1.ntu.edu.sg/media/t/1_abc?x=1" });
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
    expect(questionBlock(noCaptionPage("youtube"))).toBeNull();
    expect(questionBlock(noCaptionPage("ntu_kaltura"))).toBe("该视频没有可用字幕，暂时无法提问");
    const bare = noCaptionPage("youtube");
    bare.metadata = { ...bare.metadata, title: null, description: null, chapters: [] };
    expect(questionBlock(bare)).toMatch(/没有可用的标题和简介/);
  });
});
