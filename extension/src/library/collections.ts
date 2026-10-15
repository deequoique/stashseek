export const COLLECTION_NAME_MAX_LENGTH = 20;
export const WHY_SAVED_MAX_LENGTH = 500;

const COLLECTION_NAME_PATTERN = /^[\p{L}\p{N}_-]+$/u;
const COLLECTION_TOKEN_PATTERN = /(^|\s)#([\p{L}\p{N}_-]{1,20})(?=\s|$)/gu;

export interface ParsedWhySaved {
  reason: string;
  collections: string[];
}

export interface FormattedWhySaved {
  value: string | null;
  error: string | null;
}

function normalizeCollectionName(value: string): string {
  const trimmed = value.trim();
  return trimmed.startsWith("#") ? trimmed.slice(1) : trimmed;
}

function collectionKey(value: string): string {
  return value.toLocaleLowerCase("en");
}

export function validateCollectionName(value: string): string | null {
  const normalized = normalizeCollectionName(value);
  if (!normalized) return "请输入收藏夹名称";
  if (Array.from(normalized).length > COLLECTION_NAME_MAX_LENGTH) {
    return `名称最多 ${COLLECTION_NAME_MAX_LENGTH} 个字符`;
  }
  if (!COLLECTION_NAME_PATTERN.test(normalized)) {
    return "名称只能使用中文、字母、数字、短横线或下划线";
  }
  return null;
}

export function parseWhySaved(value: string | null | undefined): ParsedWhySaved {
  const collections: string[] = [];
  const seen = new Set<string>();
  const reason = (value ?? "")
    .replace(COLLECTION_TOKEN_PATTERN, (match, leading: string, name: string) => {
      const key = collectionKey(name);
      if (!seen.has(key)) {
        seen.add(key);
        collections.push(name);
      }
      return leading || (match.startsWith(" ") ? " " : "");
    })
    .replace(/\s+/gu, " ")
    .trim();
  return { reason, collections };
}

export function formatWhySaved(reason: string, collection: string | null): FormattedWhySaved {
  return formatWhySavedWithCollections(reason, collection === null ? [] : [collection]);
}

export function formatWhySavedWithCollections(
  reason: string,
  collections: readonly string[],
): FormattedWhySaved {
  const parsedReason = parseWhySaved(reason).reason;
  const normalizedCollections: string[] = [];
  const seen = new Set<string>();
  for (const collection of collections) {
    const validationError = validateCollectionName(collection);
    if (validationError) return { value: null, error: validationError };
    const normalized = normalizeCollectionName(collection);
    const key = collectionKey(normalized);
    if (seen.has(key)) continue;
    seen.add(key);
    normalizedCollections.push(normalized);
  }
  const value = [parsedReason, ...normalizedCollections.map((name) => `#${name}`)]
    .filter(Boolean)
    .join(" ") || null;
  if (value && value.length > WHY_SAVED_MAX_LENGTH) {
    return { value: null, error: `保存说明和收藏夹合计最多 ${WHY_SAVED_MAX_LENGTH} 个字符` };
  }
  return { value, error: null };
}

export function collectCollectionNames(
  values: ReadonlyArray<string | null | undefined>,
): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  values.forEach((value) => {
    parseWhySaved(value).collections.forEach((name) => {
      const key = collectionKey(name);
      if (seen.has(key)) return;
      seen.add(key);
      names.push(name);
    });
  });
  return names;
}
