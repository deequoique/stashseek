import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import {
  ApiError, cancelExternalLogin, getExternalLoginPending, logout,
  probeLoginSession, requestExternalLoginCode, verifyExternalLogin,
} from "../api/client";
import type { ExternalLoginPending, ExternalLoginVerifyInput, SessionInfo } from "../api/contracts";
import { BrandLogo } from "../app/BrandLogo";
import { useRouteNavigate } from "../app/RouteTransition";
import { externalProviderLabels } from "./ExternalLoginMethods";

interface ExternalLoginPageProps {
  loadPending?: (signal?: AbortSignal) => Promise<ExternalLoginPending>;
  probeSession?: (signal?: AbortSignal) => Promise<SessionInfo | null>;
  requestCode?: (email: string, signal?: AbortSignal) => Promise<{ status: "accepted" }>;
  verify?: (input: ExternalLoginVerifyInput, signal?: AbortSignal) => Promise<SessionInfo>;
  cancel?: (signal?: AbortSignal) => Promise<void>;
  signOut?: () => Promise<void>;
  onAuthenticated?: (session: SessionInfo) => void;
  onAnonymous?: () => void;
  onSignedOut?: () => void;
}

interface RequestContext { generation: number; signal: AbortSignal }
interface LoginContext { flow: ExternalLoginPending; session: SessionInfo | null }

function flowError(code?: string | null): string {
  switch (code) {
    case "session_present":
    case "session_changed": return "此浏览器的登录已改变。请继续当前资料库，或退出后重新开始。";
    case "binding_conflict": return "该账号无法绑定到这个邮箱。请使用原有登录方式，或重新开始。";
    case "provider_unavailable": return "此登录方式暂不可用，请使用邮箱登录或稍后重试。";
    case "email_delivery_unavailable": return "验证码暂时无法发送，请稍后重试。";
    case "verification_failed": return "验证码无效或已过期，请重新输入或重新发送。";
    default: return "登录流程未完成或已失效，请重新开始。";
  }
}

