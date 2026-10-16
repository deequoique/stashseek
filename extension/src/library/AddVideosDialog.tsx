import { useEffect, useRef, useState } from "react";

import { ApiError, submitVideoBatch } from "../api/client";
import type { BatchSubmitInput, BatchSubmitResponse } from "../api/contracts";
import {
  collectCollectionNames,
  formatWhySaved,
  parseWhySaved,
  validateCollectionName,
  WHY_SAVED_MAX_LENGTH,
} from "./collections";
import { parseVideoInput } from "./videoInput";
import { PartsPicker } from "./PartsPicker";
import { FavoritesPicker } from "./FavoritesPicker";

interface AddVideosDialogProps {
  open: boolean;
  onClose: () => void;
  suggestedCollections?: readonly string[];
  submitBatch?: (input: BatchSubmitInput) => Promise<BatchSubmitResponse>;
  onSubmitted?: (result: BatchSubmitResponse) => void;
  partsEnabled?: boolean;
  favoritesEnabled?: boolean;
  onPartsSubmitted?: () => void;
}

const resultCopy: Record<string, string> = {
  queued: "已添加，等待整理",
  already_exists: "资料库中已有此视频",
  unsupported_url: "暂不支持这个链接或 BV 号",
  invalid_url: "链接或 BV 号格式不正确",
  queue_unavailable: "暂时无法开始整理，请稍后重试",
  create_failed: "添加失败，请稍后重试",
  quota_exceeded: "已达到保存上限",
};

function submitErrorCopy(error: unknown): string {
  if (!(error instanceof ApiError)) return "无法连接资料库，请检查网络后重试。";
  switch (error.code) {
    case "save_disabled":
      return "当前部署暂未开放服务器端添加，请改用浏览器插件或稍后再试。";
    case "quota_exceeded":
      return "当前资料库已达到保存上限，可以先归档旧视频后再试。";
    case "queue_unavailable":
      return "视频已收到，但整理队列暂时不可用，请稍后重新提交。";
    default:
      return error.status >= 500
        ? "资料库服务暂时没有响应，请稍后重试。"
        : "添加没有完成，请检查链接或 BV 号后重试。";
  }
}

