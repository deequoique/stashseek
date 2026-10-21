/// <reference types="node" />

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (name: string) => readFileSync(resolve(process.cwd(), name), "utf8");

describe("side panel packaging", () => {
  const manifest = JSON.parse(read("manifest.json")) as Record<string, unknown> & {
    permissions: string[];
    host_permissions: string[];
    icons: Record<string, string>;
    action: Record<string, unknown> & { default_icon: Record<string, string> };
    side_panel: { default_path: string };
  };

  it("declares the side panel and no popup", () => {
    expect(manifest.permissions).toEqual(["activeTab", "scripting", "storage", "sidePanel"]);
    expect(manifest.side_panel).toEqual({ default_path: "sidepanel.html" });
    expect(manifest.action.default_popup).toBeUndefined();
    expect(manifest.action.default_title).toBeTruthy();
  });

  it("ships the toolbar and store icons, and every manifest icon file exists", () => {
    expect(Object.keys(manifest.icons)).toEqual(["16", "32", "48", "128"]);
    for (const path of [...Object.values(manifest.icons), ...Object.values(manifest.action.default_icon)]) {
      expect(path).toMatch(/^icons\/icon-(?:16|32|48|128)\.png$/);
      expect(existsSync(resolve(process.cwd(), "public", path)), path).toBe(true);
    }
  });

  it("asks only for YouTube, Bilibili and the API host: no NTULearn or Kaltura hosts", () => {
    expect(manifest.host_permissions).toEqual([
      "https://www.youtube.com/*",
      "https://youtu.be/*",
      "https://www.bilibili.com/*",
      "https://stashseek.deequoique.tech/*",
    ]);
  });

  it("packages the release zips from the production build", () => {
    const pkg = JSON.parse(read("package.json")) as { scripts: Record<string, string> };
    expect(pkg.scripts["package:release"]).toBe("node scripts/package-release.mjs");
  });

  it("keeps the manifest, package and client versions in step", () => {
    const pkg = JSON.parse(read("package.json")) as { version: string };
    expect(manifest.version).toBe("0.2.3");
    expect(pkg.version).toBe(manifest.version);
    expect(read("src/protocol.ts")).toContain(`CLIENT_VERSION = "${String(manifest.version)}"`);
    expect(read("src/app/browserCompanion.ts")).toContain(`production-${String(manifest.version)}.zip`);
  });

  it("builds the panel document from its own entry and the workspace as a second input", () => {
    expect(read("sidepanel.html")).toContain('src="/src/sidepanel/main.tsx"');
    const vite = read("vite.config.ts");
    expect(vite).toContain('"workspace.html"');
    expect(vite).toContain('"sidepanel.html"');
  });
});
