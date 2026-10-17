import { useEffect, useState } from "react";

import type { ConversationCitation } from "../api/contracts";
import { MarkdownAnswer } from "../chat/MarkdownAnswer";
import { StepTimeline, useAutoCollapse } from "../chat/StepTimeline";
import type { CollectEntry } from "./collectModel";
import { formatTimestamp, panelAnswerText } from "./copy";
import { collectProgress, collectResult, collectSteps, collectSummary, collectWorkspacePath, qaSteps, qaSummary } from "./steps";
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
  onOpenWorkspace: (route: string) => void;
}

/** The 「收藏视频」 action as a tool-call-style block: steps, elapsed time and ETA, then the way to the item. */
function CollectBlock({ entry, now, tickMs, onOpenWorkspace }: CollectBlockProps) {
  const active = entry.phase !== "done";
  const clock = useClock(now, tickMs, active);
  const progress = collectProgress(entry, clock);
  const steps = collectSteps(entry).map((step) => (
    step.outcome === "running" && progress ? { ...step, detail: <small className="sp-step-detail">{progress}</small> } : step
  ));
  const [expanded, setExpanded] = useAutoCollapse(entry.outcome === "ready" || entry.outcome === "already_exists");
  const result = collectResult(entry);
  const workspacePath = collectWorkspacePath(entry);
  return (
    <article className="sp-collect" aria-busy={active} aria-label="收藏视频">
      <StepTimeline
        className="sp-timeline"
        summary={collectSummary(entry, steps)}
        steps={steps}
        expanded={expanded}
        onToggle={setExpanded}
        stepsLabel="收藏步骤"
      />
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
  onOpenCitation: (citation: ConversationCitation) => void;
  onRetry: (turnId: string) => void;
  onOpenWorkspace: (route: string) => void;
}

export function QaThread({ thread, now, clockTickMs, onOpenCitation, onRetry, onOpenWorkspace }: QaThreadProps) {
  return (
    <ol className="sp-turns" aria-label="视频问答">
      {thread.map((entry) => {
        if (entry.kind === "collect") {
          return (
            <li key={entry.id} className="sp-turn sp-turn--action">
              <CollectBlock entry={entry} now={now} tickMs={clockTickMs} onOpenWorkspace={onOpenWorkspace} />
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