export function AddVideosDialog({
  open,
  onClose,
  suggestedCollections = [],
  submitBatch = submitVideoBatch,
  onSubmitted,
  partsEnabled = false,
  favoritesEnabled = false,
  onPartsSubmitted = () => undefined,
}: AddVideosDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [urls, setUrls] = useState<string[]>([]);
  const [urlDraft, setUrlDraft] = useState("");
  const [whySaved, setWhySaved] = useState("");
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [newCollection, setNewCollection] = useState("");
  const [createdCollections, setCreatedCollections] = useState<string[]>([]);
  const [collectionError, setCollectionError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BatchSubmitResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submissionGenerationRef = useRef(0);
  const [mode, setMode] = useState<"direct" | "parts" | "favorites">("direct");
  const [partsInitialUrl, setPartsInitialUrl] = useState("");
  const [partsBusy, setPartsBusy] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      if (!dialog.open) dialog.showModal();
      return;
    }
    if (dialog.open) dialog.close();
    submissionGenerationRef.current += 1;
    setUrls([]);
    setUrlDraft("");
    setWhySaved("");
    setSelectedCollection(null);
    setNewCollection("");
    setCreatedCollections([]);
    setCollectionError(null);
    setError(null);
    setResult(null);
    setSubmitting(false);
    setMode("direct");
    setPartsInitialUrl("");
    setPartsBusy(false);
  }, [open]);

  const collectionOptions = collectCollectionNames([
    ...suggestedCollections.map((name) => `#${name}`),
    ...createdCollections.map((name) => `#${name}`),
  ]);

  function selectCollection(name: string | null) {
    setSelectedCollection(name);
    setCollectionError(null);
  }

  function createCollection() {
    const validationError = validateCollectionName(newCollection);
    if (validationError) {
      setCollectionError(validationError);
      return;
    }
    const formatted = formatWhySaved("", newCollection);
    const name = parseWhySaved(formatted.value).collections[0];
    if (!name) return;
    setCreatedCollections((current) => collectCollectionNames([
      ...current.map((value) => `#${value}`),
      `#${name}`,
    ]));
    setSelectedCollection(name);
    setNewCollection("");
    setCollectionError(null);
  }

  function addUrlTags(value: string) {
    const normalized = parseVideoInput(value);
    if (normalized.length === 0) return;
    setUrls((current) => [...current, ...normalized]);
    setError(null);
  }

  function commitUrlDraft() {
    if (!urlDraft.trim()) return;
    addUrlTags(urlDraft);
    setUrlDraft("");
  }

  function removeUrl(index: number) {
    setUrls((current) => current.filter((_, currentIndex) => currentIndex !== index));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (mode !== "direct" || submitting) return;
    const submittedUrls = [...urls, ...parseVideoInput(urlDraft)];
    if (submittedUrls.length === 0) {
      setError("请至少输入一个 BV 号、YouTube 或 Bilibili 链接。");
      return;
    }
    if (submittedUrls.length > 10) {
      setError("一次最多添加 10 个视频。");
      return;
    }
    const formattedWhySaved = formatWhySaved(whySaved, selectedCollection);
    if (formattedWhySaved.error) {
      setError(formattedWhySaved.error);
      return;
    }
    setError(null);
    setUrls(submittedUrls);
    setUrlDraft("");
    setSubmitting(true);
    const submissionGeneration = ++submissionGenerationRef.current;
    try {
      const nextResult = await submitBatch({ urls: submittedUrls, why_saved: formattedWhySaved.value });
      if (submissionGeneration !== submissionGenerationRef.current) return;
      setResult(nextResult);
      onSubmitted?.(nextResult);
    } catch (submitError) {
      if (submissionGeneration !== submissionGenerationRef.current) return;
      setError(submitErrorCopy(submitError));
    } finally {
      if (submissionGeneration === submissionGenerationRef.current) setSubmitting(false);
    }
  }

  return (
    <dialog
      className="add-dialog"
      ref={dialogRef}
      aria-labelledby="add-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <form className="add-form" onSubmit={handleSubmit}>
        <header className="dialog-header">
          <div>
            <p className="eyebrow">一次最多添加 10 个</p>
            <h2 id="add-dialog-title">添加视频链接</h2>
          </div>
          <button className="icon-button" type="button" aria-label="关闭添加视频窗口" onClick={onClose}>×</button>
        </header>
        {partsEnabled || favoritesEnabled ? <nav className="add-mode-options" aria-label="添加方式">
          <button type="button" className="button button--quiet" aria-pressed={mode === "direct"}
            disabled={submitting || partsBusy} onClick={() => setMode("direct")}>普通视频链接</button>
          {partsEnabled ? <button type="button" className="button button--quiet" aria-pressed={mode === "parts"}
            disabled={submitting || partsBusy} onClick={() => {
              const candidate = [...urls, ...parseVideoInput(urlDraft)].find((value) => /^https:\/\/(?:www\.)?bilibili\.com\/video\//.test(value) && /[?&]p=/.test(value));
              setPartsInitialUrl(candidate ?? ""); setMode("parts");
            }}>选择 B 站分 P</button> : null}
          {favoritesEnabled ? <button type="button" className="button button--quiet" aria-pressed={mode === "favorites"}
            disabled={submitting || partsBusy} onClick={() => setMode("favorites")}>导入公开 B 站收藏夹</button> : null}
        </nav> : null}
        {mode === "parts" && partsEnabled ? <PartsPicker
          whySaved={formatWhySaved(whySaved, selectedCollection).value}
          destinationError={formatWhySaved(whySaved, selectedCollection).error}
          onSubmitted={onPartsSubmitted} onBusy={setPartsBusy}
          initialUrl={partsInitialUrl}
        /> : null}
        {mode === "favorites" && favoritesEnabled ? <FavoritesPicker
          whySaved={formatWhySaved(whySaved, selectedCollection).value}
          destinationError={formatWhySaved(whySaved, selectedCollection).error}
          onSubmitted={onPartsSubmitted} onBusy={setPartsBusy}
        /> : null}
        {mode === "direct" ? <>
        <div className="field url-field">
          <span className="field-label-row">
            <label htmlFor="url-draft">BV 号、YouTube 或 Bilibili 链接，每行一个</label>
            <small className="url-count" data-over-limit={urls.length > 10}>{urls.length} / 10</small>
          </span>
          <p className="field-help" id="url-draft-help">粘贴链接或 BV 号后按 Enter；可直接粘贴 B 站分享文字，也可以混合粘贴多行。</p>
          <p className="field-help">支持 BV 号及 YouTube、Bilibili 普通视频链接。NTULearn 或服务器暂时无法读取的视频，可以在视频页面打开搜藏助手侧边栏收藏，详见<a href="/account/browser-companion">浏览器伴侣</a>。</p>
          <div className="url-token-input">
            {urls.length > 0 ? (
              <ol className="url-tag-list" aria-label="已添加的视频">
                {urls.map((url, index) => (
                  <li className="url-tag" key={`${url}-${index}`}>
                    <span title={url}>{url}</span>
                    <button type="button" aria-label={`移除视频 ${index + 1}`} onClick={() => removeUrl(index)}>×</button>
                  </li>
                ))}
              </ol>
            ) : null}
            <textarea
              id="url-draft"
              className="url-draft-input"
              name="urls"
              aria-describedby="url-draft-help"
              autoComplete="off"
              spellCheck={false}
              rows={1}
              value={urlDraft}
              placeholder={urls.length === 0 ? "输入 BV 号或粘贴 YouTube、Bilibili 链接" : "继续添加链接或 BV 号"}
              onChange={(event) => setUrlDraft(event.target.value)}
              onPaste={(event) => {
                const pasted = event.clipboardData.getData("text");
                if (!pasted) return;
                event.preventDefault();
                const input = event.currentTarget;
                const merged = urlDraft.slice(0, input.selectionStart)
                  + pasted + urlDraft.slice(input.selectionEnd);
                addUrlTags(merged);
                setUrlDraft("");
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                commitUrlDraft();
              }}
            />
          </div>
        </div>
        </> : null}
        <fieldset className="collection-picker" disabled={partsBusy}>
          <legend>保存到收藏夹（可选）</legend>
          <p className="field-help">选择已有收藏夹，或新建一个标签。未选择时保存为未归类。</p>
          <div className="collection-options" aria-label="选择收藏夹">
            <button
              className="collection-chip"
              type="button"
              aria-pressed={selectedCollection === null}
              onClick={() => selectCollection(null)}
            >
              未归类
            </button>
            {collectionOptions.map((name) => (
              <button
                className="collection-chip"
                type="button"
                aria-pressed={selectedCollection?.toLocaleLowerCase("en") === name.toLocaleLowerCase("en")}
                key={name}
                onClick={() => selectCollection(name)}
              >
                {name}
              </button>
            ))}
          </div>
          <div className="collection-create">
            <label className="field">
              <span>新收藏夹名称</span>
              <input
                name="new-collection"
                autoComplete="off"
                value={newCollection}
                maxLength={21}
                placeholder="例如：产品调研"
                onChange={(event) => setNewCollection(event.target.value)}
              />
            </label>
            <button className="button button--quiet" type="button" onClick={createCollection}>
              创建并选择
            </button>
          </div>
          {collectionError ? <p className="inline-error" role="alert">{collectionError}</p> : null}
        </fieldset>
        <div className="field">
          <span className="field-label-row">
            <label htmlFor="why-saved">备注（可选）</label>
            <small>{whySaved.length} / {WHY_SAVED_MAX_LENGTH}</small>
          </span>
          <textarea
            id="why-saved"
            className="why-saved-textarea"
            name="why-saved"
            autoComplete="off"
            value={whySaved}
            disabled={partsBusy}
            rows={3}
            maxLength={WHY_SAVED_MAX_LENGTH}
            placeholder="例如：用于周末精读或项目调研"
            onChange={(event) => setWhySaved(event.target.value)}
          />
        </div>
        {mode === "direct" && error ? <p className="inline-error" role="alert">{error}</p> : null}
        {mode === "direct" && result ? (
          <ol className="submission-results" aria-label="添加结果" aria-live="polite">
            {result.results.map((item) => (
              <li key={`${item.input_index}-${item.status}`} data-status={item.status}>
                <span>第 {item.input_index + 1} 个视频</span>
                <strong>{resultCopy[item.status] ?? "未能确认处理结果，请稍后查看资料库"}</strong>
              </li>
            ))}
          </ol>
        ) : null}
        {mode === "direct" ? <button className="button button--primary button--wide" disabled={submitting} type="submit">
          {submitting ? "正在添加…" : "添加并整理"}
        </button> : null}
      </form>
    </dialog>
  );
}
