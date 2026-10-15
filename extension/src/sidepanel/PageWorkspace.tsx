import { type FormEvent, useState } from "react";

import { isGrantRejected, workerErrorCopy } from "./copy";
import type { PanelDeps } from "./deps";
import { PanelLogin } from "./PanelLogin";
import { QaThread } from "./QaThread";
import { questionBlock, usePageQa, type VideoSession } from "./usePageQa";

const MAX_QUESTION_CHARS = 4000;
const SUGGESTIONS = ["这个视频讲了什么？", "刚才说的是什么意思？"];

const PLATFORM_LABEL = { youtube: "YouTube", ntu_kaltura: "NTULearn" } as const;

interface PageWorkspaceProps {
  deps: PanelDeps;
  /** The capture device grant exists, so saving needs no new connection. */
  paired: boolean;
  /** The server offers page Q&A; without it the panel only saves. */
  pageQa: boolean;
  onPairedChange: (paired: boolean) => void;
  onSessionInvalid: () => void;
  onLogout: () => void;
}

function captionLine(session: VideoSession): { tone: "loading" | "ready" | "action" | "error"; text: string } {
  if (session.readStatus === "error") return { tone: "error", text: workerErrorCopy(session.readError ?? "request_failed") };
  const { page } = session;
  if (!page) return { tone: "loading", text: "正在读取字幕…" };
  const block = questionBlock(page);
  if (page.caption.status === "available") {
    const kind = page.caption.source === "auto_caption" ? "自动字幕" : "官方字幕";
    return { tone: "ready", text: `字幕可用 · ${kind} · ${page.caption.cues.length} 条` };
  }
  return block
    ? { tone: "action", text: block }
    : { tone: "action", text: "该视频没有字幕，提问将仅基于标题和简介回答" };
}

function saveLine(session: VideoSession): { tone: "success" | "action" | "error"; text: string } | null {
  const { save } = session;
  if (save.status === "saved") {
    return save.lifecycle === "needs_asr"
      ? { tone: "action", text: "视频没有可用字幕，已标记为需要语音转写。" }
      : { tone: "success", text: "已提交到收藏内容，搜藏助手正在准备对话。" };
  }
  return save.status === "error" ? { tone: "error", text: workerErrorCopy(save.code) } : null;
}

export function PageWorkspace({ deps, paired, pageQa, onPairedChange, onSessionInvalid, onLogout }: PageWorkspaceProps) {
  const controller = usePageQa(deps, onSessionInvalid);
  const { tab, session } = controller;
  const [draft, setDraft] = useState("");
  const [connecting, setConnecting] = useState(false);

  async function saveNow() {
    const outcome = await controller.save();
    if (!outcome.ok && isGrantRejected(outcome.code)) {
      onPairedChange(false);
      setConnecting(true);
    }
  }

  function requestSave() {
    if (paired) void saveNow();
    else setConnecting(true);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim() || session?.asking) return;
    controller.ask(draft);
    setDraft("");
  }

  const ready = session?.readStatus === "ready";
  const block = session?.page ? questionBlock(session.page) : null;
  const asking = session?.asking === true;
  const caption = session ? captionLine(session) : null;
  const saved = session ? saveLine(session) : null;
  const saving = session?.save.status === "saving";

  return (
    <div className="sp-shell">
      <header className="sp-brand">
        <span className="sp-brand__mark" aria-hidden="true"><i /></span>
        <div>
          <p className="eyebrow">STASHSEEK CHAT</p>
          <p className="sp-brand__name">搜藏助手</p>
        </div>
        <button className="sp-link" type="button" onClick={onLogout}>退出登录</button>
      </header>

      {tab === undefined ? <p className="sp-hint" role="status">正在读取当前页面…</p> : null}
      {tab !== undefined && !session ? (
        <section className="sp-hint sp-empty" aria-labelledby="sp-empty-title">
          <h1 id="sp-empty-title">打开 YouTube 或 NTULearn 视频后提问</h1>
          <p>切到一个视频页，就可以直接问它讲了什么，并跳到对应的时间点。</p>
        </section>
      ) : null}

      {session ? (
        <>
          <section className="sp-video" aria-labelledby="sp-video-title">
            <p className="eyebrow">{PLATFORM_LABEL[session.platform]}</p>
            <h1 id="sp-video-title">{session.page?.metadata.title ?? (session.readStatus === "error" ? "无法读取视频信息" : "正在读取视频信息…")}</h1>
            {caption ? (
              <div className="sp-status" data-tone={caption.tone}>
                <span className="sp-status__dot" aria-hidden="true" />
                <p role="status">{caption.text}</p>
              </div>
            ) : null}
            {session.readStatus === "error" ? (
              <button className="text-button" type="button" onClick={controller.reread}>重新读取</button>
            ) : null}

            {connecting ? (
              <div className="sp-connect">
                <p>保存字幕需要先连接这台设备，请用邮箱验证码确认。</p>
                <PanelLogin
                  worker={deps.worker}
                  onCancel={() => setConnecting(false)}
                  onDone={() => { onPairedChange(true); setConnecting(false); void saveNow(); }}
                />
              </div>
            ) : (
              <button
                className="button button--quiet button--wide sp-save"
                type="button"
                disabled={!ready || saving}
                onClick={requestSave}
              >
                {saving ? "正在保存…" : session.save.status === "saved" ? "再次保存" : "保存到资料库"}
              </button>
            )}
            {saved ? <p className="sp-save__result" data-tone={saved.tone} role="status">{saved.text}</p> : null}
            <p className="sp-privacy">提问时字幕只用于生成回答，历史记录只保留引用的短句；点“保存到资料库”才会提交完整字幕。</p>
          </section>

          {pageQa ? (
            <section className="sp-qa" aria-label="就当前视频提问">
              {session.turns.length === 0 && !block ? (
                <div className="sp-suggestions" aria-label="示例问题">
                  {SUGGESTIONS.map((suggestion) => (
                    <button key={suggestion} type="button" onClick={() => setDraft(suggestion)}>{suggestion}</button>
                  ))}
                </div>
              ) : null}
              <QaThread turns={session.turns} onOpenCitation={(citation) => { void controller.openCitation(citation); }} onRetry={controller.retry} />
              {controller.notice ? <p className="sp-hint" role="status">{controller.notice}</p> : null}
            </section>
          ) : (
            <p className="sp-hint">当前服务未开放视频问答，你仍然可以保存字幕。</p>
          )}

          {pageQa && !block ? (
            <form className="sp-compose" onSubmit={submit}>
              <label className="sr-only" htmlFor="sp-question">就当前视频提问</label>
              <textarea
                id="sp-question"
                rows={2}
                maxLength={MAX_QUESTION_CHARS}
                value={draft}
                disabled={!ready}
                placeholder={ready ? "就这个视频提问…" : "正在读取字幕…"}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              {asking ? (
                <button className="button button--quiet" type="button" onClick={controller.stop}>停止</button>
              ) : (
                <button className="button button--primary" type="submit" disabled={!ready || !draft.trim()}>发送</button>
              )}
            </form>
          ) : null}
        </>
      ) : null}

      <footer className="sp-footer">
        <button className="text-button" type="button" onClick={() => { void deps.worker.openWorkspace(); }}>打开工作区</button>
      </footer>
    </div>
  );
}
