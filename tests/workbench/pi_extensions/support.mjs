// Shared helpers for the Node built-in test suites in this directory. No npm dependencies.
//
// The extensions import `@earendil-works/pi-ai` and `@earendil-works/pi-coding-agent` at run time;
// the Pi host normally supplies those. The suites that exercise them resolve an installed copy,
// looked up in order from PITWALL_PI_MODULES, and the user-level
// node prefix, and skip with a reason when none is complete.
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdtempSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { after, test } from "node:test";

const here = new URL(".", import.meta.url).pathname;
export const extensionDirectory = resolve(here, "../../../src/pitwall/workbench/pi_extensions");

const REQUIRED = ["pi-ai", "pi-coding-agent"];

function completeModulesDirectory(directory) {
  return REQUIRED.every((name) => existsSync(join(directory, "@earendil-works", name, "package.json")));
}

function locatePiModules() {
  const candidates = [
    process.env.PITWALL_PI_MODULES,
    join(homedir(), ".local", "lib", "node_modules"),
  ];
  return candidates.find((directory) => directory && completeModulesDirectory(directory));
}

export const piModulesDirectory = locatePiModules();
export const skipReason = piModulesDirectory
  ? false
  : "@earendil-works/pi-ai and pi-coding-agent are not installed (set PITWALL_PI_MODULES to a node_modules directory containing them)";

let copyDirectory;

/**
 * Import a compiled extension module from a scratch copy of the extension directory that has the
 * located Pi packages linked as its node_modules, so bare `@earendil-works/*` imports resolve
 * without installing anything next to the package sources.
 */
export async function loadExtension(name) {
  if (!copyDirectory) {
    copyDirectory = mkdtempSync(join(tmpdir(), "pitwall-pi-extensions-"));
    process.on("exit", () => rmSync(copyDirectory, { recursive: true, force: true }));
    for (const entry of readdirSync(extensionDirectory)) {
      if (entry.endsWith(".js") || entry === "package.json") cpSync(join(extensionDirectory, entry), join(copyDirectory, entry));
    }
    writeFileSync(join(copyDirectory, "import-pi.js"), "export const importPi = (specifier) => import(specifier);\n");
    if (piModulesDirectory) symlinkSync(piModulesDirectory, join(copyDirectory, "node_modules"));
  }
  return import(pathToFileURL(join(copyDirectory, name)).href);
}

/** Import a Pi package (or a file inside one) the same way the extensions resolve it. */
export async function importPi(specifier) {
  const { importPi: importer } = await loadExtension("import-pi.js");
  return importer(specifier);
}

/** URL of a compiled extension module inside the scratch copy (for spawned host processes). */
export async function extensionUrl(name) {
  await loadExtension("import-pi.js");
  return pathToFileURL(join(copyDirectory, name)).href;
}

/** `test` that skips with a reason when the Pi packages are unavailable. */
export function piTest(name, ...rest) {
  const fn = rest.pop();
  const options = rest[0] ?? {};
  return test(name, skipReason ? { ...options, skip: skipReason } : options, fn);
}

/**
 * The extensions unref their deadline timers because the Pi host keeps the loop alive. Under
 * `node --test` nothing else does, so a pending deadline would end the process early; hold the
 * loop open for the life of the file.
 */
export function keepEventLoopAlive() {
  const handle = setInterval(() => undefined, 1000);
  after(() => clearInterval(handle));
}

export function errorLike(expected) {
  if (expected instanceof RegExp) return expected;
  return (error) => {
    assert.ok(String(error?.message ?? error).includes(expected), `expected error message to contain ${JSON.stringify(expected)}, got ${JSON.stringify(String(error?.message ?? error))}`);
    return true;
  };
}

const ANY = Symbol("any");
const ARRAY_CONTAINING = Symbol("arrayContaining");
const OBJECT_CONTAINING = Symbol("objectContaining");
const STRING_CONTAINING = Symbol("stringContaining");
const STRING_MATCHING = Symbol("stringMatching");

export const any = (type) => ({ [ANY]: type });
export const arrayContaining = (items) => ({ [ARRAY_CONTAINING]: items });
export const objectContaining = (value) => ({ [OBJECT_CONTAINING]: value });
export const stringContaining = (value) => ({ [STRING_CONTAINING]: value });
export const stringMatching = (value) => ({ [STRING_MATCHING]: value });

function isType(value, type) {
  if (type === Number) return typeof value === "number";
  if (type === String) return typeof value === "string";
  if (type === Boolean) return typeof value === "boolean";
  if (type === Object) return typeof value === "object" && value !== null;
  return value instanceof type;
}

function subset(actual, expected, strictArrays) {
  if (expected !== null && typeof expected === "object") {
    if (ANY in expected) return isType(actual, expected[ANY]);
    if (ARRAY_CONTAINING in expected) {
      return Array.isArray(actual) && expected[ARRAY_CONTAINING].every((item) => actual.some((candidate) => subset(candidate, item, false)));
    }
    if (OBJECT_CONTAINING in expected) return subset(actual, expected[OBJECT_CONTAINING], false);
    if (STRING_CONTAINING in expected) return typeof actual === "string" && actual.includes(expected[STRING_CONTAINING]);
    if (STRING_MATCHING in expected) return typeof actual === "string" && new RegExp(expected[STRING_MATCHING]).test(actual);
    if (Array.isArray(expected)) {
      return Array.isArray(actual) && actual.length === expected.length && expected.every((item, index) => subset(actual[index], item, strictArrays));
    }
    if (actual === null || typeof actual !== "object") return false;
    return Object.keys(expected).every((key) => subset(actual[key], expected[key], strictArrays));
  }
  return Object.is(actual, expected);
}

export function assertMatch(actual, expected) {
  assert.ok(subset(actual, expected, true), `value does not match expected shape\nactual: ${JSON.stringify(actual, null, 2)}\nexpected: ${JSON.stringify(expected, null, 2)}`);
}

export function assertNotMatch(actual, expected) {
  assert.ok(!subset(actual, expected, true), `value unexpectedly matches ${JSON.stringify(expected)}`);
}

/** Retry an assertion until it stops throwing (default 1 s budget, 10 ms interval). */
export async function waitFor(check, { timeout = 1000, interval = 10 } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    try {
      return await check();
    } catch (error) {
      if (Date.now() > deadline) throw error;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, interval));
    }
  }
}
