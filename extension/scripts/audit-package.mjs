import { existsSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { apiHostPermission, buildTarget } from "./build-targets.mjs";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const target = buildTarget(process.argv[2]);
const manifest = JSON.parse(readFileSync(resolve(dist, "manifest.json"), "utf8"));
const expectedPermissions = ["activeTab", "scripting", "storage", "sidePanel"];
const expectedHosts = [
  "https://www.youtube.com/*",
  "https://youtu.be/*",
  "https://ntulearn.ntu.edu.sg/*",
  "https://ntulearnvideo.ntu.edu.sg/*",
  "https://ntulearnv1.ntu.edu.sg/*",
  "https://cdnapisec.kaltura.com/*",
  apiHostPermission(target),
];

if (JSON.stringify(manifest.permissions) !== JSON.stringify(expectedPermissions)) throw new Error("unexpected extension permissions");
if (JSON.stringify(manifest.host_permissions) !== JSON.stringify(expectedHosts)) throw new Error("unexpected extension hosts");
if (manifest.content_security_policy || manifest.externally_connectable) throw new Error("unexpected extension execution boundary");
if (manifest.action?.default_popup) throw new Error("the toolbar action must open the side panel, not a popup");
if (manifest.side_panel?.default_path !== "sidepanel.html") throw new Error("unexpected side panel entry");
if (!existsSync(resolve(dist, manifest.side_panel.default_path))) throw new Error("side panel document missing from the package");
if (existsSync(resolve(dist, "popup.html"))) throw new Error("retired popup document packaged");
const sidePanelHtml = readFileSync(resolve(dist, "sidepanel.html"), "utf8");
if (/<script\b(?![^>]*\bsrc=)/.test(sidePanelHtml) || /(?:src|href)=["']https?:/.test(sidePanelHtml)) {
  throw new Error("side panel document has inline or remote code");
}

const files = readdirSync(dist);
if (files.some((name) => name.endsWith(".map") || name.includes("test"))) throw new Error("test or source-map artifact packaged");
const selectedApiOrigin = apiHostPermission(target).slice(0, -2);
const apiClient = readFileSync(resolve(dist, "api-client.js"), "utf8");
if (!apiClient.includes(JSON.stringify(selectedApiOrigin))) throw new Error("selected API origin missing from runtime client");
for (const name of files.filter((value) => value.endsWith(".js"))) {
  const body = readFileSync(resolve(dist, name), "utf8");
  if (/sourceMappingURL|<script\b|eval\s*\(|new\s+Function\s*\(/.test(body)) throw new Error(`remote/dynamic code marker in ${name}`);
}
