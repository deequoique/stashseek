import { describe, expect, it } from "vitest";
import { addLoadedParts, refreshSelectedParts } from "./partSelection";
import type { SelectedPart } from "./PartsPicker";

function selected(index: number): SelectedPart {
  return { parentTitle: "视频", allowLegacyOverlap: false, part: {
    part_key: `BV1xx411c7mD:${index}`, part_ref: `ref-${index}`, page: index,
    selectable: true, has_unverified_legacy_item: false,
  } };
}
describe("loaded part selection", () => {
  it("deduplicates stable identities without replacing a frozen chosen ref", () => {
    const chosen = selected(1);
    const refreshed = { ...selected(1), part: { ...selected(1).part, part_ref: "new-proof" } };
    expect(addLoadedParts([chosen], [refreshed, selected(2)])).toEqual([chosen, selected(2)]);
  });
  it("rejects capacity atomically and ignores unavailable/unproven rows", () => {
    const items = Array.from({ length: 11 }, (_, index) => selected(index + 1));
    expect(addLoadedParts([], items)).toBeNull();
    expect(addLoadedParts([items[0]], [{ ...items[1], part: { ...items[1].part, selectable: false } }])).toEqual([items[0]]);
  });
  it("explicit refresh replaces only matching stable keys and clears changed compatibility consent", () => {
    const previous = { ...selected(1), allowLegacyOverlap: true };
    const refreshed = { ...selected(1), part: { ...selected(1).part, page: 6, part_ref: "new-proof", has_unverified_legacy_item: true } };
    expect(refreshSelectedParts([previous, selected(2)], [refreshed, selected(3)])).toEqual([
      { ...refreshed, allowLegacyOverlap: false }, selected(2),
    ]);
  });
});
