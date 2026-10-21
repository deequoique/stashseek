import { useId } from "react";

import type { ConversationCitation } from "../api/contracts";

/**
 * Inline citation chips.
 *
 * The server rewrites each in-text citation marker into `[^n]`, where `n` is
 * the 1-based position of the citation in the answer's public `citations`
 * list. `remarkCitationChips` turns those markers into marked links and
 * `MarkdownAnswer` renders them as `CitationChip`. Browsers never see internal
 * segment IDs, and a `[^n]` that does not resolve to a citation stays text.
 */

const EXCERPT_CHARS = 120;
const MARKER = /\[\^(\d+)\]/g;
const CODE_SPAN = /```[\s\S]*?```|`[^`\n]*`/g;
// Never rewrite markers inside code or inside a model-authored link label.
const SKIPPED_NODE_TYPES = new Set(["code", "inlineCode", "link", "linkReference"]);

// Minimal structural view of the mdast nodes this plugin touches. The mdast
// typings are not a direct dependency, so the plugin stays structural.
interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
  data?: { hProperties?: Record<string, string | number> };
}

function startTime(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function citationPosition(citation: ConversationCitation): string {
  return startTime(citation.start_sec) ?? "标题与简介";
}

function excerptPreview(excerpt: string): string {
  const chars = Array.from(excerpt.replace(/\s+/g, " ").trim());
  return chars.length > EXCERPT_CHARS ? `${chars.slice(0, EXCERPT_CHARS).join("")}…` : chars.join("");
}

function splitMarkers(value: string): MdNode[] | null {
  const parts: MdNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(MARKER)) {
    const index = match.index ?? 0;
    if (index > cursor) parts.push({ type: "text", value: value.slice(cursor, index) });
    const ordinal = match[1] as string;
    parts.push({
      type: "link",
      url: `#cite-${ordinal}`,
      data: { hProperties: { "data-cite": ordinal } },
      children: [{ type: "text", value: ordinal }],
    });
    cursor = index + match[0].length;
  }
  if (cursor === 0) return null;
  if (cursor < value.length) parts.push({ type: "text", value: value.slice(cursor) });
  return parts;
}

function rewriteMarkers(node: MdNode): void {
  if (!node.children || SKIPPED_NODE_TYPES.has(node.type)) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    const replaced = child.type === "text" && typeof child.value === "string"
      ? splitMarkers(child.value)
      : null;
    if (replaced) {
      next.push(...replaced);
    } else {
      rewriteMarkers(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function remarkCitationChips() {
  return (tree: object): void => {
    rewriteMarkers(tree as MdNode);
  };
}

/** Ordinals that the answer text itself references (code spans excluded). */
function referencedOrdinals(text: string): Set<number> {
  const found = new Set<number>();
  for (const match of text.replace(CODE_SPAN, "").matchAll(MARKER)) {
    found.add(Number(match[1]));
  }
  return found;
}

export function CitationChip({
  ordinal,
  citation,
  hidden = false,
}: {
  ordinal: number;
  citation: ConversationCitation;
  /** Collapsed: still in the text (so a copy keeps "[n]") but out of the AT tree and tab order. */
  hidden?: boolean;
}) {
  const tipId = useId();
  const position = citationPosition(citation);
  const excerpt = excerptPreview(citation.excerpt);
  return (
    <span className="chat-cite-wrap">
      <a
        className="chat-cite"
        href={citation.url}
        target="_blank"
        rel="noreferrer"
        aria-label={`引用 ${ordinal}：《${citation.title}》 ${position}`}
        aria-describedby={hidden ? undefined : tipId}
        aria-hidden={hidden ? "true" : undefined}
        tabIndex={hidden ? -1 : undefined}
      >
        {/* Zero-size brackets keep "[n]" in copied text without widening the chip. */}
        <span className="chat-cite__mark" aria-hidden="true">[</span>
        {ordinal}
        <span className="chat-cite__mark" aria-hidden="true">]</span>
      </a>
      <span role="tooltip" id={tipId} className="chat-cite__tip">
        <strong>{citation.title}</strong>
        {` · ${position}`}
        {excerpt ? <span className="chat-cite__excerpt">{excerpt}</span> : null}
      </span>
    </span>
  );
}

/** Citations the answer text never marks, so removing the list hides none. */
export function UnreferencedChips({
  citations,
  text,
  hidden = false,
}: {
  citations: ConversationCitation[];
  text: string;
  hidden?: boolean;
}) {
  const referenced = referencedOrdinals(text);
  const unreferenced = citations
    .map((citation, index) => ({ citation, ordinal: index + 1 }))
    .filter(({ ordinal }) => !referenced.has(ordinal));
  if (!unreferenced.length) return null;
  return (
    <p className="chat-cite-more" aria-hidden={hidden ? "true" : undefined}>
      <span>另见</span>
      {unreferenced.map(({ citation, ordinal }) => (
        <CitationChip key={ordinal} ordinal={ordinal} citation={citation} hidden={hidden} />
      ))}
    </p>
  );
}

export function CitationToggle({
  count,
  hidden,
  onToggle,
}: {
  count: number;
  hidden: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="chat-cite-toggle"
      // Pressed means "citations are shown"; the accessible name stays "引用 N".
      aria-pressed={!hidden}
      title={hidden ? "显示引用按钮" : "隐藏引用按钮"}
      onClick={onToggle}
    >
      引用 {count}
    </button>
  );
}
