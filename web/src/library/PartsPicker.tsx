import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ApiError, previewBilibiliParts, submitBilibiliParts } from "../api/client";
import type {
  BilibiliPartPreview, BilibiliPartsPreview, BilibiliPartsPreviewInput,
  BilibiliPartsBatchInput, BilibiliPartsBatchResponse,
} from "../api/contracts";

export interface SelectedPart {
  part: BilibiliPartPreview;
  parentTitle: string;
  allowLegacyOverlap: boolean;
}

export function partLabel(value: SelectedPart): string {
  return `${value.parentTitle} · P${value.part.page} ${value.part.title ?? "未命名分 P"}`;
}

export function PartSelectionToolbar({ count, onConfirm }: { count: number; onConfirm: () => void }) {
  return <div className="part-selection-toolbar" aria-label="分 P 选择状态">
    <p className="parts-selection-count" aria-live="polite">已选 {count} / 10 个分 P</p>
    <button type="button" className="button button--quiet" disabled={count === 0} onClick={onConfirm}>查看选择与确认</button>
  </div>;
}

/** Shared controlled rows: expanding a video never implicitly selects a part. */
export function PartRows({ parts, parentTitle, selected, onChange, disabled = false }: {
  parts: readonly BilibiliPartPreview[];
  parentTitle: string;
  selected: readonly SelectedPart[];
  onChange: (selection: SelectedPart[]) => void;
  disabled?: boolean;
}) {
  return <ol className="part-list" aria-label={`${parentTitle}的分 P`}>
    {parts.map((part) => {
      const chosen = selected.find((value) => value.part.part_key === part.part_key);
      const label = partLabel({ part, parentTitle, allowLegacyOverlap: false });
      return <li key={part.part_key}>
        <label className="part-choice">
          <input type="checkbox" checked={Boolean(chosen)}
            disabled={disabled || !part.selectable || !part.part_ref || (!chosen && selected.length >= 10)}
            onChange={() => onChange(chosen
              ? selected.filter((value) => value.part.part_key !== part.part_key)
              : [...selected, { part: { ...part }, parentTitle, allowLegacyOverlap: false }])} />
          <span>{label}{part.duration_sec != null ? <small> · {part.duration_sec} 秒</small> : null}</span>
        </label>
        {!part.selectable ? <p className="field-help">这个分 P 暂时无法选择，请重新预览或稍后再试。</p> : null}
        {part.has_unverified_legacy_item ? <div className="part-legacy-warning">
          <p>资料库中有这个视频的旧记录，但无法确认它对应哪个分 P。此次会另存一条记录，旧记录和历史保留。</p>
          {chosen ? <label className="part-choice">
            <input type="checkbox" checked={chosen.allowLegacyOverlap} disabled={disabled}
              onChange={(event) => onChange(selected.map((value) => value.part.part_key === part.part_key
                ? { ...value, allowLegacyOverlap: event.target.checked } : value))} />
            <span>我了解并同意将 {label} 单独保存</span>
          </label> : null}
        </div> : null}
      </li>;
    })}
  </ol>;
}

const resultCopy: Record<string, string> = {
  queued: "已添加，等待整理", already_exists: "资料库中已有这个分 P",
  part_ref_invalid: "选择凭据已失效，请重新预览", part_ref_expired: "预览已过期，请重新预览",
  legacy_identity_unknown: "旧记录身份尚未确认，请重新预览并明确选择",
  quota_exceeded: "当前添加额度不足", queue_unavailable: "整理队列暂不可用，请查看资料库状态",
  create_failed: "添加没有完成，请稍后再试",
};

export function partsPreviewError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "bilibili_limit_exceeded") return "这个视频超过当前预览范围，无法完整读取分 P。";
    if (error.code === "bilibili_invalid_url" || error.code === "invalid_url" || error.code === "unsupported_url" || error.status === 422) return "请输入完整 BV 号或 B 站视频链接，可包含 ?p=2。";
    if (error.status === 429) return "预览请求过于频繁，请稍后再试。";
    if (error.code === "source_unavailable" || error.code === "bilibili_unavailable") return "无法读取公开分 P 列表，视频可能不可访问。";
  }
  return "分 P 列表暂时无法读取，请稍后重新预览。";
}

interface Attempt {
  key: string;
  input: BilibiliPartsBatchInput;
  selection: SelectedPart[];
}

