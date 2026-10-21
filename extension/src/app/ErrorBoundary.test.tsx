import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary, isChunkLoadError, RouteErrorBoundary } from "./ErrorBoundary";

function Boom({ message }: { message: string }): never {
  throw new Error(message);
}

describe("route error boundary", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("explains a stale chunk instead of leaving a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary resetKey="/library">
        <Boom message="Failed to fetch dynamically imported module" />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("heading", { name: "页面版本已更新" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("offers a generic retry for other render failures", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary resetKey="/login">
        <Boom message="boom" />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("heading", { name: "页面暂时无法显示" })).toBeInTheDocument();
  });

  it("clears the error when navigation changes the reset key", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(
      <ErrorBoundary resetKey="/library">
        <Boom message="boom" />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    rerender(
      <ErrorBoundary resetKey="/library/next">
        <p>recovered</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("recovered")).toBeInTheDocument();
  });

  it("wraps routes and recognizes chunk failures", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <RouteErrorBoundary>
          <Boom message="Importing a module script failed." />
        </RouteErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "页面版本已更新" })).toBeInTheDocument();
    expect(isChunkLoadError("Loading chunk 42 failed")).toBe(true);
    expect(isChunkLoadError("ordinary failure")).toBe(false);
  });
});
