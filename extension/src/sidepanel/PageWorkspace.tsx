import { type FormEvent, useState } from "react";

import { BILIBILI_NO_CAPTIONS_HINT, workerErrorCopy } from "./copy";
import type { PanelDeps } from "./deps";
import { PanelLogin } from "./PanelLogin";
import { PrivacyLink } from "./PrivacyLink";
import { QaThread } from "./QaThread";
import { isActiveCollect } from "./useCollect";
import { questionBlock, usePageQa, type VideoSession } from "./usePageQa";

const MAX_QUESTION_CHARS = 4000;
const SUGGESTIONS = ["这个视频讲了什么？", "刚才说的是什么意思？"];

const PLATFORM_LABEL = { youtube: "YouTube", bilibili: "哔哩哔哩" } as const;

interface PageWorkspaceProps {
  deps: PanelDeps;
  /** The capture device grant exists, so saving needs no new connection. */
  paired: boolean;
  /** The server offers page Q&A; without it the panel only saves. */
  pageQa: boolean;
  /** Platforms whose videos the server transcribes when they have no captions; empty when it does not. */
  transcriptionPlatforms: readonly string[];
  onPairedChange: (paired: boolean) => void;
  onSessionInvalid: () => void;
  onLogout: () => void;
}

const NO_CAPTIONS_ANSWER_COPY = "该视频没有字幕，提问将仅基于标题和简介回答";
/** Points at 「收藏视频」 without promising the panel can answer from the transcript: it still reads browser captions only. */
const NO_CAPTIONS_TRANSCRIBE_COPY = `${NO_CAPTIONS_ANSWER_COPY}。收藏后会自动语音转写，转写完成后可以按内容搜索。`;

function captionLine(session: VideoSession, willTranscribe: boolean): { tone: "loading" | "ready" | "action" | "error"; text: string } {
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
    : { tone: "action", text: willTranscribe ? NO_CAPTIONS_TRANSCRIBE_COPY : NO_CAPTIONS_ANSWER_COPY };
}

export function PageWorkspace({ deps, paired, pageQa, transcriptionPlatforms, onPairedChange, onSessionInvalid, onLogout }: PageWorkspaceProps) {
  const controller = usePageQa(deps, onSessionInvalid, { paired, onGrantRejected: () => onPairedChange(false) });
  const { tab, session } = controller;
  const [draft, setDraft] = useState("");
  const [connecting, setConnecting] = useState(false);

  function openWorkspaceRoute(route: string) {
    void deps.tabs.openUrl(deps.workspaceUrl(route));
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
  const willTranscribe = session !== null && transcriptionPlatforms.includes(session.platform);
  const caption = session ? captionLine(session, willTranscribe) : null;
  const collecting = session?.thread.some(isActiveCollect) === true;

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
          <h1 id="sp-empty-title">打开 YouTube 或哔哩哔哩视频后提问</h1>
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
            {session.page?.platform === "bilibili" && session.page.caption.status === "unavailable" ? (
              <p className="sp-hint">{BILIBILI_NO_CAPTIONS_HINT}</p>
            ) : null}
            {session.readStatus === "error" ? (
              <button className="text-button" type="button" onClick={controller.reread}>重新读取</button>
            ) : null}

            <button
              className="button button--primary button--wide sp-collect-button"
              type="button"
              disabled={collecting}
              onClick={controller.collect}
            >
              {collecting ? "收藏中…" : "收藏视频"}
            </button>
            {connecting ? (
              <div className="sp-connect">
                <p>连接这台设备后，收藏时可以直接读取浏览器里的字幕（包括需要登录才能看的字幕），请用邮箱验证码确认。</p>
                <PanelLogin
                  worker={deps.worker}
                  onCancel={() => setConnecting(false)}
                  onDone={() => { onPairedChange(true); setConnecting(false); }}
                />
              </div>
            ) : paired ? null : (
              <button className="text-button sp-connect-toggle" type="button" onClick={() => setConnecting(true)}>
                连接这台设备，直接读取浏览器里的字幕
              </button>
            )}
            <p className="sp-privacy">提问时字幕只用于生成回答，历史记录只保留引用的短句；点「收藏视频」才会把视频保存到资料库。</p>
          </section>

          <section className="sp-qa" aria-label="就当前视频提问">
            {pageQa && session.thread.length === 0 && !block ? (
              <div className="sp-suggestions" aria-label="示例问题">
                {SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} type="button" onClick={() => setDraft(suggestion)}>{suggestion}</button>
                ))}
              </div>
            ) : null}
            <QaThread
              thread={session.thread}
              now={deps.now}
              clockTickMs={deps.clockTickMs}
              willTranscribe={willTranscribe}
              onOpenCitation={(citation) => { void controller.openCitation(citation); }}
              onRetry={controller.retry}
              onOpenWorkspace={openWorkspaceRoute}
              onRetryCollect={controller.retryCollect}
              onRecollect={controller.recollect}
            />
            {controller.notice ? <p className="sp-hint" role="status">{controller.notice}</p> : null}
            {pageQa ? null : <p className="sp-hint">当前服务未开放视频问答，你仍然可以收藏视频。</p>}
          </section>

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
        <PrivacyLink deps={deps} />
      </footer>
    </div>
  );
}
