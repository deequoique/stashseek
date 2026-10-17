import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type PollerOptions, startPolling } from "./itemPoller";

type Options = PollerOptions<string>;

function setup(overrides: Partial<Options> = {}) {
  const state = { now: 0, visible: true };
  const options: Options = {
    fetch: vi.fn(async () => "item"),
    onResult: vi.fn(() => "continue" as const),
    onError: vi.fn(() => "continue" as const),
    onDeadline: vi.fn(),
    intervalMs: 2_500,
    backoffMs: 10_000,
    deadlineAt: 30 * 60 * 1000,
    now: () => state.now,
    isVisible: () => state.visible,
    ...overrides,
  };
  return { options, state, poller: startPolling(options) };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("item polling", () => {
  it("reads at once and then every interval", async () => {
    const { options } = setup();
    await vi.advanceTimersByTimeAsync(0);
    expect(options.fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2_499);
    expect(options.fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(options.fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(options.fetch).toHaveBeenCalledTimes(4);
  });

  it("stops when the result handler says so", async () => {
    const onResult = vi.fn<Options["onResult"]>().mockReturnValueOnce("continue").mockReturnValue("stop");
    const { options } = setup({ onResult });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(options.fetch).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("backs off to the slower interval after a failed read without stopping", async () => {
    const fetch = vi.fn<Options["fetch"]>().mockRejectedValueOnce(new Error("offline")).mockResolvedValue("item");
    const { options } = setup({ fetch });
    await vi.advanceTimersByTimeAsync(0);
    expect(options.onError).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(9_999);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    // Back to the normal interval after the next success.
    await vi.advanceTimersByTimeAsync(2_500);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("stops when the error handler says the failure is final", async () => {
    const fetch = vi.fn<Options["fetch"]>().mockRejectedValue(new Error("gone"));
    setup({ fetch, onError: vi.fn(() => "stop" as const) });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("calls the deadline handler once the cap has passed and reads no more", async () => {
    const { options, state } = setup({ deadlineAt: 1_000 });
    await vi.advanceTimersByTimeAsync(0);
    expect(options.fetch).toHaveBeenCalledTimes(1);
    state.now = 1_000;
    await vi.advanceTimersByTimeAsync(2_500);
    expect(options.onDeadline).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(options.fetch).toHaveBeenCalledTimes(1);
    expect(options.onDeadline).toHaveBeenCalledOnce();
  });

  it("skips reads while hidden and resumes on the next tick or when nudged", async () => {
    const { options, state, poller } = setup();
    await vi.advanceTimersByTimeAsync(0);
    expect(options.fetch).toHaveBeenCalledTimes(1);
    state.visible = false;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(options.fetch).toHaveBeenCalledTimes(1);
    state.visible = true;
    poller.nudge();
    await vi.advanceTimersByTimeAsync(0);
    expect(options.fetch).toHaveBeenCalledTimes(2);
  });

  it("does not start a second read while one is running", async () => {
    let release: (value: string) => void = () => undefined;
    const fetch = vi.fn<Options["fetch"]>(() => new Promise((resolve) => { release = resolve; }));
    const { poller } = setup({ fetch });
    await vi.advanceTimersByTimeAsync(0);
    poller.nudge();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(fetch).toHaveBeenCalledTimes(1);
    release("item");
    await vi.advanceTimersByTimeAsync(2_500);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("stop() cancels the next read and ignores a read that is still in flight", async () => {
    let release: (value: string) => void = () => undefined;
    const fetch = vi.fn<Options["fetch"]>(() => new Promise((resolve) => { release = resolve; }));
    const { options, poller } = setup({ fetch });
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    release("late");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(options.onResult).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
