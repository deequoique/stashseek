// Extract display entries, leaving URL admission and canonicalization to the server.
// Lines without URLs stay visible for validation, apart from standalone share titles.
export function parseVideoInput(value: string): string[] {
  return value.split(/\r?\n/u).flatMap((rawLine) => {
    const line = rawLine.trim().replace(
      /(^|\s)【[^【】]*】(?=\s*(?:[a-z][a-z0-9+.-]*:\/\/|$))/giu,
      "$1",
    ).trim();
    if (!line) return [];

    const urls = Array.from(
      line.matchAll(/(?:^|\s)((?:[a-z][a-z0-9+.-]*:)?\/\/[^\s]+)/giu),
      (match) => match[1],
    );
    return urls.length > 0 ? urls : [line];
  });
}
