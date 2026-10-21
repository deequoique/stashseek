import { type ReactNode, useId } from "react";

import type { IngestStage } from "../api/contracts";
import { isKnownStageKey, STAGE_LABEL, STAGE_PLACEHOLDER } from "../ingest/ingestCopy";
import { BODY_KEY } from "../ingest/ingestModel";

interface StageRegionProps {
  /** A stage after the body, from `visibleStages`. */
  stage: IngestStage;
  /** What the finished stage produced; the stage's owner supplies it. */
  children?: ReactNode;
}

/**
 * The region of one stage that follows the body on the detail page. The body is already searchable
 * by then, so a stage that failed only shows its placeholder; it never marks the item unusable.
 */
export function StageRegion({ stage, children }: StageRegionProps) {
  const titleId = useId();
  if (!isKnownStageKey(stage.key) || stage.key === BODY_KEY) return null;
  const waiting = stage.status === "pending" || stage.status === "running";
  const content = waiting
    ? <p className="muted" aria-live="polite">整理中…</p>
    : stage.status === "done" && children ? children : <p className="description-copy">{STAGE_PLACEHOLDER[stage.key]}</p>;
  return (
    <section className="detail-section stage-region" aria-labelledby={titleId} data-stage={stage.key} data-status={stage.status}>
      <p className="eyebrow">AI 整理</p>
      <h2 id={titleId}>{STAGE_LABEL[stage.key]}</h2>
      {content}
    </section>
  );
}