export function PartImportControls({ selected, onChange, whySaved, destinationError, onSubmitted, onBusy, showCount = true,
  submitParts = submitBilibiliParts }: {
  selected: SelectedPart[];
  onChange: (selection: SelectedPart[]) => void;
  whySaved: string | null;
  destinationError?: string | null;
  onSubmitted: () => void;
  onBusy?: (busy: boolean) => void;
  showCount?: boolean;
  submitParts?: (input: BilibiliPartsBatchInput, operationKey: string, signal?: AbortSignal) => Promise<BilibiliPartsBatchResponse>;
}) {
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [result, setResult] = useState<{ selection: SelectedPart[]; response: BilibiliPartsBatchResponse } | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const running = useRef(false);
  const generation = useRef(0);
  const [now, setNow] = useState(Date.now());
  const expiresAt = Math.min(...(attempt?.selection ?? selected).map((value) => value.part.expires_at == null ? Infinity : value.part.expires_at * 1000));
  const expired = expiresAt <= now;
  useEffect(() => {
    if (!Number.isFinite(expiresAt)) return;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, expiresAt - Date.now() + 50));
    return () => window.clearTimeout(timer);
  }, [expiresAt]);
  useEffect(() => () => {
    generation.current += 1;
    controller.current?.abort();
  }, []);

  async function submit(frozen?: Attempt) {
    if (running.current || expiresAt <= Date.now() || (!frozen && (selected.length === 0 || selected.length > 10 || destinationError))) return;
    if (!frozen && selected.some((value) => !value.part.part_ref || (value.part.has_unverified_legacy_item && !value.allowLegacyOverlap))) return;
    const next = frozen ?? {
      key: crypto.randomUUID(), selection: selected.map((value) => ({ ...value, part: { ...value.part } })),
      input: { selections: selected.map((value) => ({ part_ref: value.part.part_ref!, allow_legacy_overlap: value.allowLegacyOverlap })), why_saved: whySaved },
    };
    running.current = true;
    const currentGeneration = generation.current;
    const abort = new AbortController(); controller.current = abort;
    setAttempt(next); setBusy(true); setUncertain(false); onBusy?.(true);
    try {
      const response = await submitParts(next.input, next.key, abort.signal);
      if (generation.current !== currentGeneration || abort.signal.aborted) return;
      setResult({ selection: next.selection, response });
      const accepted = new Set(response.results.filter((value) => ["queued", "already_exists"].includes(value.status))
        .map((value) => next.selection[value.input_index]?.part.part_key));
      onChange(selected.filter((value) => !accepted.has(value.part.part_key)));
      setAttempt(null); onSubmitted();
    } catch {
      if (generation.current !== currentGeneration || abort.signal.aborted) return;
      setUncertain(true);
    } finally {
      if (generation.current === currentGeneration) { running.current = false; setBusy(false); }
    }
  }

  const locked = busy || uncertain;
  useEffect(() => { onBusy?.(locked); }, [locked, onBusy]);
  return <section aria-label="确认已选分 P">
    {showCount ? <p className="parts-selection-count" aria-live="polite">已选 {selected.length} / 10 个分 P</p> : null}
    {selected.length > 0 ? <ol className="parts-selection-summary" aria-label="已选分 P">
      {selected.map((value) => <li key={value.part.part_key}><div>{partLabel(value)}
        {value.part.has_unverified_legacy_item ? <div className="part-legacy-warning">
          <p>已有旧 P1 记录，无法确认对应分 P；本次会新增独立分 P 记录，保留旧记录和历史。</p>
          <label className="part-choice"><input type="checkbox" checked={value.allowLegacyOverlap} disabled={locked}
            onChange={(event) => onChange(selected.map((part) => part.part.part_key === value.part.part_key
              ? { ...part, allowLegacyOverlap: event.target.checked } : part))} />
            <span>已选项目确认：将 {partLabel(value)} 单独保存</span>
          </label>
        </div> : null}
        </div>
        <button type="button" className="text-button" disabled={locked} onClick={() => onChange(selected.filter((part) => part.part.part_key !== value.part.part_key))}>跳过</button>
      </li>)}
    </ol> : null}
    {selected.some((value) => value.part.has_unverified_legacy_item && !value.allowLegacyOverlap)
      ? <p role="alert" className="inline-error">请逐项确认旧记录重叠提示，或跳过这些分 P。</p> : null}
    {destinationError ? <p role="alert" className="inline-error">{destinationError}</p> : null}
    {expired ? <p role="alert" className="inline-error">部分选择已过期，请明确刷新对应视频的分 P 凭据后再确认。若上次结果未知，请先查看资料库。</p> : null}
    {uncertain && attempt ? <div role="alert" className="inline-error">
      <p>未能确认本次结果。部分分 P 可能已保存，可查看资料库；重新确认会沿用本次相同选择和请求。</p>
      <p>可关闭此窗口返回资料库核对；关闭不会撤销已接收的任务。</p>
      <button type="button" className="button button--quiet" disabled={busy || expired} onClick={() => void submit(attempt)}>重新确认本次结果</button>
    </div> : null}
    {result ? <ol className="submission-results" aria-label="分 P 添加结果" aria-live="polite">
      {result.response.results.map((value) => <li key={value.result_id}>
        <span>{result.selection[value.input_index] ? partLabel(result.selection[value.input_index]) : `第 ${value.input_index + 1} 项`}</span>
        <strong>{resultCopy[value.status] ?? "未能确认处理结果，请查看资料库"}</strong>
      </li>)}
    </ol> : null}
    <button type="button" className="button button--primary button--wide"
      disabled={locked || expired || selected.length === 0 || selected.length > 10 || Boolean(destinationError) || selected.some((value) => value.part.has_unverified_legacy_item && !value.allowLegacyOverlap)}
      onClick={() => void submit()}>{busy ? "正在添加…" : `添加已选 ${selected.length} 个分 P`}</button>
    <p className="field-help">关闭窗口会清空选择；已收到的添加任务会继续处理。</p>
  </section>;
}

