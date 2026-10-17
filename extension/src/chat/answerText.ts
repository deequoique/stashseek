/**
 * The server appends a deterministic `来源：` block to grounded answer text;
 * the UI renders the structured citations instead of that text block.
 */
export function assistantTextWithoutSourceList(text: string, hasCitations: boolean): string {
  if (!hasCitations) return text;
  const sourceListStart = text.lastIndexOf("\n\n来源：\n");
  return sourceListStart === -1 ? text : text.slice(0, sourceListStart).trimEnd();
}
