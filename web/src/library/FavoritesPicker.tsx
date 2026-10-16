import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, previewBilibiliFavorites, previewBilibiliParts } from "../api/client";
import type {
  BilibiliFavoriteVideo, BilibiliFavoritesPreview, BilibiliFavoritesPreviewInput,
  BilibiliPartsPreview, BilibiliPartsPreviewInput, BilibiliPartsBatchInput, BilibiliPartsBatchResponse,
} from "../api/contracts";
import { PartImportControls, PartRows, PartSelectionToolbar, partsPreviewError, type SelectedPart } from "./PartsPicker";
import { addLoadedParts, refreshSelectedParts } from "./partSelection";

type PreviewParts = (input: BilibiliPartsPreviewInput, signal?: AbortSignal) => Promise<BilibiliPartsPreview>;
type LoadedPage = { parent: string; parts: BilibiliPartsPreview["parts"] };

function favoritesError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "bilibili_limit_exceeded") return "收藏夹返回的数据超过预览范围，请稍后再试。";
    if (error.status === 429) return "预览请求过于频繁，请稍后再试。";
    if (error.code === "bilibili_invalid_favorites_url" || error.code === "bilibili_invalid_url" || error.status === 422) return "请输入公开 B 站收藏夹链接（favlist?fid=… 或 medialist/detail/ml…）。";
    if (error.code === "bilibili_favorites_private") return "这个收藏夹需要 B 站账号访问；当前只支持公开收藏夹。";
    if (error.code === "bilibili_unavailable") return "无法读取这个公开收藏夹，它可能私密、已删除或暂时不可访问。";
  }
  return "收藏夹暂时无法读取，请稍后重新预览。";
}

