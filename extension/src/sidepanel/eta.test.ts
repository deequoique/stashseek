import { describe, expect, it } from "vitest";

import {
  type EtaInput,
  estimateEta,
  formatElapsed,
  formatEta,
  OVERRUN_FACTOR,
  STAGE_EXPECTED_SECONDS,
  stagesOnPath,
} from "./eta";

const TEN_MINUTES = 600;

function input(overrides: Partial<EtaInput> = {}): EtaInput {
  return {
    path: "url",
    stage: "fetching",
    durationSec: TEN_MINUTES,
    secondsInStage: 0,
    captionless: false,
    sawTranscribing: false,
    ...overrides,
  };
}

describe("per-stage expected seconds", () => {
  it("follows the design table for a 10 minute video", () => {
    expect(STAGE_EXPECTED_SECONDS.queued(10)).toBe(10);
    expect(STAGE_EXPECTED_SECONDS.fetching(10)).toBe(40);
    expect(STAGE_EXPECTED_SECONDS.transcribing(10)).toBe(180);
    expect(STAGE_EXPECTED_SECONDS.chunking(10)).toBe(25);
    expect(STAGE_EXPECTED_SECONDS.embedding(10)).toBe(35);
  });
});

describe("stages on a path", () => {
  it("skips fetching on the capture path and transcribing unless it is expected or seen", () => {
    expect(stagesOnPath({ path: "capture", stage: "queued", captionless: false, sawTranscribing: false }))
      .toEqual(["queued", "chunking", "embedding"]);
    expect(stagesOnPath({ path: "url", stage: "queued", captionless: false, sawTranscribing: false }))
      .toEqual(["queued", "fetching", "chunking", "embedding"]);
  });

  it("counts transcribing for a caption-less URL import, once seen, and while in it", () => {
    expect(stagesOnPath({ path: "url", stage: "fetching", captionless: true, sawTranscribing: false }))
      .toEqual(["queued", "fetching", "transcribing", "chunking", "embedding"]);
    expect(stagesOnPath({ path: "url", stage: "chunking", captionless: false, sawTranscribing: true }))
      .toEqual(["queued", "fetching", "transcribing", "chunking", "embedding"]);
    expect(stagesOnPath({ path: "capture", stage: "transcribing", captionless: false, sawTranscribing: false }))
      .toEqual(["queued", "transcribing", "chunking", "embedding"]);
  });
});

describe("estimateEta", () => {
  it("is the rest of the current stage plus every later stage", () => {
    // fetching 40 + chunking 25 + embedding 35 = 100 s, nothing elapsed yet.
    expect(estimateEta(input())).toEqual({ kind: "remaining", seconds: 100 });
    // 30 s into fetching: 10 + 25 + 35.
    expect(estimateEta(input({ secondsInStage: 30 }))).toEqual({ kind: "remaining", seconds: 70 });
  });

  it("covers each stage of the URL path", () => {
    expect(estimateEta(input({ stage: "queued" }))).toEqual({ kind: "remaining", seconds: 110 });
    expect(estimateEta(input({ stage: "transcribing", secondsInStage: 60 }))).toEqual({ kind: "remaining", seconds: 120 + 25 + 35 });
    expect(estimateEta(input({ stage: "chunking", secondsInStage: 5 }))).toEqual({ kind: "remaining", seconds: 20 + 35 });
    expect(estimateEta(input({ stage: "embedding", secondsInStage: 10 }))).toEqual({ kind: "remaining", seconds: 25 });
  });

  it("covers the capture path without fetching or transcription", () => {
    expect(estimateEta(input({ path: "capture", stage: "queued" }))).toEqual({ kind: "remaining", seconds: 10 + 25 + 35 });
    expect(estimateEta(input({ path: "capture", stage: "chunking" }))).toEqual({ kind: "remaining", seconds: 60 });
  });

  it("adds transcription to a caption-less URL import before the stage is even seen", () => {
    expect(estimateEta(input({ captionless: true }))).toEqual({ kind: "remaining", seconds: 40 + 180 + 25 + 35 });
  });

  it("floors the current stage at zero and never goes negative", () => {
    // 50 s in a 40 s stage is not yet an overrun (limit 60 s): only later stages remain.
    expect(estimateEta(input({ secondsInStage: 50 }))).toEqual({ kind: "remaining", seconds: 60 });
    expect(estimateEta(input({ secondsInStage: -5 }))).toEqual({ kind: "remaining", seconds: 100 });
  });

  it("reports an overrun once a stage exceeds 1.5 times its estimate", () => {
    expect(OVERRUN_FACTOR).toBe(1.5);
    expect(estimateEta(input({ secondsInStage: 60 }))).toEqual({ kind: "remaining", seconds: 60 });
    expect(estimateEta(input({ secondsInStage: 61 }))).toEqual({ kind: "overrun" });
    expect(formatEta({ kind: "overrun" })).toBe("比预计久，仍在处理");
  });

  it("gives no estimate when the duration is unknown", () => {
    for (const durationSec of [null, 0, -3, Number.NaN]) {
      expect(estimateEta(input({ durationSec }))).toEqual({ kind: "unknown" });
    }
    expect(formatEta({ kind: "unknown" })).toBeNull();
  });
});

describe("formatEta", () => {
  it("rounds minutes up and says less than a minute below 60 seconds", () => {
    expect(formatEta({ kind: "remaining", seconds: 0 })).toBe("预计还需不到 1 分钟");
    expect(formatEta({ kind: "remaining", seconds: 59.9 })).toBe("预计还需不到 1 分钟");
    expect(formatEta({ kind: "remaining", seconds: 60 })).toBe("预计还需约 1 分钟");
    expect(formatEta({ kind: "remaining", seconds: 61 })).toBe("预计还需约 2 分钟");
    expect(formatEta({ kind: "remaining", seconds: 300 })).toBe("预计还需约 5 分钟");
  });
});

describe("formatElapsed", () => {
  it("shows seconds, minutes and hours", () => {
    expect(formatElapsed(0)).toBe("0 秒");
    expect(formatElapsed(42.9)).toBe("42 秒");
    expect(formatElapsed(83)).toBe("1 分 23 秒");
    expect(formatElapsed(3725)).toBe("1 小时 02 分");
    expect(formatElapsed(-4)).toBe("0 秒");
  });
});