export function ExternalLoginPage({
  loadPending = getExternalLoginPending, probeSession = probeLoginSession,
  requestCode = requestExternalLoginCode, verify = verifyExternalLogin,
  cancel = cancelExternalLogin, signOut = logout, onAuthenticated, onSignedOut, onAnonymous,
}: ExternalLoginPageProps) {
  const navigate = useRouteNavigate();
  const [context, setContext] = useState<LoginContext | null>(null);
  const [checking, setChecking] = useState(true);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [error, setError] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const flowVersion = useRef<string | null>(null);

  function begin(): RequestContext {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    busy.current = true;
    return { generation: ++generation.current, signal: controller.signal };
  }
  function current(request: RequestContext): boolean {
    return mounted.current && generation.current === request.generation && !request.signal.aborted;
  }
  function activate(session: SessionInfo) {
    setCode("");
    setEmail("");
    if (onAuthenticated) onAuthenticated(session);
    else navigate("/library", { replace: true });
  }
  async function refresh(recovering = false, preserveError = false) {
    const request = begin();
    setChecking(true);
    try {
      // Check the flow against the Cookie visible after this Session read.
      // Parallel reads can pair an old completed flow with a newer account
      // using the same provider, whose public Session has no account ID.
      const session = await probeSession(request.signal);
      if (!current(request)) return;
      const flow = await loadPending(request.signal);
      if (!current(request)) return;
      if (!session) onAnonymous?.();
      if (!current(request)) return;
      const version = `${flow.provider ?? ""}:${flow.expires_at ?? ""}`;
      if (flowVersion.current !== version) {
        setEmail(""); setCode(""); setStep("email");
      }
      flowVersion.current = version;
      setContext({ flow, session });
      setReconciling(false);
      if (flow.status === "completed" && session && flow.provider === session.login_channel && !flow.error_code) {
        activate(session);
      } else if (recovering) {
        setCode("");
        setError(flow.status === "email_required" && !session
          ? "绑定尚未完成。请重新输入验证码后重试。"
          : flow.status === "completed" && !session
            ? "绑定已完成，请重新选择该登录方式进入资料库。"
            : flowError(flow.error_code));
      } else if (!preserveError) {
        setError(null);
      }
    } catch {
      if (current(request)) setError("无法确认登录结果，请检查网络后重新检查。");
    } finally {
      if (current(request)) { setChecking(false); busy.current = false; }
    }
  }

  useEffect(() => {
    mounted.current = true;
    void refresh();
    function recheck() { if (!busy.current) void refresh(); }
    window.addEventListener("focus", recheck);
    window.addEventListener("pageshow", recheck);
    return () => {
      mounted.current = false;
      generation.current += 1;
      activeRequest.current?.abort();
      window.removeEventListener("focus", recheck);
      window.removeEventListener("pageshow", recheck);
    };
    // The injected transport functions define this observer's lifetime.
  }, [loadPending, probeSession]);

  const codeRequest = useMutation({
    gcTime: 0, retry: false,
    mutationFn: ({ address, request }: { address: string; request: RequestContext }) => requestCode(address, request.signal),
    onSuccess: (_response, { request }) => {
      if (!current(request)) return;
      setStep("code"); setCode(""); setError(null);
    },
    onError: (failure, { request }) => {
      if (!current(request)) return;
      setError(failure instanceof ApiError ? flowError(failure.code) : "验证码请求未完成，请检查网络后重试。");
      if (failure instanceof ApiError && ["flow_invalid", "session_changed", "provider_unavailable"].includes(failure.code)) void refresh(false, true);
    },
    onSettled: (_data, _failure, { request }) => { if (current(request)) busy.current = false; },
  });
  const binding = useMutation({
    gcTime: 0, retry: false,
    mutationFn: ({ input, request }: { input: ExternalLoginVerifyInput; request: RequestContext }) => verify(input, request.signal),
    onSuccess: (session, { request }) => { if (current(request)) activate(session); },
    onError: (failure, { request }) => {
      if (!current(request)) return;
      if (failure instanceof ApiError && failure.code === "verification_failed") {
        setError(flowError(failure.code));
        return;
      }
      // A lost response may follow a committed binding. Never resend blindly.
      setReconciling(true);
      setError("正在核对绑定结果…");
      void refresh(true);
    },
    onSettled: (_data, _failure, { request }) => { if (current(request)) busy.current = false; },
  });
  const cancellation = useMutation({
    gcTime: 0, retry: false,
    mutationFn: ({ request, switching }: { request: RequestContext; switching: boolean }) =>
      (switching && context?.flow.status === "expired" ? Promise.resolve() : cancel(request.signal))
        .then(async () => { if (switching && current(request)) await signOut(); }),
    onSuccess: (_data, { request, switching }) => {
      if (!current(request)) return;
      setEmail(""); setCode("");
      if (switching && onSignedOut) onSignedOut();
      else navigate("/login", { replace: true });
    },
    onError: (_failure, { request }) => { if (current(request)) { setError("操作未完成，当前登录仍保留。请重新检查后重试。"); void refresh(false, true); } },
    onSettled: (_data, _failure, { request }) => { if (current(request)) busy.current = false; },
  });

  function sendCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current) return;
    const address = email.trim();
    if (!address.includes("@") || address.length < 3) { setError("请输入有效的邮箱地址。"); return; }
    setError(null);
    codeRequest.mutate({ address, request: begin() });
  }
  function submitBinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current || reconciling || context?.session) return;
    if (!/^\d{6}$/.test(code.trim())) { setError("请输入 6 位数字验证码。"); return; }
    setError(null);
    binding.mutate({ input: { email: email.trim(), code: code.trim(), confirm_binding: true }, request: begin() });
  }
  function cancelFlow(switching = false) {
    if (busy.current) return;
    if (!switching && context && ["expired", "cancelled", "failed"].includes(context.flow.status)) {
      navigate("/login", { replace: true });
      return;
    }
    cancellation.mutate({ request: begin(), switching });
  }
  function resendCode() {
    if (busy.current) return;
    codeRequest.mutate({ address: email.trim(), request: begin() });
  }
  const pending = checking || codeRequest.isPending || binding.isPending || cancellation.isPending;
  const flow = context?.flow;
  const provider = flow?.provider ? externalProviderLabels[flow.provider] : "所选账号";
  const expired = flow?.expires_at ? Date.parse(flow.expires_at) <= Date.now() : false;
  const canBind = flow?.status === "email_required" && !flow.error_code && !context?.session && !expired;
  const terminal = flow && !["created", "exchanging", "email_required"].includes(flow.status);

  return (
    <main className="login-page">
      <section className="login-card external-login-card" aria-labelledby="external-login-title" aria-busy={pending}>
        <a className="wordmark" href="/" aria-label="搜藏助手首页"><BrandLogo className="wordmark__sigil" /><span>StashSeek Chat</span></a>
        <h1 id="external-login-title">完成{provider}登录</h1>
        {checking ? <p role="status">正在确认登录结果…</p> : null}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        {!checking && !context ? <button className="button button--primary button--wide" type="button" onClick={() => void refresh(reconciling)}>重新检查</button> : null}
        {context?.session && !checking ? (
          <div className="external-session-notice">
            <p role="status">{flowError(flow?.error_code === "session_changed" ? "session_changed" : "session_present")}</p>
            <button className="button button--primary button--wide" type="button" onClick={() => activate(context.session!)}>继续当前资料库</button>
            <button className="login-back-button" type="button" disabled={pending} onClick={() => cancelFlow(true)}>退出后重新开始</button>
          </div>
        ) : null}
        {canBind && !reconciling && !checking ? (
          <>
            <p className="external-binding-purpose">验证邮箱并将{provider}绑定到该邮箱的资料库。已有收藏和检索记录会保留，之后可直接使用此登录方式。</p>
            {step === "email" ? (
              <form className="email-login-form" onSubmit={sendCode} noValidate>
                <div className="field"><label htmlFor="external-email">资料库邮箱</label><input id="external-email" type="email" autoComplete="email" required value={email} disabled={pending} onChange={(event) => setEmail(event.target.value)} /></div>
                <button className="button button--primary button--wide" type="submit" disabled={pending}>获取验证码</button>
              </form>
            ) : (
              <form className="email-login-form" onSubmit={submitBinding} noValidate>
                <p id="external-binding-target" className="email-login-confirmation">将{provider}绑定到 <strong>{email.trim()}</strong> 的资料库。提交即验证并绑定。</p>
                <div className="field"><label htmlFor="external-code">6 位验证码</label><input id="external-code" type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={6} aria-describedby="external-binding-target" value={code} disabled={pending} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} /></div>
                <button className="button button--primary button--wide" type="submit" disabled={pending}>{binding.isPending ? "正在验证并绑定…" : "验证并绑定"}</button>
                <div className="external-login-actions">
                  <button className="login-back-button" type="button" disabled={pending} onClick={() => { setStep("email"); setCode(""); setError(null); codeRequest.reset(); binding.reset(); }}>更换邮箱</button>
                  <button className="login-back-button" type="button" disabled={pending} onClick={resendCode}>重新发送验证码</button>
                </div>
              </form>
            )}
          </>
        ) : null}
        {reconciling && !checking ? <button className="button button--primary button--wide" type="button" onClick={() => void refresh(true)}>重新检查结果</button> : null}
        {!checking && !context?.session && (terminal || expired || (flow?.error_code && !canBind)) ? (
          <p role="status">{flow?.status === "completed" ? "绑定已完成，请重新选择该登录方式进入资料库。" : flowError(flow?.error_code)}</p>
        ) : null}
        {!checking && flow && ["created", "exchanging"].includes(flow.status) ? <p role="status">授权尚未完成。请完成授权后重新检查，或取消并重新开始。</p> : null}
        {flow && !context?.session ? <button className="login-back-button" type="button" disabled={pending} onClick={() => cancelFlow()}>取消并返回登录</button> : null}
        {!checking && flow && !canBind ? <button className="text-button" type="button" disabled={pending} onClick={() => void refresh(reconciling)}>重新检查</button> : null}
        <p className="privacy-note">邮箱和验证码仅在本页内使用。未完成验证与绑定前不会进入资料库。</p>
      </section>
    </main>
  );
}
