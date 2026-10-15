import type { FormEvent } from "react";

export type EmailStep = "email" | "code";

export interface EmailLoginFlowProps {
  email: string;
  code: string;
  step: EmailStep;
  pendingEmail: boolean;
  pendingCode: boolean;
  error: string | null;
  onEmailChange: (value: string) => void;
  onCodeChange: (value: string) => void;
  onSubmitEmail: (event: FormEvent<HTMLFormElement>) => void;
  onSubmitCode: (event: FormEvent<HTMLFormElement>) => void;
  onChangeEmail: () => void;
}

export function EmailLoginFlow({
  email,
  code,
  step,
  pendingEmail,
  pendingCode,
  error,
  onEmailChange,
  onCodeChange,
  onSubmitEmail,
  onSubmitCode,
  onChangeEmail,
}: EmailLoginFlowProps) {
  return (
    <div className="email-login-flow">
      {step === "email" ? (
        <form className="email-login-form" onSubmit={onSubmitEmail} noValidate>
          <div className="field">
            <label htmlFor="login-email">邮箱地址</label>
            <input
              id="login-email"
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              required
              value={email}
              onChange={(event) => onEmailChange(event.target.value)}
              aria-describedby="login-email-help"
            />
            <p className="field-help" id="login-email-help">
              输入邮箱后，我们会发送一次性验证码。
            </p>
          </div>
          <button className="button button--primary button--wide" type="submit" disabled={pendingEmail}>
            {pendingEmail ? "正在发送…" : "获取验证码"}
          </button>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {pendingEmail ? <p className="login-capability-note" aria-live="polite">正在准备登录…</p> : null}
        </form>
      ) : (
        <form className="email-login-form" onSubmit={onSubmitCode} noValidate>
          <p className="email-login-confirmation" aria-live="polite">
            如果该邮箱已绑定，验证码已发送，请查收邮件。
          </p>
          <div className="field">
            <label htmlFor="login-code">6 位验证码</label>
            <input
              id="login-code"
              name="code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(event) => onCodeChange(event.target.value)}
              aria-describedby="login-code-help"
            />
            <p className="field-help" id="login-code-help">
              验证码为 6 位数字，仅在本页内使用。
            </p>
          </div>
          <button className="button button--primary button--wide" type="submit" disabled={pendingCode}>
            {pendingCode ? "正在验证…" : "确认登录"}
          </button>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          <button className="login-back-button" type="button" onClick={onChangeEmail}>
            ← 更换邮箱
          </button>
        </form>
      )}
    </div>
  );
}
