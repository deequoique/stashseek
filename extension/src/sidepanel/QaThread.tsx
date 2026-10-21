import { useEffect, useState } from "react";

import type { ConversationCitation } from "../api/contracts";
import { MarkdownAnswer } from "../chat/MarkdownAnswer";
import { StepTimeline, useAutoCollapse } from "../chat/StepTimeline";
import { IngestProgress } from "../ingest/IngestProgress";
import { type IngestPrimaryAction, IngestUnfinished } from "../ingest/IngestUnfinished";
import { ACTION_LABEL, platformLabel, QUOTA_RETRY_COPY, RETRY_FAILED_COPY, TITLE_SEARCH_ROUTE } from "../ingest/ingestCopy";
import { type CollectEntry, entryStages } from "./collectModel";
import { formatTimestamp, panelAnswerText } from "./copy";
import {
  collectBody,
  collectNote,
  collectProgress,
  collectResult,
  collectSteps,
  collectSummary,
  collectWorkspacePath,
  qaSteps,
  qaSummary,
} from "./steps";
import type { QaTurn, ThreadEntry } from "./usePageQa";

function CitationChips({
  citations,
  onOpen,
}: {
  citations: ConversationCitation[];
  onOpen: (citation: ConversationCitation) => void;
}) {
  if (!citations.length) return null;
  return (
    <ol className="sp-citations" aria-label="回答依据">
      {citations.map((citation, index) => {
        const time = typeof citation.start_sec === "number" ? formatTimestamp(citation.start_sec) : null;
        return (
          <li key={`${citation.url}:${citation.start_sec ?? ""}:${index}`}>
            <button
              className="sp-citation"
              type="button"
              title={time ? `跳转到 ${time}` : "在新标签页打开"}
              onClick={() => onOpen(citation)}
            >
              <span className="sp-citation__time">{time ?? "打开"}</span>
              <span className="sp-citation__title">{citation.title}</span>
            </button>
            <details className="sp-citation__excerpt">
              <summary>展开字幕依据</summary>
              <p>{citation.excerpt}</p>
            </details>
          </li>
        );
      })}
    </ol>
  );
}

/** Client-side steps above a streamed answer; folds away once the answer starts. */
function TurnSteps({ turn }: { turn: QaTurn }) {
  const steps = qaSteps(turn);
  const [expanded, setExpanded] = useAutoCollapse(turn.answer !== "" || turn.status !== "streaming");
  return (
    <StepTimeline
      className="sp-timeline"
      summary={qaSummary(steps)}
      steps={steps}
      expanded={expanded}
      onToggle={setExpanded}
      stepsLabel="回答步骤"
    />
  );
}

/** Re-renders every `tickMs` while `active`, returning the current time. */
function useClock(now: () => number, tickMs: number, active: boolean): number {
  const [value, setValue] = useState(now);
  useEffect(() => {
    if (!active) return undefined;
    setValue(now());
    const timer = setInterval(() => setValue(now()), tickMs);
    return () => clearInterval(timer);
  }, [active, now, tickMs]);
  return value;
}

interface CollectBlockProps {
  entry: CollectEntry;
  now: () => number;
  tickMs: number;
  /** The server transcribes this video's platform, so a caption-less video is not a dead end. */
  willTranscribe: boolean;
  /** The block may act: only the latest 「收藏视频」 block of the thread has live buttons. */
  actionable: boolean;
  onOpenWorkspace: (route: string) => void;
  onRetryCollect: (entryId: string) => void;
  onRecollect: () => void;
}

/** The main button of the unfinished card, by why the body is not searchable. */
function unfinishedPrimary(
  entry: CollectEntry,
  { actionable, onRetryCollect, onRecollect }: Pick<CollectBlockProps, "actionable" | "onRetryCollect" | "onRecollect">,
): IngestPrimaryAction | null {
  if (!actionable || !entry.reason) return null;
  // The server cannot read login-only subtitles; collecting again from the page can.
  if (entry.reason.category === "login_required") return { kind: "button", label: ACTION_LABEL.recollect, onClick: onRecollect };
  if (!entry.retryable) return null;
  return { kind: "button", label: ACTION_LABEL.retry, onClick: () => onRetryCollect(entry.id), pending: entry.retryState === "pending" };
}

function retryError(entry: CollectEntry): string | null {
  if (entry.retryState === "quota_exceeded") return QUOTA_RETRY_COPY;
  return entry.retryState === "failed" ? RETRY_FAILED_COPY : null;
}

/**
 * The 「收藏视频」 action. Reading and submitting show as steps; once the server has the item the
 * block shows its ingest progress, or why it did not finish and what can be done about it.
 */
