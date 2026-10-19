import { type ReactNode, useId } from "react";

import type { IngestStage } from "../api/contracts";
import {
  BODY_DETAIL_LABEL,
  isKnownStageKey,
  PROGRESS_EXPLANATION,
  PROGRESS_TITLE,
  SAVED_LINK_STEP,
  SEARCHABLE_STATUS,
  STAGE_LABEL,
  STAGE_PLACEHOLDER,
} from "./ingestCopy";
import { BODY_KEY, normalizeStages } from "./ingestModel";

interface Mark {
  glyph: string;
  className: string;
  /** Spoken in place of the glyph. */
  spoken: string;
}

const DONE: Mark = { glyph: "✓", className: "is-done", spoken: "已完成" };
const PENDING: Mark = { glyph: "○", className: "is-pending", spoken: "未开始" };
const MARKS: Record<string, Mark> = {
  done: DONE,
  running: { glyph: "◌", className: "is-running", spoken: "进行中" },
  pending: PENDING,
  failed: { glyph: "!", className: "is-failed", spoken: "未完成" },
};

function markOf(status: string): Mark {
  return (Object.hasOwn(MARKS, status) ? MARKS[status] : undefined) ?? PENDING;
}

/** A waiting or running body stage says what it is doing; every other row uses its stage name. */
function rowText(stage: IngestStage): string {
  if (!isKnownStageKey(stage.key)) return stage.key;
  const label = STAGE_LABEL[stage.key];
  if (stage.key === BODY_KEY) {
    const waiting = stage.status === "pending" || stage.status === "running";
    const detail = waiting && stage.detail && Object.hasOwn(BODY_DETAIL_LABEL, stage.detail) ? BODY_DETAIL_LABEL[stage.detail] : null;
    return detail ?? label;
  }
  return stage.status === "failed" ? `${label} · ${STAGE_PLACEHOLDER[stage.key]}` : label;
}

interface IngestProgressProps {
  /** The stages from the server. Unknown and `skipped` stages are not shown. */
  stages: readonly IngestStage[];
  /** The body is in the search index; when not given, a finished body stage says so. */
  searchable?: boolean;
  /** A secondary line under the first stage that is not done, such as elapsed time. */
  note?: ReactNode;
}

/** The screen after 「收藏」: the item is saved, and each stage is done, running or not started. */
export function IngestProgress({ stages, searchable, note }: IngestProgressProps) {
  const titleId = useId();
  const shown = normalizeStages(stages);
  const showSearchable = searchable ?? shown.some((stage) => stage.key === BODY_KEY && stage.status === "done");
  const running = shown.findIndex((stage) => stage.status === "running");
  const noteIndex = running >= 0 ? running : shown.findIndex((stage) => stage.status === "pending");
  return (
    <section className="ingest-progress" aria-labelledby={titleId}>
      <p className="eyebrow">内容整理</p>
      <h2 id={titleId}>{PROGRESS_TITLE}</h2>
      <p className="ingest-muted">{PROGRESS_EXPLANATION}</p>
      <ol className="ingest-steps" aria-label="整理步骤">
        <li className={`ingest-step ${DONE.className}`}>
          <span className="ingest-step__mark" aria-hidden="true">{DONE.glyph}</span>
          <span>
            {SAVED_LINK_STEP}
            <span className="sr-only">，{DONE.spoken}</span>
          </span>
        </li>
        {shown.map((stage, index) => {
          const mark = markOf(stage.status);
          return (
            <li className={`ingest-step ${mark.className}`} key={stage.key}>
              <span className="ingest-step__mark" aria-hidden="true">{mark.glyph}</span>
              <span>
                {rowText(stage)}
                <span className="sr-only">，{mark.spoken}</span>
                {note && index === noteIndex ? <span className="ingest-step__note">{note}</span> : null}
              </span>
            </li>
          );
        })}
      </ol>
      {showSearchable ? <p className="ingest-status ingest-status--ready" role="status">{SEARCHABLE_STATUS}</p> : null}
    </section>
  );
}
