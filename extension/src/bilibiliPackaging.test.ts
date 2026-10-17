/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (name: string) => readFileSync(resolve(process.cwd(), name), "utf8");

describe("Bilibili packaging", () => {
  const manifest = JSON.parse(read("manifest.json")) as { host_permissions: string[] };

  it("asks for the video page host only: the player API and subtitle CDN are fetched inside the page", () => {
    const bilibiliHosts = manifest.host_permissions.filter((host) => /bilibili|hdslb/.test(host));
    expect(bilibiliHosts).toEqual(["https://www.bilibili.com/*"]);
  });

  it("keeps the package audit's exact host list in step with the manifest", () => {
    const audit = read("scripts/audit-package.mjs");
    for (const host of manifest.host_permissions.filter((value) => !value.includes("notebookai"))) {
      expect(audit, host).toContain(`"${host}"`);
    }
    expect(audit).not.toMatch(/api\.bilibili\.com|hdslb\.com/);
  });
});
