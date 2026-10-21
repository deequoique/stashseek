import { describe, expect, it } from "vitest";

import { BROWSER_COMPANION_STORE_URL, isExtensionContext } from "./browserCompanion";

describe("browser companion helpers", () => {
  it("points the install entry at the Chrome Web Store listing without tracking parameters", () => {
    expect(BROWSER_COMPANION_STORE_URL).toBe(
      "https://chromewebstore.google.com/detail/jeemllapbelpglmpnhjfamcnoibpeail",
    );
  });

  it("detects the extension origin only from the chrome-extension protocol", () => {
    expect(isExtensionContext({ protocol: "https:" })).toBe(false);
    expect(isExtensionContext({ protocol: "chrome-extension:" })).toBe(true);
    expect(isExtensionContext(undefined)).toBe(false);
  });
});
