import { afterEach, expect, it, vi } from "vitest";

import { startRouteTransition } from "./RouteTransition";

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(document, "startViewTransition");
  document.documentElement.classList.remove("route-transition--entering");
});

it("adds a short enter animation when the browser has no native view transition", () => {
  vi.useFakeTimers();
  Reflect.deleteProperty(document, "startViewTransition");
  const update = vi.fn();

  startRouteTransition(update);

  expect(update).toHaveBeenCalledOnce();
  expect(document.documentElement.classList.contains("route-transition--entering")).toBe(true);
  vi.advanceTimersByTime(380);
  expect(document.documentElement.classList.contains("route-transition--entering")).toBe(false);
});

it("consumes the expected AbortError when a native transition is skipped", async () => {
  const update = vi.fn();
  const startViewTransition = vi.fn((callback: () => void) => {
    callback();
    return {
      ready: Promise.reject(new DOMException("Transition was skipped", "AbortError")),
    };
  });
  Object.defineProperty(document, "startViewTransition", {
    configurable: true,
    value: startViewTransition,
  });

  startRouteTransition(update);
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(startViewTransition).toHaveBeenCalledOnce();
  expect(update).toHaveBeenCalledOnce();
});
