import { useId } from "react";

import type { IngestReason } from "../api/contracts";
import {
  ACTION_LABEL,
  LOGIN_REQUIRED_EXPLANATION,
  RETRY_NOT_USEFUL_HINT,
  reasonStatusLine,
  UNFINISHED_EXPLANATION,
  UNFINISHED_TITLE,
  UNSEARCHABLE_BADGE,
  UNSEARCHABLE_BADGE_DETAIL,
} from "./ingestCopy";

/** The main action of the unfinished screen. */
export type IngestPrimaryAction =
  | { kind: "button"; label: string; onClick: () => void; pending?: boolean }
  | { kind: "link"; label: string; href: string };

interface IngestUnfinishedProps {
  /** The video title, when the server has one yet. */
  title: string | null;
  /** The platform's display name, such as `YouTube`; empty when unknown. */
  platformName: string;
  reason: IngestReason;
  /** 「重新整理」, 「重新收藏」 or 「打开视频页」; none when the item cannot be re-run from here. */
  primary?: IngestPrimaryAction | null;
  /** 「仅查标题 / 备注」; the button is left out when the caller cannot search. */
  onSearchTitles?: () => void;
  /** Why the last action did not go through. */
  error?: string | null;
}

/** The screen of an item whose body is not searchable: why, what still works, and what to do. */
export function IngestUnfinished({ title, platformName, reason, primary = null, onSearchTitles, error = null }: IngestUnfinishedProps) {
  const titleId = useId();
  const loginRequired = reason.category === "login_required";
  return (
    <section className="ingest-unfinished" aria-labelledby={titleId}>
      <h2 id={titleId}>{UNFINISHED_TITLE}</h2>
      <div className="ingest-card">
        <p className="ingest-card__title">{title?.trim() || "视频信息尚未准备好"}</p>
        <p className="ingest-muted">{platformName ? `${platformName} · 链接已保存` : "链接已保存"}</p>
      </div>
      <p className="ingest-status ingest-status--error" role="status">{reasonStatusLine(reason)}</p>
      <p className="ingest-muted">{loginRequired ? LOGIN_REQUIRED_EXPLANATION : UNFINISHED_EXPLANATION}</p>
      {!loginRequired && !reason.retry_useful ? <p className="ingest-muted">{RETRY_NOT_USEFUL_HINT}</p> : null}
      {error ? <p className="inline-error ingest-error" role="alert">{error}</p> : null}
      {primary || onSearchTitles ? (
        <div className="ingest-actions">
          {primary?.kind === "button" ? (
            <button className="button button--primary" type="button" disabled={primary.pending === true} aria-busy={primary.pending === true} onClick={primary.onClick}>
              {primary.label}
            </button>
          ) : null}
          {primary?.kind === "link" ? (
            <a className="button button--primary" href={primary.href} target="_blank" rel="noreferrer">{primary.label}</a>
          ) : null}
          {onSearchTitles ? (
            <button className="button button--quiet" type="button" onClick={onSearchTitles}>{ACTION_LABEL.searchTitles}</button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** Marks an item whose body cannot be found by content search; its title and note still can. */
export function UnsearchableBadge({ detailed = false }: { detailed?: boolean }) {
  return <span className="ingest-badge">{detailed ? UNSEARCHABLE_BADGE_DETAIL : UNSEARCHABLE_BADGE}</span>;
}
