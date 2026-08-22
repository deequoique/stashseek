import { useEffect, useRef, useState } from "react";

import type { ConversationStreamEvent } from "../api/contracts";

export type ExecutionStepCode = NonNullable<ConversationStreamEvent["step_code"]>;
export type ExecutionStepOutcome = "running" | NonNullable<ConversationStreamEvent["step_outcome"]>;
export type ExecutionPlanItem = NonNullable<ConversationStreamEvent["plan"]>[number];

export interface ExecutionStep {
  stepId: string;
  code: ExecutionStepCode;
  outcome: ExecutionStepOutcome;
  resultCount?: number;
}

interface ExecutionTimelineProps {
  steps: ExecutionStep[];
  plan: ExecutionPlanItem[];
  answerStarted: boolean;
}

const STEP_COPY: Record<ExecutionStepCode, { running: string; completed: string }> = {
  updating_plan: { running: "正在整理处理步骤", completed: "已更新本轮计划" },
  searching_library: { running: "正在搜索资料库", completed: "已完成资料库搜索" },
  reading_context: { running: "正在读取相关上下文", completed: "已读取相关上下文" },
  checking_source: { running: "正在核对来源", completed: "已核对来源" },
  reviewing_library: { running: "正在查看资料库", completed: "已查看资料库" },
  checking_item: { running: "正在查看条目", completed: "已查看条目" },
  handling_save: { running: "正在处理保存请求", completed: "已处理保存请求" },
  managing_library: { running: "正在处理资料库操作", completed: "已处理资料库操作" },
  working: { running: "正在执行一个步骤", completed: "步骤已完成" },
};

const PLAN_STATUS_COPY: Record<ExecutionPlanItem["status"], string> = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
  blocked: "受阻",
};

function completedWithCount(step: ExecutionStep): string {
  if (step.resultCount === undefined) return STEP_COPY[step.code].completed;
  if (step.code === "searching_library") {
    return step.resultCount === 0 ? "未找到匹配片段" : `找到 ${step.resultCount} 个相关片段`;
  }
  if (step.code === "reading_context") return `已读取 ${step.resultCount} 个相关片段`;
  if (step.code === "reviewing_library") return `找到 ${step.resultCount} 个资料库条目`;
  if (["handling_save", "managing_library"].includes(step.code)) {
    return `已处理 ${step.resultCount} 个条目`;
  }
  return STEP_COPY[step.code].completed;
}

export function executionStepCopy(step: ExecutionStep): string {
  if (step.outcome === "running") return STEP_COPY[step.code].running;
  if (step.outcome === "failed") return `${STEP_COPY[step.code].running.replace(/^正在/, "")}未能完成`;
  if (step.outcome === "skipped") return `已跳过：${STEP_COPY[step.code].running.replace(/^正在/, "")}`;
  return completedWithCount(step);
}

export function ExecutionTimeline({ steps, plan, answerStarted }: ExecutionTimelineProps) {
  const [expanded, setExpanded] = useState(true);
  const collapsedForAnswer = useRef(false);

  useEffect(() => {
    if (!answerStarted) {
      collapsedForAnswer.current = false;
      setExpanded(true);
      return;
    }
    if (answerStarted && !collapsedForAnswer.current) {
      collapsedForAnswer.current = true;
      setExpanded(false);
    }
  }, [answerStarted]);

  if (!steps.length && !plan.length) return null;
  const completedSteps = steps.filter((step) => step.outcome !== "running").length;
  const runningStep = steps.find((step) => step.outcome === "running");
  const summary = runningStep
    ? executionStepCopy(runningStep)
    : `执行过程 · ${completedSteps} 个步骤`;

  return (
    <details
      className="chat-execution"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        <span>{summary}</span>
        <small>{expanded ? "收起" : "展开"}</small>
      </summary>
      <div className="chat-execution__body">
        {plan.length ? (
          <section className="chat-execution-plan" aria-label="本轮计划">
            <p>本轮计划</p>
            <ol>
              {plan.map((item) => (
                <li className={`is-${item.status}`} key={item.id}>
                  <span aria-hidden="true" />
                  <strong>{item.title}</strong>
                  <small>{PLAN_STATUS_COPY[item.status]}</small>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
        {steps.length ? (
          <ol className="chat-execution-steps" aria-label="Agent 执行步骤">
            {steps.map((step) => (
              <li className={`is-${step.outcome}`} key={step.stepId}>
                <span aria-hidden="true" />
                <p>{executionStepCopy(step)}</p>
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </details>
  );
}
