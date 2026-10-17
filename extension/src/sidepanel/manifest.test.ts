/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (name: string) => readFileSync(resolve(process.cwd(), name), "utf8");

describe("side panel packaging", () => {
  const manifest = JSON.parse(read("manifest.json")) as Record<string, unknown> & {
    permissions: string[];
    action: Record<string, unknown>;
    side_panel: { default_path: string };
  };

  it("declares the side panel and no popup", () => {
    expect(manifest.permissions).toEqual(["activeTab", "scripting", "storage", "sidePanel"]);
    expect(manifest.side_panel).toEqual({ default_path: "sidepanel.html" });
    expect(manifest.action.default_popup).toBeUndefined();
    expect(manifest.action.default_title).toBeTruthy();
  });

  it("keeps the manifest, package and client versions in step", () => {
    const pkg = JSON.parse(read("package.json")) as { version: string };
    expect(manifest.version).toBe("0.2.0");
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
