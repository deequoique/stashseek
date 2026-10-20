export const BROWSER_COMPANION_DOWNLOAD_URL =
  "/assets/stashseek-chat-browser-companion-production-0.2.1.zip";

export const BROWSER_COMPANION_STORE_URL =
  "https://chromewebstore.google.com/detail/jeemllapbelpglmpnhjfamcnoibpeail";

// Inside the extension workspace the install entry points are redundant.
export function isExtensionContext(
  location: Pick<Location, "protocol"> | undefined = globalThis.location,
): boolean {
  return location?.protocol === "chrome-extension:";
}
