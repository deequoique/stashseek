import type { SelectedPart } from "./PartsPicker";

/** Loaded-scope actions are atomic: a capacity failure changes nothing. */
export function addLoadedParts(selected: readonly SelectedPart[], loaded: readonly SelectedPart[]): SelectedPart[] | null {
  const result = [...selected];
  const keys = new Set(result.map((value) => value.part.part_key));
  for (const value of loaded) {
    if (!value.part.selectable || !value.part.part_ref || keys.has(value.part.part_key)) continue;
    keys.add(value.part.part_key);
    result.push({ ...value, part: { ...value.part }, allowLegacyOverlap: false });
  }
  return result.length > 10 ? null : result;
}

export function refreshSelectedParts(selected: readonly SelectedPart[], refreshed: readonly SelectedPart[]): SelectedPart[] {
  const byKey = new Map(refreshed.map((value) => [value.part.part_key, value]));
  return selected.map((old) => {
    const next = byKey.get(old.part.part_key);
    if (!next?.part.selectable || !next.part.part_ref) return old;
    return { ...next, allowLegacyOverlap: next.part.has_unverified_legacy_item === old.part.has_unverified_legacy_item
      ? old.allowLegacyOverlap : false };
  });
}
