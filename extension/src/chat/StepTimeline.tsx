import { type ReactNode, useEffect, useRef, useState } from "react";

export type StepOutcome = "running" | "completed" | "failed" | "skipped";

export interface TimelineStep {
  id: string;
  text: string;
  outcome: StepOutcome;
  /** Secondary line under the step text, e.g. elapsed time. */
  detail?: ReactNode;
}

/**
 * Open until `collapseWhen` turns true, then collapse once; the user may reopen it.
 * Turning `collapseWhen` false again re-arms the collapse and reopens the block.
 */
export function useAutoCollapse(collapseWhen: boolean): [boolean, (open: boolean) => void] {
  const [expanded, setExpanded] = useState(true);
  const collapsed = useRef(false);

  useEffect(() => {
    if (!collapseWhen) {
      collapsed.current = false;
      setExpanded(true);
      return;
    }
    if (!collapsed.current) {
      collapsed.current = true;
      setExpanded(false);
    }
  }, [collapseWhen]);

  return [expanded, setExpanded];
}

interface StepTimelineProps {
  summary: string;
  steps: TimelineStep[];
  expanded: boolean;
  onToggle: (open: boolean) => void;
  /** Accessible name of the step list. */
  stepsLabel?: string;
  className?: string;
  /** Rendered above the step list inside the body. */
  children?: ReactNode;
}

/**
 * Collapsible, tool-call-style list of steps: a spinner on the running step, a check on
 * finished steps, and failed or skipped states. Used by the workspace answer timeline and
 * the side panel thread.
 */
export function StepTimeline({
  summary,
  steps,
  expanded,
  onToggle,
  stepsLabel = "Agent 执行步骤",
  className,
  children,
}: StepTimelineProps) {
  return (
    <details
      className={className ? `chat-execution ${className}` : "chat-execution"}
      open={expanded}
      onToggle={(event) => onToggle(event.currentTarget.open)}
    >
      <summary>
        <span>{summary}</span>
        <small>{expanded ? "收起" : "展开"}</small>
      </summary>
      <div className="chat-execution__body">
        {children}
        {steps.length ? (
          <ol className="chat-execution-steps" aria-label={stepsLabel}>
            {steps.map((step) => (
              <li className={step.detail ? `is-${step.outcome} has-detail` : `is-${step.outcome}`} key={step.id}>
                <span aria-hidden="true" />
                {step.detail ? (
                  <div>
                    <p>{step.text}</p>
                    {step.detail}
                  </div>
                ) : (
                  <p>{step.text}</p>
                )}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </details>
  );
}
