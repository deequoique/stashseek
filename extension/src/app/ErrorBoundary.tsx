import { Component, type ErrorInfo, type ReactNode } from "react";
import { useLocation } from "react-router";

interface ErrorBoundaryProps {
  children: ReactNode;
  resetKey?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/** A stale extension tab keeps the old chunk URLs after a release. */
export function isChunkLoadError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /ChunkLoadError|Loading chunk \d+ failed|dynamically imported module|Importing a module script failed|Failed to fetch/i.test(text);
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("route_render_failed", error, info.componentStack);
  }

  componentDidUpdate(previous: ErrorBoundaryProps): void {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    const staleChunk = isChunkLoadError(this.state.error);
    return (
      <main className="route-error" role="alert">
        <h1>{staleChunk ? "页面版本已更新" : "页面暂时无法显示"}</h1>
        <p>
          {staleChunk
            ? "这个标签页加载的旧版本已经失效，重新加载即可继续。"
            : "刷新页面后再试一次；如果仍然失败，请稍后回来。"}
        </p>
        <div className="route-error__actions">
          <button className="button button--primary" type="button" onClick={() => window.location.reload()}>
            重新加载
          </button>
        </div>
      </main>
    );
  }
}

/** Reset the boundary when navigation succeeds, so one bad page does not stick. */
export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  return (
    <ErrorBoundary resetKey={`${location.pathname}${location.search}`}>
      {children}
    </ErrorBoundary>
  );
}