export function PartsPicker({ whySaved, destinationError, onSubmitted, onBusy, initialUrl = "",
  previewParts = previewBilibiliParts, submitParts = submitBilibiliParts }: {
  whySaved: string | null;
  destinationError?: string | null;
  onSubmitted: () => void;
  onBusy?: (busy: boolean) => void;
  initialUrl?: string;
  previewParts?: (input: BilibiliPartsPreviewInput, signal?: AbortSignal) => Promise<BilibiliPartsPreview>;
  submitParts?: (input: BilibiliPartsBatchInput, operationKey: string, signal?: AbortSignal) => Promise<BilibiliPartsBatchResponse>;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(initialUrl);
  const [url, setUrl] = useState("");
  const [page, setPage] = useState<number | undefined>();
  const [selected, setSelected] = useState<SelectedPart[]>([]);
  const [locked, setLocked] = useState(false);
  const [revision, setRevision] = useState(0);
  const confirmation = useRef<HTMLDivElement>(null);
  const preview = useQuery({
    queryKey: ["bilibili-parts-preview", url, page],
    queryFn: ({ signal }) => previewParts({ url, ...(page === undefined ? {} : { page }) }, signal),
    enabled: Boolean(url), retry: false, gcTime: 0,
  });
  useEffect(() => () => {
    void queryClient.cancelQueries({ queryKey: ["bilibili-parts-preview"] });
    queryClient.removeQueries({ queryKey: ["bilibili-parts-preview"] });
  }, [queryClient]);
  function loadPreview() {
    if (locked) return;
    setSelected([]); setRevision((value) => value + 1); setPage(undefined);
    const next = draft.trim();
    if (next === url && page === undefined) void preview.refetch();
    else setUrl(next);
  }
  function changeLock(value: boolean) { setLocked(value); onBusy?.(value); }
  return <section className="parts-picker" aria-label="选择 B 站分 P">
    <PartSelectionToolbar count={selected.length} onConfirm={() => {
      confirmation.current?.scrollIntoView({ block: "start", behavior: "instant" });
      confirmation.current?.focus({ preventScroll: true });
    }} />
    <label className="field"><span>B 站视频 BV 号或链接</span>
      <input value={draft} maxLength={2048} autoComplete="off" disabled={locked}
        placeholder="BV1xx411c7mD 或视频链接 ?p=2" onChange={(event) => setDraft(event.target.value)} />
    </label>
    <button type="button" className="button button--quiet" disabled={locked || !draft.trim()} onClick={loadPreview}>预览分 P（清空原选择）</button>
    <p className="field-help">预览不会保存视频。展开后勾选需要的分 P，每个分 P 独立整理；可读取列表不代表一定有字幕。</p>
    {preview.isFetching ? <p role="status">正在读取分 P…</p> : null}
    {preview.isError ? <p role="alert" className="inline-error">{partsPreviewError(preview.error)}</p> : null}
    {preview.data && !preview.isError ? <>
      <h3>{preview.data.title ?? preview.data.bvid}</h3>
      <p>{preview.data.total_parts} 个分 P · 第 {preview.data.page} 页{preview.data.requested_page ? ` · 链接指定 P${preview.data.requested_page}，请勾选确认` : ""}</p>
      <PartRows parts={preview.data.parts} parentTitle={preview.data.title ?? preview.data.bvid}
        selected={selected} onChange={setSelected} disabled={locked} />
      {preview.data.parts.length === 0 ? <p>没有可显示的分 P。</p> : null}
      <nav className="pagination" aria-label="分 P 分页">
        <button type="button" className="button button--quiet" disabled={locked || preview.isFetching || preview.data.page <= 1}
          onClick={() => setPage(preview.data!.page - 1)}>上一页</button>
        <button type="button" className="button button--quiet" disabled={locked || preview.isFetching || !preview.data.has_more}
          onClick={() => setPage(preview.data!.page + 1)}>下一页</button>
      </nav>
    </> : null}
    <div ref={confirmation} className="parts-confirmation-target" tabIndex={-1} role="region" aria-label="分 P 选择与确认">
    <PartImportControls key={revision} selected={selected} onChange={setSelected} showCount={false}
      whySaved={whySaved} destinationError={destinationError} onSubmitted={onSubmitted}
      onBusy={changeLock} submitParts={submitParts} />
    </div>
  </section>;
}
