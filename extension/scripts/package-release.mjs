// Builds the production extension and writes two deterministic release zips:
//
//   stashseek-chat-browser-companion-production-<version>.zip
//       the audited production build as is, including the manifest `key`, so a
//       zip install keeps its extension ID;
//   stashseek-chat-chrome-web-store-<version>.zip
//       the same files, except that `manifest.json` has no `key`, because the
//       Chrome Web Store assigns the item ID itself and rejects the field.
//
// Entries are sorted and carry a fixed timestamp, so the same sources always
// produce byte-identical archives.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { relative, resolve, sep } from "node:path";
import { deflateRawSync } from "node:zlib";

import { apiHostPermission } from "./build-targets.mjs";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const releaseDir = resolve(root, "release");
const bin = (name) => resolve(root, "node_modules", ".bin", name);

function run(command, args) {
  execFileSync(command, args, { cwd: root, stdio: "inherit" });
}

function buildProduction() {
  run(process.execPath, ["scripts/clean.mjs"]);
  run(bin("tsc"), ["-p", "tsconfig.json"]);
  run(bin("vite"), ["build", "--config", "vite.config.ts"]);
  run(process.execPath, ["scripts/copy-static.mjs", "production"]);
  run(process.execPath, ["scripts/audit-package.mjs", "production"]);
}

function listFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? listFiles(path) : [path];
  });
}

/** Every packaged file as `name -> bytes`, with sorted, slash-separated names. */
function readDist() {
  const entries = [];
  for (const path of listFiles(dist)) {
    if (statSync(path).isFile()) entries.push([relative(dist, path).split(sep).join("/"), readFileSync(path)]);
  }
  return new Map(entries.sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0)));
}

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_unused, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

// 1980-01-01 00:00:00, the DOS epoch: a fixed timestamp keeps archives reproducible.
const DOS_TIME = 0;
const DOS_DATE = (1 << 5) | 1;
const UNIX_FILE_MODE = (0o100644 << 16) >>> 0;

/** A minimal ZIP writer (deflate, no ZIP64): enough for a few hundred KiB of extension files. */
function zipArchive(entries) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, content] of entries) {
    const nameBytes = Buffer.from(name, "utf8");
    const data = deflateRawSync(content, { level: 9 });
    const crc = crc32(content);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed to extract
    local.writeUInt16LE(0x0800, 6); // UTF-8 names
    local.writeUInt16LE(8, 8); // deflate
    local.writeUInt16LE(DOS_TIME, 10);
    local.writeUInt16LE(DOS_DATE, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(content.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    locals.push(local, nameBytes, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE((3 << 8) | 20, 4); // made by Unix, spec 2.0
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(8, 10);
    central.writeUInt16LE(DOS_TIME, 12);
    central.writeUInt16LE(DOS_DATE, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(content.length, 24);
    central.writeUInt16LE(nameBytes.length, 28);
    central.writeUInt32LE(UNIX_FILE_MODE, 38);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, nameBytes);

    offset += local.length + nameBytes.length + data.length;
  }
  const centralDirectory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.size, 8);
  end.writeUInt16LE(entries.size, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, centralDirectory, end]);
}

/** Fails the release when a package would carry something it must not. */
function verifyPackage(label, entries, { expectKey }) {
  const fail = (message) => { throw new Error(`${label}: ${message}`); };
  const manifestBytes = entries.get("manifest.json");
  if (!manifestBytes) fail("manifest.json missing");
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  const hosts = manifest.host_permissions ?? [];
  if (!hosts.includes(apiHostPermission("production"))) fail("production API host permission missing");
  if (hosts.some((host) => /ntu\.edu\.sg|kaltura\.com|127\.0\.0\.1|localhost/.test(host))) fail("unexpected host permission");
  if (expectKey !== Object.hasOwn(manifest, "key")) fail(expectKey ? "manifest key missing" : "manifest key must be absent");
  const icons = Object.values(manifest.icons ?? {});
  if (!icons.length) fail("manifest has no icons");
  for (const icon of icons) {
    if (!entries.has(icon)) fail(`icon missing: ${icon}`);
  }
  for (const name of entries.keys()) {
    if (name.endsWith(".map")) fail(`source map packaged: ${name}`);
  }
}

function writeRelease(name, entries) {
  const archive = zipArchive(entries);
  const path = resolve(releaseDir, name);
  writeFileSync(path, archive);
  const sha256 = createHash("sha256").update(archive).digest("hex");
  console.log(`${path}\n  ${entries.size} files, ${archive.length} bytes, sha256 ${sha256}`);
}

buildProduction();

const productionEntries = readDist();
const productionManifest = JSON.parse(productionEntries.get("manifest.json").toString("utf8"));
const { key: _storeIdentityKey, ...storeManifest } = productionManifest;
const storeEntries = new Map(productionEntries);
storeEntries.set("manifest.json", Buffer.from(`${JSON.stringify(storeManifest, null, 2)}\n`));

verifyPackage("production zip", productionEntries, { expectKey: true });
verifyPackage("store zip", storeEntries, { expectKey: false });

mkdirSync(releaseDir, { recursive: true });
writeRelease(`stashseek-chat-browser-companion-production-${productionManifest.version}.zip`, productionEntries);
writeRelease(`stashseek-chat-chrome-web-store-${productionManifest.version}.zip`, storeEntries);