function CollectBlock({ entry, now, tickMs, willTranscribe, actionable, onOpenWorkspace, onRetryCollect, onRecollect }: CollectBlockProps) {
  const active = entry.phase !== "done";
  const clock = useClock(now, tickMs, active);
  const progress = collectProgress(entry, clock);
  const [expanded, setExpanded] = useState(true);
  const body = collectBody(entry);
  const note = collectNote(entry, willTranscribe);
  const steps = collectSteps(entry, willTranscribe).map((step) => (
    step.outcome === "running" && progress ? { ...step, detail: <small className="sp-step-detail">{progress}</small> } : step
  ));
  const result = collectResult(entry);
  const workspacePath = collectWorkspacePath(entry);
  return (
    <article className="sp-collect" aria-busy={active} aria-label="收藏视频">
      {body === "steps" ? (
        <StepTimeline
          className="sp-timeline"
          summary={collectSummary(entry, steps)}
          steps={steps}
          expanded={expanded}
          onToggle={setExpanded}
          stepsLabel="收藏步骤"
        />
      ) : (
        <>
          {note ? <p className="sp-collect__note">{note}</p> : null}
          {body === "progress" ? (
            <IngestProgress stages={entryStages(entry)} searchable={entry.bodySearchable} note={progress} />
          ) : entry.reason ? (
            <IngestUnfinished
              title={entry.itemTitle}
              platformName={platformLabel(entry.itemPlatform ?? "")}
              reason={entry.reason}
              primary={unfinishedPrimary(entry, { actionable, onRetryCollect, onRecollect })}
              onSearchTitles={() => onOpenWorkspace(TITLE_SEARCH_ROUTE)}
              error={retryError(entry)}
            />
          ) : null}
        </>
      )}
      {result ? <p className="sp-collect__result" data-tone={result.tone} role="status">{result.text}</p> : null}
      {workspacePath ? (
        <button className="sp-link sp-collect__link" type="button" onClick={() => onOpenWorkspace(workspacePath)}>在工作区查看</button>
      ) : null}
    </article>
  );
}

interface QaThreadProps {
  thread: ThreadEntry[];
  now: () => number;
  clockTickMs: number;
  /** The server transcribes this video's platform. */
  willTranscribe: boolean;
  onOpenCitation: (citation: ConversationCitation) => void;
  onRetry: (turnId: string) => void;
  onOpenWorkspace: (route: string) => void;
  onRetryCollect: (entryId: string) => void;
  /** 「重新收藏」: collect the video again from the page. */
  onRecollect: () => void;
}

export function QaThread({
  thread,
  now,
  clockTickMs,
  willTranscribe,
  onOpenCitation,
  onRetry,
  onOpenWorkspace,
  onRetryCollect,
  onRecollect,
}: QaThreadProps) {
  let latestCollectId: string | null = null;
  for (const entry of thread) if (entry.kind === "collect") latestCollectId = entry.id;
  return (
    <ol className="sp-turns" aria-label="视频问答">
      {thread.map((entry) => {
        if (entry.kind === "collect") {
          return (
            <li key={entry.id} className="sp-turn sp-turn--action">
              <CollectBlock
                entry={entry}
                now={now}
                tickMs={clockTickMs}
                willTranscribe={willTranscribe}
                actionable={entry.id === latestCollectId}
                onOpenWorkspace={onOpenWorkspace}
                onRetryCollect={onRetryCollect}
                onRecollect={onRecollect}
              />
            </li>
          );
        }
        const turn = entry;
        return (
          <li key={turn.id} className="sp-turn">
            <p className="sp-question">{turn.question}</p>
            <article className={`sp-answer sp-answer--${turn.status}`} aria-busy={turn.status === "streaming"}>
              <TurnSteps turn={turn} />
              {turn.status === "streaming" && !turn.answer ? <p className="sr-only" role="status">正在回答…</p> : null}
              {turn.answer ? <MarkdownAnswer>{panelAnswerText(turn.answer, turn.citations.length > 0)}</MarkdownAnswer> : null}
              {turn.status === "failed" || turn.status === "stopped" ? (
                <p className="sp-answer__state" role={turn.status === "failed" ? "alert" : "status"}>
                  {turn.error}
                  {turn.status === "failed" ? (
                    <> <button className="sp-link" type="button" onClick={() => onRetry(turn.id)}>重试</button></>
                  ) : null}
                </p>
              ) : null}
              {turn.status === "done" ? <CitationChips citations={turn.citations} onOpen={onOpenCitation} /> : null}
            </article>
          </li>
        );
      })}
    </ol>
  );
}
