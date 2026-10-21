import { describe, expect, it } from "vitest";

import type { IngestReasonCategory } from "../api/contracts";
import {
  ACTION_LABEL,
  BODY_DETAIL_LABEL,
  isKnownStageKey,
  KNOWN_STAGE_KEYS,
  lifecycleLabel,
  platformLabel,
  REASON_COPY,
  reasonPhrase,
  reasonStatusLine,
  STAGE_LABEL,
  STAGE_PLACEHOLDER,
  stageRank,
} from "./ingestCopy";

const CATEGORIES: IngestReasonCategory[] = [
  "no_speech", "no_transcription", "fetch_failed", "asr_failed", "quota", "too_large", "login_required", "error",
];

describe("reason copy", () => {
  it("has a sentence for every reason category the server can send", () => {
    for (const category of CATEGORIES) {
      expect(REASON_COPY[category]).toBeTruthy();
      expect(reasonStatusLine({ category })).toBe(`${REASON_COPY[category]}，正文暂不可检索。`);
    }
    expect(Object.keys(REASON_COPY).sort()).toEqual([...CATEGORIES].sort());
  });

  it("says why the body is not searchable in the words of the product", () => {
    expect(reasonPhrase({ category: "no_speech" })).toBe("没有字幕，也没有识别出语音");
    expect(reasonPhrase({ category: "no_transcription" })).toBe("没有字幕，暂时无法语音转写");
    expect(reasonPhrase({ category: "fetch_failed" })).toBe("没能获取视频内容，可能是平台暂时限制了访问");
    expect(reasonPhrase({ category: "asr_failed" })).toBe("语音转写服务暂时出错");
    expect(reasonPhrase({ category: "quota" })).toBe("处理额度不足");
    expect(reasonPhrase({ category: "too_large" })).toBe("视频太长或内容太大，超出处理上限");
    expect(reasonPhrase({ category: "login_required" })).toBe("这个视频的字幕需要登录 B站 才能读取");
    expect(reasonPhrase({ category: "error" })).toBe("整理时出错了");
  });

  it("reads a category this build does not know as a plain error", () => {
    // A newer server may add categories; the type is closed, the wire is not.
    const future: { category: IngestReasonCategory } = JSON.parse('{"category":"brand_new"}');
    expect(reasonPhrase(future)).toBe(REASON_COPY.error);
    expect(reasonStatusLine(future)).toBe("整理时出错了，正文暂不可检索。");
  });
});

describe("lifecycle label", () => {
  it("names every lifecycle", () => {
    expect(lifecycleLabel("queued")).toBe("排队中");
    expect(lifecycleLabel("processing")).toBe("整理中");
    expect(lifecycleLabel("ready")).toBe("可检索");
    expect(lifecycleLabel("failed")).toBe("整理失败");
    expect(lifecycleLabel("needs_action")).toBe("整理失败");
    expect(lifecycleLabel("archived")).toBe("已归档");
  });

  it("falls back to processing for a value it does not know, without throwing", () => {
    expect(lifecycleLabel("teleporting")).toBe("整理中");
    expect(lifecycleLabel("")).toBe("整理中");
    // Inherited object keys are not lifecycle values.
    expect(lifecycleLabel("constructor")).toBe("整理中");
    expect(lifecycleLabel("toString")).toBe("整理中");
  });
});

describe("stage vocabulary", () => {
  it("labels every known stage and gives the later ones a placeholder", () => {
    expect(KNOWN_STAGE_KEYS).toEqual(["body", "classify", "summary", "movie"]);
    expect(STAGE_LABEL).toEqual({ body: "读取字幕与内容", classify: "分类与主题", summary: "内容要点", movie: "电影识别" });
    expect(STAGE_PLACEHOLDER).toEqual({ classify: "未分类", summary: "暂无内容要点", movie: "未识别" });
    expect(BODY_DETAIL_LABEL).toEqual({ queued: "排队中", fetching: "读取字幕与内容", transcribing: "语音转写中", indexing: "建立检索索引" });
  });

  it("orders known keys and ignores the rest", () => {
    expect(stageRank("body")).toBeLessThan(stageRank("classify"));
    expect(stageRank("classify")).toBeLessThan(stageRank("summary"));
    expect(stageRank("summary")).toBeLessThan(stageRank("movie"));
    expect(stageRank("translation")).toBe(-1);
    expect(isKnownStageKey("summary")).toBe(true);
    expect(isKnownStageKey("translation")).toBe(false);
    expect(isKnownStageKey("constructor")).toBe(false);
  });
});

describe("labels and actions", () => {
  it("uses the button wording of the prototype", () => {
    expect(ACTION_LABEL).toEqual({ retry: "重新整理", recollect: "重新收藏", openVideo: "打开视频页", searchTitles: "仅查标题 / 备注" });
  });

  it("names platforms and leaves an unknown one as it is", () => {
    expect(platformLabel("youtube")).toBe("YouTube");
    expect(platformLabel("bilibili")).toBe("B站");
    expect(platformLabel("ntu_kaltura")).toBe("课程视频");
    expect(platformLabel("vimeo")).toBe("vimeo");
    expect(platformLabel("")).toBe("");
    expect(platformLabel("constructor")).toBe("constructor");
  });
});
