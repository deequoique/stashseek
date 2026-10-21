import { describe, expect, it } from "vitest";

import { parseVideoInput } from "./videoInput";

describe("video input entries", () => {
  it("extracts repeated share blocks and keeps duplicates in their original order", () => {
    expect(parseVideoInput(
      "【第一个标题】https://www.bilibili.com/video/BV15pao6iEZo/?share_source=test "
      + "【第二个标题】 https://www.bilibili.com/video/BV15pao6iEZo/?share_source=test",
    )).toEqual([
      "https://www.bilibili.com/video/BV15pao6iEZo/?share_source=test",
      "https://www.bilibili.com/video/BV15pao6iEZo/?share_source=test",
    ]);
  });

  it("removes title-only lines while retaining invalid mixed entries", () => {
    expect(parseVideoInput(
      "【分享标题】\nBV15pao6iEZo\nnot a video\nhttps://youtu.be/dQw4w9WgXcQ",
    )).toEqual(["BV15pao6iEZo", "not a video", "https://youtu.be/dQw4w9WgXcQ"]);
  });

  it("removes ordinary sharing text around a URL on the same line", () => {
    expect(parseVideoInput(
      "分享一下 https://www.bilibili.com/video/BV15pao6iEZo 快来看看",
    )).toEqual([
      "https://www.bilibili.com/video/BV15pao6iEZo",
    ]);
  });

  it("retains a protocol-relative URL beside another URL for server validation", () => {
    expect(parseVideoInput(
      "分享 //foreign.example/?next= https://www.bilibili.com/video/BV15pao6iEZo",
    )).toEqual([
      "//foreign.example/?next=", "https://www.bilibili.com/video/BV15pao6iEZo",
    ]);
  });

  it.each([
    "https://foreign.example/?next=https://www.bilibili.com/video/BV15pao6iEZo",
    "//foreign.example/?next=(https://www.bilibili.com/video/BV15pao6iEZo)",
    "//foreign.example/?next=】https://www.bilibili.com/video/BV15pao6iEZo",
    "https://user:password@www.bilibili.com/video/BV15pao6iEZo",
    "https://www.bilibili.com:443/video/BV15pao6iEZo",
    "https://www.bilibili.com/video/BV15pao6iEZo?p=2#part",
    "https://www.bilibili.com/video/BV15pao6iEZo/错误路径",
    "ftp://www.bilibili.com/video/BV15pao6iEZo",
    "文字 BV15pao6iEZo 文字",
  ])("preserves unsafe or invalid input for authoritative server validation: %s", (input) => {
    expect(parseVideoInput(input)).toEqual([input]);
  });
});
