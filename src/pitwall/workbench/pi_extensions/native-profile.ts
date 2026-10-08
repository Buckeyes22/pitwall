import { enforceRuntimeSettings } from "./runtime-settings.js";
import { readFile, writeFile, lstat, mkdir, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { compileProfile, type CompiledProfile } from "./profile.js";
import { parseFrontmatter } from "@earendil-works/pi-coding-agent";

const profileFile = "native-profile.json";
const scoutName = "workbench-scout";
const managedNames = new Set(["workbench-scout", "workbench-worker", "workbench-reviewer"]);
const planningTools = ["read", "grep", "find", "ls"] as const;

export interface NativeProfilePaths {
  profilePath: string;
  agentPath: string;
  env: { PITWALL_WORKBENCH_NATIVE_PROFILE: string };
}

export interface NativeProfileOptions {
  /** Start the coordinator and every native child with read-only tools. */
  planning?: boolean;
}

function scoutFrontmatter(compiled: CompiledProfile): string {
  return `---
name: ${scoutName}
description: Read-only native scout
model: ${JSON.stringify(`${compiled.profile.provider}/${compiled.profile.modelId}`)}
tools: read, grep, find, ls
extensions: false
skills: false
inherit_context: false
isolated: true
allowed_subagents: none
isolation: off
---
Perform the assigned bounded read-only investigation and report evidence.
`;
}

function roleFrontmatter(compiled: CompiledProfile, name: "workbench-worker" | "workbench-reviewer", description: string, tools: string, planning: boolean): string {
  return `---
name: ${name}
description: ${description}
model: ${JSON.stringify(`${compiled.profile.provider}/${compiled.profile.modelId}`)}
tools: ${tools}
extensions: false
skills: false
inherit_context: false
isolated: true
allowed_subagents: none
isolation: off
---
${planning ? "Planning policy: read-only investigation only. Do not edit, write, run shell commands, commit, reset, switch branches, or modify files." : "Complete only the assigned bounded task. Do not commit, reset, switch branches, or modify files outside the supplied workspace."}
`;
}

/**
 * Validate the role definitions that the native backend will load for a
 * planning session. The generated files are owned, but a profile can also be
 * supplied directly through PITWALL_WORKBENCH_NATIVE_PROFILE; fail closed there
 * instead of assuming that `planning: true` makes a writable role safe.
 */
export async function validatePlanningRoleProfiles(agentDir: string, expectedModel: string): Promise<void> {
  const agentsDir = join(resolve(agentDir), "agents");
  for (const name of ["workbench-scout", "workbench-worker", "workbench-reviewer"] as const) {
    const path = join(agentsDir, `${name}.md`);
    let text: string;
    try {
      if ((await lstat(path)).isSymbolicLink()) throw new Error("managed role must not be a symlink");
      text = await readFile(path, "utf8");
    }
    catch (error) { throw new Error(`planning native profile requires managed role ${path}: ${error instanceof Error ? error.message : String(error)}`); }
    let frontmatter: Record<string, unknown>;
    try {
      frontmatter = parseFrontmatter<Record<string, unknown>>(text.startsWith("\uFEFF") ? text.slice(1) : text).frontmatter;
    } catch (error) {
      throw new Error(`planning native profile has invalid role ${path}: ${error instanceof Error ? error.message : String(error)}`);
    }
    const actualTools = typeof frontmatter.tools === "string" ? frontmatter.tools.split(",").map(value => value.trim()).filter(Boolean) : [];
    const expectedTools = [...planningTools];
    const toolsMatch = actualTools.length === expectedTools.length && expectedTools.every((tool, index) => actualTools[index] === tool);
    const identityMatches = frontmatter.name === name && frontmatter.model === expectedModel;
    const safe = identityMatches && toolsMatch && frontmatter.extensions === false && frontmatter.skills === false && frontmatter.inherit_context === false && frontmatter.isolated === true && frontmatter.allowed_subagents === "none" && frontmatter.isolation === "off";
    if (!identityMatches) throw new Error(`planning native profile role ${path} must declare name ${name} and exact model ${expectedModel}`);
    if (!safe) throw new Error(`planning native profile role ${path} must expose only read, grep, find, and ls with isolated context and no subagents`);
  }
}

async function ownedFile(path: string, contents: string): Promise<void> {
  try {
    if ((await lstat(path)).isSymbolicLink()) throw new Error(`native profile file must not be a symlink: ${path}`);
    const existing = await readFile(path, "utf8");
    if (existing !== contents) throw new Error(`native profile file conflict: ${path}`);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    await writeFile(path, contents, { encoding: "utf8", mode: 0o600, flag: "wx" });
  }
}

const nativeBackendSettings = {
  schedulingEnabled: false,
  workflowsEnabled: false,
  agentMentions: "off",
  disableDefaultAgents: true,
  fallbackSubagent: false,
  maxSubagentDepth: 0,
};
const nativeBackendSettingKeys = new Set(Object.keys(nativeBackendSettings));

async function rejectProjectBackendPolicyConflicts(cwd: string): Promise<void> {
  const path = join(resolve(cwd), ".pi", "subagents.json");
  let raw: unknown;
  try { raw = JSON.parse(await readFile(path, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw new Error(`invalid project subagent settings: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("invalid project subagent settings");
  const settings = raw as Record<string, unknown>;
  for (const key of Object.keys(settings)) if (!nativeBackendSettingKeys.has(key)) throw new Error(`unknown project subagent setting ${key}; Workbench native policy does not accept ignored backend keys`);
  for (const [key, expected] of Object.entries(nativeBackendSettings)) {
    if (Object.prototype.hasOwnProperty.call(settings, key) && settings[key] !== expected) throw new Error(`project subagent setting ${key} conflicts with Workbench native policy`);
  }
}

export async function configureNativeProfile(compiled: CompiledProfile, cwd = process.cwd(), options: NativeProfileOptions = {}): Promise<NativeProfilePaths> {
  const agentDir = resolve(compiled.agentDir);
  compiled = await compileProfile(compiled.name, compiled.profile, agentDir);
  await enforceRuntimeSettings(agentDir, cwd, compiled.profile);
  await rejectProjectBackendPolicyConflicts(cwd);
  const agentsDir = join(agentDir, "agents");
  try { if ((await lstat(agentsDir)).isSymbolicLink() || !(await lstat(agentsDir)).isDirectory()) throw new Error("native agents directory must be a real directory"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; await mkdir(agentsDir, { recursive: true, mode: 0o700 }); }
  const profilePath = join(agentDir, profileFile);
  const agentPath = join(agentsDir, `${scoutName}.md`);
  const workerPath = join(agentsDir, "workbench-worker.md");
  const reviewerPath = join(agentsDir, "workbench-reviewer.md");
  const ownedPaths = new Set([agentPath, workerPath, reviewerPath]);
  const collisionDirs = [join(agentDir, "agents"), join(resolve(cwd), ".pi", "agents"), join(resolve(cwd), ".agents", "agents")];
  for (const dir of collisionDirs) {
    let files: string[];
    try { files = (await readdir(dir)).filter((name) => name.endsWith(".md")); } catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") continue; throw error; }
    for (const file of files) {
      const path = join(dir, file);
      if (ownedPaths.has(path)) continue;
      if (options.planning) throw new Error(`planning native profile refuses unmanaged agent: ${path}`);
      const text = await readFile(path, "utf8");
      const declared = (parseFrontmatter<Record<string, unknown>>(text.startsWith("\uFEFF") ? text.slice(1) : text).frontmatter.name as string | undefined)?.trim();
      if (managedNames.has(file.slice(0, -3)) || (declared && managedNames.has(declared))) throw new Error(`${declared === scoutName || file === `${scoutName}.md` ? "native scout" : "native role"} collision: ${path}`);
    }
  }
  const profile = JSON.stringify({
    schemaVersion: 1,
    backend: "@tintinweb/pi-subagents",
    version: "0.19.0",
    profileName: compiled.name,
    ...(options.planning ? { planning: true } : {}),
    profile: compiled.profile,
    providerConfig: compiled.provider,
  }, null, 2) + "\n";
  await ownedFile(join(agentDir, "subagents.json"), JSON.stringify(nativeBackendSettings, null, 2) + "\n");
  await ownedFile(profilePath, profile);
  await ownedFile(agentPath, scoutFrontmatter(compiled));
  const planningToolText = planningTools.join(", ");
  await ownedFile(workerPath, roleFrontmatter(compiled, "workbench-worker", options.planning ? "Read-only planning worker" : "Bounded writer in an isolated handoff workspace", options.planning ? planningToolText : "read, edit, write, bash", options.planning === true));
  await ownedFile(reviewerPath, roleFrontmatter(compiled, "workbench-reviewer", "Read-only patch reviewer", planningToolText, options.planning === true));
  if (options.planning) await validatePlanningRoleProfiles(agentDir, `${compiled.profile.provider}/${compiled.profile.modelId}`);
  return { profilePath, agentPath, env: { PITWALL_WORKBENCH_NATIVE_PROFILE: profilePath } };
}
