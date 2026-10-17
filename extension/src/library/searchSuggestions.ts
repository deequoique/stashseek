import type { LibraryItemSummary } from "../api/contracts";
import { parseWhySaved } from "./collections";

export interface SearchSuggestion {
  kind: "标题" | "作者" | "收藏夹" | "保存说明";
  value: string;
}

export function collectSearchSuggestions(
  items: ReadonlyArray<LibraryItemSummary>,
  query: string,
  limit = 6,
): SearchSuggestion[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  if (!normalizedQuery) return [];

  const suggestions: SearchSuggestion[] = [];
  const seen = new Set<string>();
  const add = (kind: SearchSuggestion["kind"], rawValue: string | null | undefined) => {
    const value = rawValue?.trim();
    if (!value || !value.toLocaleLowerCase().includes(normalizedQuery)) return;
    const key = value.toLocaleLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    suggestions.push({ kind, value });
  };

  for (const item of items) {
    const parsedWhySaved = parseWhySaved(item.why_saved);
    add("标题", item.title);
    add("作者", item.author);
    parsedWhySaved.collections.forEach((name) => add("收藏夹", `#${name}`));
    add("保存说明", parsedWhySaved.reason);
    if (suggestions.length >= limit) break;
  }

  return suggestions.slice(0, limit);
}
