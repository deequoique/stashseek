import { createContext, type ReactNode, useContext } from "react";
import Markdown, { type Components } from "react-markdown";

import type { ConversationCitation } from "../api/contracts";
import { CitationChip, remarkCitationChips } from "./citationChips";

interface MarkdownAnswerProps {
  children: string;
  /** Public citations of this answer; `[^n]` markers resolve to `citations[n - 1]`. */
  citations?: ConversationCitation[];
  /** Collapsed chips stay in the text but leave the accessibility tree and tab order. */
  citesHidden?: boolean;
}

interface CitationsState {
  citations: readonly ConversationCitation[];
  hidden: boolean;
}

const NO_CITATIONS: ConversationCitation[] = [];
const CitationsContext = createContext<CitationsState>({ citations: NO_CITATIONS, hidden: false });

const remarkPlugins = [remarkCitationChips];

/** The raw marker number the remark plugin attached, or null for an ordinary link. */
function citationMarker(props: object): string | null {
  const raw = (props as Record<string, unknown>)["data-cite"];
  return typeof raw === "string" || typeof raw === "number" ? String(raw) : null;
}

function MarkdownLink({ marker, children }: { marker: string | null; children: ReactNode }) {
  const { citations, hidden } = useContext(CitationsContext);
  // Model-authored links stay inert text; only a server-numbered marker that
  // resolves to a real citation becomes a link.
  if (marker === null) return <span>{children}</span>;
  const ordinal = Number(marker);
  const citation = Number.isInteger(ordinal) && ordinal >= 1 ? citations[ordinal - 1] : undefined;
  return citation
    ? <CitationChip ordinal={ordinal} citation={citation} hidden={hidden} />
    : <span>{`[^${marker}]`}</span>;
}

// Module-level and stable on purpose: an inline `components` object gives
// react-markdown a new component type per render, which would remount every
// chip on each streamed delta and drop keyboard focus or an open tooltip.
const components: Components = {
  a: ({ children, ...props }) => <MarkdownLink marker={citationMarker(props)}>{children}</MarkdownLink>,
  img: ({ alt }) => <span>{alt ?? ""}</span>,
};

export function MarkdownAnswer({ children, citations = NO_CITATIONS, citesHidden = false }: MarkdownAnswerProps) {
  return (
    <CitationsContext.Provider value={{ citations, hidden: citesHidden }}>
      <div className="chat-markdown">
        <Markdown remarkPlugins={remarkPlugins} components={components}>
          {children}
        </Markdown>
      </div>
    </CitationsContext.Provider>
  );
}