function ExpandedParts({ video, selected, onChange, onLoaded, onRefresh, disabled, previewParts }: {
  video: BilibiliFavoriteVideo;
  selected: SelectedPart[];
  onChange: (selection: SelectedPart[]) => void;
  onLoaded: (key: string, page: LoadedPage | null) => void;
  onRefresh: (parts: SelectedPart[]) => void;
  disabled: boolean;
  previewParts: PreviewParts;
}) {
  const [page, setPage] = useState(1);
  const mounted = useRef(true);
  const title = video.title ?? video.bvid ?? "未命名视频";
  const parts = useQuery({
    queryKey: ["bilibili-favorites-parts", video.bvid, page],
    queryFn: ({ signal }) => previewParts({ url: video.canonical_url!, page }, signal),
    retry: false, gcTime: 0,
  });
  useEffect(() => {
    onLoaded(video.entry_key, parts.data && !parts.isError && !parts.isFetching ? { parent: title, parts: parts.data.parts } : null);
    return () => onLoaded(video.entry_key, null);
  }, [onLoaded, video.entry_key, title, parts.data, parts.isError, parts.isFetching]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function refreshRefs() {
    const result = await parts.refetch();
    if (!mounted.current || result.isError || !result.data) return;
    onRefresh(result.data.parts.map((part) => ({ part, parentTitle: title, allowLegacyOverlap: false })));
  }
  return <div className="favorite-parts" aria-label={`${title}的展开内容`}>
    {parts.isFetching ? <p role="status">正在读取 {title} 的分 P…</p> : null}
    {parts.isError ? <p role="alert" className="inline-error">{partsPreviewError(parts.error)}</p> : null}
    {parts.data && !parts.isError ? <>
      <p>{parts.data.total_parts} 个分 P · 分 P 第 {parts.data.page} 页 / {Math.max(1, Math.ceil(parts.data.total_parts / 20))} 页</p>
      <PartRows parts={parts.data.parts} parentTitle={title} selected={selected} onChange={onChange} disabled={disabled || parts.isFetching} />
      {parts.data.parts.length === 0 ? <p>没有可显示的分 P。</p> : null}
      <nav className="pagination" aria-label={`${title}的分 P 分页`}>
        <button type="button" className="button button--quiet" disabled={disabled || parts.isFetching || page <= 1} onClick={() => setPage((value) => value - 1)}>分 P 上一页</button>
        <button type="button" className="button button--quiet" disabled={disabled || parts.isFetching || !parts.data.has_more} onClick={() => setPage((value) => value + 1)}>分 P 下一页</button>
      </nav>
    </> : null}
    <button type="button" className="text-button" disabled={disabled || parts.isFetching} onClick={() => void refreshRefs()}>刷新该视频当前页的分 P 凭据</button>
  </div>;
}

export function FavoritesPicker({ whySaved, destinationError, onSubmitted, onBusy,
  previewFavorites = previewBilibiliFavorites, previewParts = previewBilibiliParts, submitParts }: {
  whySaved: string | null;
  destinationError?: string | null;
  onSubmitted: () => void;
  onBusy?: (busy: boolean) => void;
  previewFavorites?: (input: BilibiliFavoritesPreviewInput, signal?: AbortSignal) => Promise<BilibiliFavoritesPreview>;
  previewParts?: PreviewParts;
  submitParts?: (input: BilibiliPartsBatchInput, key: string, signal?: AbortSignal) => Promise<BilibiliPartsBatchResponse>;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [url, setUrl] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<SelectedPart[]>([]);
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [loaded, setLoaded] = useState<Map<string, LoadedPage>>(new Map());
  const [locked, setLocked] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const confirmation = useRef<HTMLDivElement>(null);
  const preview = useQuery({
    queryKey: ["bilibili-favorites-preview", url, page],
    queryFn: ({ signal }) => previewFavorites({ url, page }, signal),
    enabled: Boolean(url), retry: false, gcTime: 0,
  });
  useEffect(() => () => {
    for (const resource of ["bilibili-favorites-preview", "bilibili-favorites-parts"]) {
      void queryClient.cancelQueries({ queryKey: [resource] });
      queryClient.removeQueries({ queryKey: [resource] });
    }
  }, [queryClient]);
  const publishLoaded = useCallback((key: string, value: LoadedPage | null) => {
    setLoaded((current) => {
      if (current.get(key) === value || (!value && !current.has(key))) return current;
      const next = new Map(current);
      if (value) next.set(key, value); else next.delete(key);
      return next;
    });
  }, []);
  function changeSelection(values: SelectedPart[]) { setSelectionError(null); setSelected(values); }
  function changeLock(value: boolean) { setLocked(value); onBusy?.(value); }
  function refreshSelected(values: SelectedPart[]) { setSelected((current) => refreshSelectedParts(current, values)); }

  function readFavorites() {
    if (locked || !draft.trim()) return;
    void queryClient.cancelQueries({ queryKey: ["bilibili-favorites-preview", url] });
    void queryClient.cancelQueries({ queryKey: ["bilibili-favorites-parts"] });
    setSelected([]); setOpened(new Set()); setLoaded(new Map()); setSelectionError(null);
    setRevision((value) => value + 1); setPage(1);
    if (draft.trim() === url && page === 1) void preview.refetch(); else setUrl(draft.trim());
  }
  function navigatePage(next: number) {
    setOpened(new Set()); setLoaded(new Map()); setSelectionError(null); setPage(next);
  }
  const visibleLoaded = (preview.data?.items ?? []).flatMap((video) => {
    const data = loaded.get(video.entry_key);
    return opened.has(video.entry_key) && data ? data.parts.map((part) => ({ part, parentTitle: data.parent, allowLegacyOverlap: false })) : [];
  });
  const eligibleLoaded = visibleLoaded.filter((value) => value.part.selectable && value.part.part_ref);
  function selectDisplayed() {
    const next = addLoadedParts(selected, eligibleLoaded);
    if (next === null) setSelectionError("本页已显示分 P 加入后会超过 10 项，原选择已保留，请减少选择。");
    else changeSelection(next);
  }
  return <section className="favorites-picker" aria-label="导入公开 B 站收藏夹">
    <PartSelectionToolbar count={selected.length} onConfirm={() => {
      confirmation.current?.scrollIntoView({ block: "start", behavior: "instant" });
      confirmation.current?.focus({ preventScroll: true });
    }} />
    <label className="field"><span>公开 B 站收藏夹链接</span>
      <input value={draft} maxLength={2048} autoComplete="off" disabled={locked}
        placeholder="https://space.bilibili.com/…/favlist?fid=…" onChange={(event) => setDraft(event.target.value)} />
    </label>
    <button type="button" className="button button--quiet" disabled={locked || !draft.trim()} onClick={readFavorites}>预览收藏夹（清空原选择）</button>
    <p className="field-help">仅支持公开收藏夹。先展开视频再选择分 P；预览不会保存或整理视频，可读取列表不代表有可用字幕。</p>
    {preview.isFetching ? <p role="status">正在读取收藏夹…</p> : null}
    {preview.isError ? <p role="alert" className="inline-error">{favoritesError(preview.error)}</p> : null}
    {preview.data && !preview.isError ? <>
      <h3>{preview.data.title ?? "未命名收藏夹"}</h3>
      <p>收藏夹报告 {preview.data.reported_total} 个条目 · 当前第 {preview.data.page} 页（每页最多 20 条）</p>
      <p className="field-help">远端列表可能变化，已选分 P 会跨页保留；未展开的分 P 不会加入选择。</p>
      {preview.data.capped ? <p role="status">已到第 500 页预览上限，剩余条目尚未读取。</p> : null}
      {preview.data.items.length === 0 ? <p>这个公开收藏夹当前没有条目。</p> : null}
      <ul className="favorite-video-list" aria-label="收藏夹视频">
        {preview.data.items.map((video) => <li key={video.entry_key}>
          {video.available && video.bvid && video.canonical_url ? <details open={opened.has(video.entry_key)}
            onToggle={(event) => {
              const open = event.currentTarget.open;
              setOpened((current) => { const next = new Set(current); if (open) next.add(video.entry_key); else next.delete(video.entry_key); return next; });
            }}>
            <summary>{video.title ?? video.bvid} <small>{video.bvid} · 展开分 P</small></summary>
            {opened.has(video.entry_key) ? <ExpandedParts key={`${revision}:${video.entry_key}`} video={video} selected={selected}
              onChange={changeSelection} onLoaded={publishLoaded} onRefresh={refreshSelected} disabled={locked} previewParts={previewParts} /> : null}
          </details> : <div><strong>{video.title ?? "不可用条目"}</strong><p className="field-help">这个条目已失效、不是视频或无法确认视频信息，不能导入。</p></div>}
        </li>)}
      </ul>
      <div className="add-mode-options">
        <button type="button" className="button button--quiet" disabled={locked || eligibleLoaded.length === 0} onClick={selectDisplayed}>选择本页已显示分 P</button>
        <button type="button" className="button button--quiet" disabled={locked || eligibleLoaded.length === 0} onClick={() => {
          const keys = new Set(visibleLoaded.map((value) => value.part.part_key));
          changeSelection(selected.filter((value) => !keys.has(value.part.part_key)));
        }}>取消本页已显示分 P 选择</button>
      </div>
      <nav className="pagination" aria-label="收藏夹分页">
        <button type="button" className="button button--quiet" disabled={locked || preview.isFetching || preview.data.page <= 1} onClick={() => navigatePage(preview.data!.page - 1)}>收藏夹上一页</button>
        <button type="button" className="button button--quiet" disabled={locked || preview.isFetching || !preview.data.has_more || preview.data.capped} onClick={() => navigatePage(preview.data!.page + 1)}>收藏夹下一页</button>
      </nav>
    </> : null}
    {selectionError ? <p className="inline-error" role="alert">{selectionError}</p> : null}
    <div ref={confirmation} className="parts-confirmation-target" tabIndex={-1} role="region" aria-label="分 P 选择与确认">
    <PartImportControls key={revision} selected={selected} onChange={changeSelection} whySaved={whySaved} showCount={false}
      destinationError={destinationError} onSubmitted={onSubmitted} onBusy={changeLock} submitParts={submitParts} />
    </div>
  </section>;
}
