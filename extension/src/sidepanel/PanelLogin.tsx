import { type FormEvent, useState } from "react";

import { EmailLoginFlow, type EmailStep } from "../auth/EmailLoginFlow";
import { errorCode, PAIRING_RESET_CODES, workerErrorCopy } from "./copy";
import type { WorkerApi } from "./workerApi";

interface PanelLoginProps {
  worker: Pick<WorkerApi, "startLogin" | "finishLogin">;
  /** Called once the email code was accepted; the worker then holds the session and the capture device grant. */
  onDone: () => void;
  onCancel?: () => void;
}

/** Email-code login. The worker pairs this browser as a capture device in the same step. */
export function PanelLogin({ worker, onDone, onCancel }: PanelLoginProps) {
  const [step, setStep] = useState<EmailStep>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = email.trim();
    if (value.length < 3 || !value.includes("@")) {
      setError("请输入有效的邮箱地址。");
      return;
    }
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await worker.startLogin(value);
      setStep("code");
      setCode("");
    } catch (failure) {
      setError(workerErrorCopy(errorCode(failure)));
    } finally {
      setPending(false);
    }
  }

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = code.trim();
    if (!/^\d{6}$/.test(value)) {
      setError("请输入 6 位数字验证码。");
      return;
    }
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await worker.finishLogin(email.trim(), value);
      onDone();
    } catch (failure) {
      const reason = errorCode(failure);
      setError(workerErrorCopy(reason));
      // An expired or used pairing cannot be finished; the user asks for a new code.
      if (PAIRING_RESET_CODES.has(reason)) setStep("email");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="sp-login">
      <EmailLoginFlow
        email={email}
        code={code}
        step={step}
        pendingEmail={pending && step === "email"}
        pendingCode={pending && step === "code"}
        error={error}
        onEmailChange={(value) => { setEmail(value); setError(null); }}
        onCodeChange={(value) => { setCode(value.replace(/\D/g, "").slice(0, 6)); setError(null); }}
        onSubmitEmail={(event) => { void submitEmail(event); }}
        onSubmitCode={(event) => { void submitCode(event); }}
        onChangeEmail={() => { setStep("email"); setCode(""); setError(null); }}
      />
      {onCancel ? <button className="text-button sp-login__cancel" type="button" onClick={onCancel}>取消</button> : null}
    </div>
  );
}
