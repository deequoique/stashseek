import { type ReactNode, useCallback, useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { defaultDeps, type PanelDeps } from "./deps";
import { errorCode, workerErrorCopy } from "./copy";
import { PageWorkspace } from "./PageWorkspace";
import { PanelLogin } from "./PanelLogin";
import { PrivacyLink } from "./PrivacyLink";

type AuthState =
  | { phase: "checking" }
  | { phase: "logged_out" }
  | { phase: "error"; message: string }
  | { phase: "ready" };

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error("request_timeout")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="sp-shell">
      <header className="sp-brand">
        <span className="sp-brand__mark" aria-hidden="true"><i /></span>
        <div>
          <p className="eyebrow">STASHSEEK CHAT</p>
          <p className="sp-brand__name">搜藏助手</p>
        </div>
      </header>
      {children}
    </div>
  );
}

/** Side panel root: login and device connection first, then the page view. */
export function SidePanel({ deps = defaultDeps }: { deps?: PanelDeps }) {
  const [auth, setAuth] = useState<AuthState>({ phase: "checking" });
  const [paired, setPaired] = useState(false);
  const [pageQa, setPageQa] = useState(false);
  const [transcriptionPlatforms, setTranscriptionPlatforms] = useState<readonly string[]>([]);
  const [check, setCheck] = useState(0);

  useEffect(() => {
    let live = true;
    setAuth({ phase: "checking" });
    void (async () => {
      try {
        // STATUS first: the worker drops credentials that belong to another build target.
        const status = await deps.worker.status();
        if (!live) return;
        setPaired(status.paired);
        await withTimeout(deps.getSession(), deps.requestTimeoutMs);
        const capabilities = await withTimeout(deps.getCapabilities(), deps.requestTimeoutMs);
        if (!live) return;
        setPageQa(capabilities.page_qa === true);
        setTranscriptionPlatforms(capabilities.transcription_platforms ?? []);
        setAuth({ phase: "ready" });
      } catch (error) {
        if (!live) return;
        if (error instanceof ApiError && error.status === 401) {
          setAuth({ phase: "logged_out" });
          return;
        }
        const code = error instanceof ApiError || error instanceof TypeError ? "network_unavailable" : errorCode(error);
        setAuth({ phase: "error", message: workerErrorCopy(code) });
      }
    })();
    return () => { live = false; };
  }, [deps, check]);

  const onSessionInvalid = useCallback(() => setAuth({ phase: "logged_out" }), []);
  const onLogout = useCallback(() => {
    void deps.worker.disconnect().catch(() => undefined).then(() => {
      setPaired(false);
      setAuth({ phase: "logged_out" });
    });
  }, [deps]);

  if (auth.phase === "checking") {
    return <Shell><p className="sp-hint" role="status">正在连接搜藏助手…</p></Shell>;
  }
  if (auth.phase === "error") {
    return (
      <Shell>
        <p className="sp-hint" role="alert">{auth.message}</p>
        <button className="button button--quiet" type="button" onClick={() => setCheck((value) => value + 1)}>重试</button>
      </Shell>
    );
  }
  if (auth.phase === "logged_out") {
    return (
      <Shell>
        <section className="sp-intro" aria-labelledby="sp-login-title">
          <h1 id="sp-login-title">登录搜藏助手</h1>
          <p>登录后，可以直接就当前视频提问，也可以把字幕保存到资料库。</p>
        </section>
        <PanelLogin worker={deps.worker} onDone={() => setCheck((value) => value + 1)} />
        <footer className="sp-footer"><PrivacyLink deps={deps} /></footer>
      </Shell>
    );
  }
  return (
    <PageWorkspace
      deps={deps}
      paired={paired}
      pageQa={pageQa}
      transcriptionPlatforms={transcriptionPlatforms}
      onPairedChange={setPaired}
      onSessionInvalid={onSessionInvalid}
      onLogout={onLogout}
    />
  );
}
