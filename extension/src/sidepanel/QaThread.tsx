import type { ConversationCitation } from "../api/contracts";
import { MarkdownAnswer } from "../chat/MarkdownAnswer";
import { formatTimestamp, panelAnswerText } from "./copy";
import type { QaTurn } from "./usePageQa";

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

interface QaThreadProps {
  turns: QaTurn[];
  onOpenCitation: (citation: ConversationCitation) => void;
  onRetry: (turnId: string) => void;
}

export function QaThread({ turns, onOpenCitation, onRetry }: QaThreadProps) {
  return (
    <ol className="sp-turns" aria-label="视频问答">
      {turns.map((turn) => (
        <li key={turn.id} className="sp-turn">
          <p className="sp-question">{turn.question}</p>
          <article className={`sp-answer sp-answer--${turn.status}`} aria-busy={turn.status === "streaming"}>
            {turn.status === "streaming" && !turn.answer ? <p className="sp-answer__state" role="status">正在回答…</p> : null}
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
      ))}
    </ol>
  );
}
