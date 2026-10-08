import { createHash, randomUUID } from "node:crypto";
import { execFile, spawn, type ChildProcess } from "node:child_process";
import { createWriteStream } from "node:fs";
import { type Readable } from "node:stream";
import { finished } from "node:stream/promises";
import { lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import { StringDecoder } from "node:string_decoder";

const exec = promisify(execFile);
const HANDOFF_DIR = ".pi-workbench-handoffs";

export type HandoffManifest = { schemaVersion: 1; taskId: string; base: string; baseRef: string; revision: string; finalRevision?: string; unexpectedCommit?: boolean; workspace: string; patchPath?: string; patchSha256?: string; treeHash?: string; status: "active" | "completed" | "cancelled" | "failed"; validation: "not-run" | "passed" | "failed"; review: "pending" | "passed" | "failed"; acceptance: "pending" | "accepted" | "rejected" };
export type ArtifactRecord = { path: string; sha256: string; bytes: number; view: string; truncated: boolean; complete: boolean; error?: string };
export type ReviewRecord = { schemaVersion: 1; taskId: string; revision: string; patchSha256: string; status: "completed" | "failed"; report: string; reportArtifact: ArtifactRecord; recordedAt: string };
export type HandoffWorkspace = HandoffManifest & { dispose: () => Promise<void> };

async function git(cwd: string, args: string[], trim = true): Promise<string> {
  const result = await exec("git", ["-C", cwd, ...args], { maxBuffer: 8 * 1024 * 1024 });
  return trim ? result.stdout.trim() : result.stdout;
}
async function gitWithEnv(cwd: string, args: string[], env: NodeJS.ProcessEnv, trim = true): Promise<string> {
  const result = await exec("git", ["-C", cwd, ...args], { maxBuffer: 8 * 1024 * 1024, env: { ...process.env, ...env } });
  return trim ? result.stdout.trim() : result.stdout;
}
async function writeManifest(manifest: HandoffManifest): Promise<void> {
  await writeFile(join(manifest.workspace, "..", ".pi-workbench-handoff.json"), `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
}

async function releaseWriterLock(base: string, taskId: string): Promise<void> {
  const lock = join(base, HANDOFF_DIR, ".writer-lock");
  let owner: { taskId?: unknown };
  try {
    owner = JSON.parse(await readFile(join(lock, "owner"), "utf8")) as { taskId?: unknown };
  } catch (error) {
    throw new Error(`writer lock owner is unreadable; refusing to remove lock: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (owner.taskId !== taskId) throw new Error("writer lock owner does not match handoff; refusing to remove lock");
  await rm(lock, { recursive: true, force: true });
}

async function writerProcessAlive(pid: number): Promise<boolean> {
  try { process.kill(pid, 0); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    throw error;
  }
  if (process.platform !== "linux") return true;
  try { return !/^State:\s+Z/m.test(await readFile(`/proc/${pid}/status`, "utf8")); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

export async function createHandoffWorkspace(cwd: string): Promise<HandoffWorkspace> {
  const base = resolve(cwd);
  if (await git(base, ["status", "--porcelain", "--untracked-files=all", "--", ".", `:(exclude)${HANDOFF_DIR}`])) throw new Error("writer requires a clean base workspace; preserve or integrate existing changes first");
  const revision = await git(base, ["rev-parse", "HEAD"]);
  const handoffRoot = join(base, HANDOFF_DIR);
  await mkdir(handoffRoot, { recursive: true, mode: 0o700 });
  const lock = join(handoffRoot, ".writer-lock");
  try { await mkdir(lock, { mode: 0o700 }); } catch { throw new Error("active writer handoff already exists; recover or review it before starting another"); }
  const taskId = `task-${Date.now()}-${createHash("sha256").update(`${base}\0${revision}\0${Math.random()}`).digest("hex").slice(0, 12)}`;
  const root = await mkdtemp(join(handoffRoot, `${taskId}-`));
  const path = join(root, "worktree");
  try { await exec("git", ["-C", base, "worktree", "add", "--detach", path, revision], { maxBuffer: 2 * 1024 * 1024 }); }
  catch (error) { await rm(root, { recursive: true, force: true }); await rm(lock, { recursive: true, force: true }); throw error; }
  await writeFile(join(lock, "owner"), JSON.stringify({ pid: process.pid, taskId }), { mode: 0o600 });
  const manifest: HandoffManifest = { schemaVersion: 1, taskId, base, baseRef: "HEAD", revision, workspace: path, status: "active", validation: "not-run", review: "pending", acceptance: "pending" };
  await writeManifest(manifest);
  return { ...manifest, dispose: async () => undefined };
}

export async function capturePatch(workspace: HandoffWorkspace, status: HandoffManifest["status"] = "completed"): Promise<{ patch: string; manifest: HandoffManifest; patchPath: string; changedFiles: string[] }> {
  const finalRevision = await git(workspace.workspace, ["rev-parse", "HEAD"]);
  const unexpectedCommit = finalRevision !== workspace.revision;
  await git(workspace.workspace, ["add", "--all", "--", ".", ":(exclude).pi-workbench-handoff.json", ":(exclude)handoff.patch"]);
  const patch = await git(workspace.workspace, ["diff", "--cached", "--binary", workspace.revision], false);
  const changedFiles = (await git(workspace.workspace, ["diff", "--cached", "--name-only", "-z", workspace.revision], false)).split("\0").filter(Boolean);
  const patchPath = join(workspace.workspace, "..", "handoff.patch");
  await writeFile(patchPath, patch, { mode: 0o600 });
  const treeHash = await git(workspace.workspace, ["write-tree"]);
  const manifest: HandoffManifest = { schemaVersion: 1, taskId: workspace.taskId, base: workspace.base, baseRef: "HEAD", revision: workspace.revision, finalRevision, unexpectedCommit, workspace: workspace.workspace, status: unexpectedCommit ? "failed" : status, patchPath, patchSha256: createHash("sha256").update(patch).digest("hex"), treeHash, validation: "not-run", review: "pending", acceptance: unexpectedCommit ? "rejected" : "pending" };
  await writeManifest(manifest);
  if (status !== "active" || unexpectedCommit) await releaseWriterLock(workspace.base, workspace.taskId);
  if (unexpectedCommit) throw new Error(`unexpected writer commit detected: base ${workspace.revision}, final ${finalRevision}; acceptance rejected`);
  return { patch, manifest, patchPath, changedFiles };
}

export async function loadHandoff(baseCwd: string, taskId: string): Promise<HandoffManifest> {
  if (!/^task-[0-9]+-[a-f0-9]{12}$/.test(taskId)) throw new Error("invalid handoff task id");
  const base = resolve(baseCwd);
  const entries = await readdir(join(base, HANDOFF_DIR), { withFileTypes: true });
  const directory = entries.find((entry) => entry.isDirectory() && entry.name.startsWith(`${taskId}-`));
  if (!directory) throw new Error("handoff task not found");
  const manifest = JSON.parse(await readFile(join(base, HANDOFF_DIR, directory.name, ".pi-workbench-handoff.json"), "utf8")) as HandoffManifest;
  if (manifest.schemaVersion !== 1 || manifest.taskId !== taskId || manifest.base !== base || manifest.baseRef !== "HEAD") throw new Error("handoff manifest does not match requested base");
  const root = resolve(base, HANDOFF_DIR, directory.name);
  if (resolve(manifest.workspace) !== resolve(root, "worktree") || resolve(manifest.patchPath ?? join(root, "handoff.patch")) !== resolve(root, "handoff.patch")) throw new Error("handoff manifest path escapes its owned directory");
  if ((await lstat(manifest.workspace)).isSymbolicLink()) throw new Error("handoff workspace must not be a symlink");
  if (manifest.patchPath && manifest.patchSha256) {
    const patch = await readFile(manifest.patchPath, "utf8");
    if (createHash("sha256").update(patch).digest("hex") !== manifest.patchSha256) throw new Error("handoff patch hash mismatch");
    await git(manifest.workspace, ["add", "--all", "--", ".", ":(exclude).pi-workbench-handoff.json", ":(exclude)handoff.patch"]);
    const currentPatch = await git(manifest.workspace, ["diff", "--cached", "--binary", manifest.revision], false);
    if (createHash("sha256").update(currentPatch).digest("hex") !== manifest.patchSha256) throw new Error("handoff workspace changed since patch capture");
  }
  return manifest;
}

export async function recoverHandoff(baseCwd: string, taskId: string): Promise<HandoffManifest> {
  const manifest = await loadHandoff(baseCwd, taskId).catch(async (error) => {
    if (!(error instanceof Error) || !error.message.includes("workspace changed")) throw error;
    throw error;
  });
  if (manifest.status !== "active") return manifest;
  let owner: { pid?: unknown; taskId?: unknown };
  try { owner = JSON.parse(await readFile(join(resolve(baseCwd), HANDOFF_DIR, ".writer-lock", "owner"), "utf8")); }
  catch { throw new Error("writer lock owner is unreadable; manual recovery required"); }
  if (owner.taskId !== taskId || typeof owner.pid !== "number" || !Number.isInteger(owner.pid) || owner.pid <= 0) throw new Error("writer lock owner does not match handoff");
  if (await writerProcessAlive(owner.pid)) throw new Error("writer process is still alive");
  const recovered = { ...manifest, status: "failed" as const };
  await writeManifest(recovered);
  // A dead writer PID does not prove that detached tool descendants are gone.
  // Keep the lock for manual inspection so recovery cannot create a duplicate writer.
  return recovered;
}

export async function updateValidation(manifest: HandoffManifest, validation: HandoffManifest["validation"]): Promise<HandoffManifest> {
  const updated = { ...manifest, validation };
  await writeManifest(updated);
  return updated;
}

export async function recordReview(manifest: HandoffManifest, report: string, status: ReviewRecord["status"] = "completed"): Promise<ReviewRecord> {
  if (!manifest.patchPath || !manifest.patchSha256) throw new Error("review requires a captured patch");
  const patch = await readFile(manifest.patchPath, "utf8");
  if (createHash("sha256").update(patch).digest("hex") !== manifest.patchSha256) throw new Error("handoff patch hash mismatch");
  const artifactRoot = await ensureArtifactDirectory(manifest);
  const reportHash = createHash("sha256").update(report).digest("hex");
  const reportPath = join(artifactRoot, `review-report-${reportHash.slice(0, 16)}.txt`);
  await writeFile(reportPath, report, { mode: 0o600, flag: "wx" }).catch(async (error: unknown) => {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    const existing = await readFile(reportPath, "utf8");
    if (createHash("sha256").update(existing).digest("hex") !== reportHash) throw new Error("review report artifact collision");
  });
  const reportLimit = 4_000;
  const reportView = boundedOutput(report, reportLimit, reportLimit);
  const reportArtifact: ArtifactRecord = { path: reportPath, sha256: reportHash, bytes: Buffer.byteLength(report), view: reportView.view, truncated: reportView.truncated, complete: true };
  const record: ReviewRecord = { schemaVersion: 1, taskId: manifest.taskId, revision: manifest.revision, patchSha256: manifest.patchSha256, status, report: reportView.view, reportArtifact, recordedAt: new Date().toISOString() };
  await writeFile(join(manifest.workspace, "..", "review.json"), `${JSON.stringify(record, null, 2)}\n`, { mode: 0o600 });
  return record;
}

export type ApprovedCheck = { command: string; args?: string[]; timeoutMs?: number };
export type CheckResult = { command: string; args?: string[]; timeoutMs?: number; code: number; stdout: string; stderr: string; stdoutArtifact: ArtifactRecord; stderrArtifact: ArtifactRecord; artifactError?: string };
export type ApprovedChecksResult = { passed: boolean; results: CheckResult[]; artifactDirectory: string; validationPath: string };
export type HandoffIntegrationResult = {
  schemaVersion: 1;
  taskId: string;
  target: string;
  expectedRevision: string;
  targetRevision: string;
  patchPath: string;
  patchSnapshotPath: string;
  patchSha256: string;
  status: "applied" | "failed" | "conflict" | "rejected";
  recordPath: string;
  checks?: ApprovedChecksResult;
  postApplyPatchSha256?: string;
  postApplyPatchMatches?: boolean;
  errorArtifact?: ArtifactRecord;
  error?: string;
  recordedAt: string;
};

const OUTPUT_HEAD_LIMIT = 2_000;
const OUTPUT_TAIL_LIMIT = 2_000;

type OutputCapture = {
  path: string;
  stream: ReturnType<typeof createWriteStream>;
  hash: ReturnType<typeof createHash>;
  bytes: number;
  chars: number;
  prefix: string;
  tail: string;
  decoder: StringDecoder;
  source?: Readable;
  onError?: () => void;
  streamError?: string;
};

function boundedOutput(value: string, headLimit = OUTPUT_HEAD_LIMIT, tailLimit = OUTPUT_TAIL_LIMIT): { view: string; truncated: boolean } {
  if (value.length <= headLimit + tailLimit) return { view: value, truncated: false };
  return { view: `${value.slice(0, headLimit)}\n…[truncated; complete output is in the artifact]…\n${value.slice(-tailLimit)}`, truncated: true };
}

async function ensureArtifactDirectory(manifest: HandoffManifest): Promise<string> {
  const root = resolve(manifest.workspace, "..");
  const directory = join(root, "artifacts");
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const stat = await lstat(directory);
  if (stat.isSymbolicLink() || !stat.isDirectory() || (stat.mode & 0o077) !== 0) throw new Error("handoff artifact directory must be a private regular directory");
  return directory;
}

function appendCapture(capture: OutputCapture, part: Buffer, source?: Readable): void {
  capture.hash.update(part);
  capture.bytes += part.byteLength;
  const text = capture.decoder.write(part);
  captureText(capture, text);
  if (capture.stream.destroyed || capture.stream.writableEnded) return;
  try {
    if (!capture.stream.write(part) && source) {
      capture.source = source;
      source.pause();
      capture.stream.once("drain", () => { capture.source = undefined; source.resume(); });
    }
  } catch (error) {
    capture.streamError = error instanceof Error ? error.message : String(error);
    source?.resume();
    capture.onError?.();
  }
}

function captureText(capture: OutputCapture, text: string): void {
  capture.chars += text.length;
  if (capture.prefix.length < OUTPUT_HEAD_LIMIT + OUTPUT_TAIL_LIMIT) capture.prefix += text.slice(0, OUTPUT_HEAD_LIMIT + OUTPUT_TAIL_LIMIT - capture.prefix.length);
  capture.tail = `${capture.tail}${text}`.slice(-OUTPUT_TAIL_LIMIT);
}

async function finishCapture(capture: OutputCapture): Promise<ArtifactRecord> {
  const trailing = capture.decoder.end();
  if (trailing) captureText(capture, trailing);
  if (!capture.stream.destroyed && !capture.stream.writableEnded) capture.stream.end();
  try { await finished(capture.stream); } catch (error) { capture.streamError = error instanceof Error ? error.message : String(error); }
  const view = capture.chars <= OUTPUT_HEAD_LIMIT + OUTPUT_TAIL_LIMIT
    ? capture.prefix
    : `${capture.prefix.slice(0, OUTPUT_HEAD_LIMIT)}\n…[truncated; complete output is in the artifact]…\n${capture.tail}`;
  return { path: capture.path, sha256: capture.hash.digest("hex"), bytes: capture.bytes, view, truncated: capture.chars > OUTPUT_HEAD_LIMIT + OUTPUT_TAIL_LIMIT, complete: !capture.streamError, ...(capture.streamError ? { error: capture.streamError } : {}) };
}

async function createCapture(directory: string, index: number, check: ApprovedCheck, streamName: "stdout" | "stderr"): Promise<OutputCapture> {
  const identity = createHash("sha256").update(JSON.stringify({ index, command: check.command, args: check.args ?? [], stream: streamName })).digest("hex").slice(0, 16);
  const path = join(directory, `check-${index + 1}-${identity}.${streamName}.log`);
  const stream = createWriteStream(path, { flags: "wx", mode: 0o600 });
  const capture: OutputCapture = { path, stream, hash: createHash("sha256"), bytes: 0, chars: 0, prefix: "", tail: "", decoder: new StringDecoder("utf8") };
  stream.on("error", (error: Error) => { capture.streamError = error.message; capture.source?.resume(); capture.onError?.(); });
  return capture;
}

function emptyArtifact(path: string, value: string): ArtifactRecord {
  const view = boundedOutput(value);
  return { path, sha256: createHash("sha256").update(value).digest("hex"), bytes: Buffer.byteLength(value), view: view.view, truncated: view.truncated, complete: true };
}

async function writeTextArtifact(directory: string, name: string, value: string): Promise<ArtifactRecord> {
  const path = join(directory, name);
  await writeFile(path, value, { encoding: "utf8", mode: 0o600, flag: "wx" });
  return emptyArtifact(path, value);
}

async function acquireIntegrationLock(base: string, taskId: string): Promise<string> {
  const lock = join(base, HANDOFF_DIR, ".integration-lock");
  try { await mkdir(lock, { mode: 0o700 }); }
  catch { throw new Error("another handoff integration is active; retry after it records its result"); }
  try {
    await writeFile(join(lock, "owner"), JSON.stringify({ pid: process.pid, taskId }), { encoding: "utf8", mode: 0o600, flag: "wx" });
  } catch (error) {
    await rm(lock, { recursive: true, force: true });
    throw error;
  }
  return lock;
}

async function releaseIntegrationLock(lock: string, taskId: string): Promise<void> {
  let owner: { taskId?: unknown };
  try { owner = JSON.parse(await readFile(join(lock, "owner"), "utf8")) as { taskId?: unknown }; }
  catch (error) { throw new Error(`integration lock owner is unreadable; refusing to remove lock: ${error instanceof Error ? error.message : String(error)}`); }
  if (owner.taskId !== taskId) throw new Error("integration lock owner does not match handoff; refusing to remove lock");
  await rm(lock, { recursive: true, force: true });
}

function killCheckProcess(child: ChildProcess): void {
  if (!child.pid) return;
  if (process.platform !== "win32") {
    try { process.kill(-child.pid, "SIGKILL"); return; } catch {}
  }
  try { child.kill("SIGKILL"); } catch {}
}

async function runApprovedCheck(cwd: string, check: ApprovedCheck, index: number, artifactDirectory: string, signal?: AbortSignal): Promise<CheckResult> {
  const stdout = await createCapture(artifactDirectory, index, check, "stdout");
  const stderr = await createCapture(artifactDirectory, index, check, "stderr");
  if (signal?.aborted) {
    appendCapture(stderr, Buffer.from("approved check aborted before start"));
    const [stdoutArtifact, stderrArtifact] = await Promise.all([finishCapture(stdout), finishCapture(stderr)]);
    return { ...check, code: 130, stdout: stdoutArtifact.view, stderr: stderrArtifact.view, stdoutArtifact, stderrArtifact };
  }
  return await new Promise<CheckResult>((resolve) => {
    const child = spawn(check.command, check.args ?? [], { cwd, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"] });
    let reason: "timeout" | "aborted" | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let settled = false;
    stdout.onError = () => killCheckProcess(child);
    stderr.onError = () => killCheckProcess(child);
    child.stdout?.on("data", (part: Buffer) => appendCapture(stdout, part, child.stdout!));
    child.stderr?.on("data", (part: Buffer) => appendCapture(stderr, part, child.stderr!));
    const finish = async (code: number) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", abort);
      const suffix = reason === "timeout" ? "approved check timed out" : reason === "aborted" ? "approved check aborted" : "";
      const [stdoutArtifact, stderrArtifact] = await Promise.all([finishCapture(stdout), finishCapture(stderr)]);
      const capturedStderr = suffix ? `${stderrArtifact.view}${stderrArtifact.view ? "\n" : ""}${suffix}` : stderrArtifact.view;
      const artifactError = stdout.streamError ?? stderr.streamError;
      resolve({ ...check, code: artifactError ? 1 : reason === "timeout" ? 124 : reason === "aborted" ? 130 : code, stdout: stdoutArtifact.view, stderr: capturedStderr, stdoutArtifact, stderrArtifact, ...(artifactError ? { artifactError } : {}) });
    };
    const abort = () => { if (settled) return; reason = "aborted"; killCheckProcess(child); };
    child.once("error", (error) => { appendCapture(stderr, Buffer.from(error instanceof Error ? error.message : String(error))); void finish(1); });
    child.once("close", (code) => { void finish(typeof code === "number" ? code : 1); });
    if (signal) signal.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => { if (settled) return; reason = "timeout"; killCheckProcess(child); }, check.timeoutMs ?? 120_000);
    timer.unref();
  });
}

async function runApprovedChecksInternal(workspace: HandoffManifest, checks: readonly ApprovedCheck[], signal: AbortSignal | undefined, cwd: string, artifactRoot: string, verifyPatch: boolean, validationPath: string): Promise<ApprovedChecksResult> {
  const artifactDirectory = join(artifactRoot, `validation-${randomUUID()}`);
  await mkdir(artifactDirectory, { recursive: true, mode: 0o700 });
  const results: CheckResult[] = [];
  for (const [index, check] of checks.entries()) {
    results.push(await runApprovedCheck(cwd, check, index, artifactDirectory, signal));
    if (signal?.aborted) break;
  }
  if (verifyPatch && workspace.patchSha256) {
    await git(workspace.workspace, ["add", "--all", "--", ".", ":(exclude).pi-workbench-handoff.json", ":(exclude)handoff.patch"]);
    const currentPatch = await git(workspace.workspace, ["diff", "--cached", "--binary", workspace.revision], false);
    if (createHash("sha256").update(currentPatch).digest("hex") !== workspace.patchSha256) {
      const stdoutPath = join(artifactDirectory, `check-integrity-${randomUUID()}.stdout.log`);
      const stderrPath = join(artifactDirectory, `check-integrity-${randomUUID()}.stderr.log`);
      await writeFile(stdoutPath, "", { mode: 0o600, flag: "wx" });
      const stderrText = "workspace changed after approved checks; patch was not unchanged";
      await writeFile(stderrPath, stderrText, { mode: 0o600, flag: "wx" });
      results.push({ command: "handoff-patch-integrity", code: 1, stdout: "", stderr: stderrText, stdoutArtifact: emptyArtifact(stdoutPath, ""), stderrArtifact: emptyArtifact(stderrPath, stderrText) });
    }
  }
  await writeFile(validationPath, `${JSON.stringify({ taskId: workspace.taskId, revision: workspace.revision, patchSha256: workspace.patchSha256 ?? null, cwd, artifactDirectory, results }, null, 2)}\n`, { mode: 0o600 });
  return { passed: results.length > 0 && results.every((result) => result.code === 0), results, artifactDirectory, validationPath };
}

export async function runApprovedChecks(workspace: HandoffManifest, checks: readonly ApprovedCheck[], signal?: AbortSignal): Promise<ApprovedChecksResult> {
  const artifactRoot = await ensureArtifactDirectory(workspace);
  return runApprovedChecksInternal(workspace, checks, signal, workspace.workspace, artifactRoot, true, join(workspace.workspace, "..", "validation.json"));
}

/**
 * Run post-apply checks in the target checkout while retaining their artifacts
 * under the reviewed handoff. The target patch is already identity-checked by
 * integrateHandoff, so this path does not compare the target's post-check tree
 * to the writer worktree after every check.
 */
export async function runApprovedChecksAt(cwd: string, workspace: HandoffManifest, checks: readonly ApprovedCheck[], signal?: AbortSignal): Promise<ApprovedChecksResult> {
  const artifactRoot = await ensureArtifactDirectory(workspace);
  return runApprovedChecksInternal(workspace, checks, signal, resolve(cwd), artifactRoot, false, join(artifactRoot, `post-apply-${randomUUID()}.validation.json`));
}

/**
 * Explicitly apply a reviewed handoff to its exact clean base checkout.
 * Integration is serialized by a target-local lease and never creates a
 * commit or pushes. A failed post-apply check leaves the uncommitted patch in
 * place for inspection, with the failure evidence retained beside the handoff.
 */
export async function integrateHandoff(baseCwd: string, taskId: string, checks: readonly ApprovedCheck[], signal?: AbortSignal): Promise<HandoffIntegrationResult> {
  const base = resolve(baseCwd);
  const lock = await acquireIntegrationLock(base, taskId);
  let handoff: HandoffManifest;
  try { handoff = await loadHandoff(base, taskId); }
  catch (error) { await releaseIntegrationLock(lock, taskId); throw error; }
  let artifactRoot: string;
  try { artifactRoot = await ensureArtifactDirectory(handoff); }
  catch (error) { await releaseIntegrationLock(lock, taskId); throw error; }
  const integrationDirectory = join(artifactRoot, `integration-${randomUUID()}`);
  await mkdir(integrationDirectory, { recursive: true, mode: 0o700 });
  const recordPath = join(integrationDirectory, "integration.json");
  const patchPath = handoff.patchPath!;
  const patchSnapshotPath = join(integrationDirectory, "handoff.patch");
  const targetIndexPath = join(integrationDirectory, "target-index");
  let targetRevision = "unavailable";
  let status: HandoffIntegrationResult["status"] = "rejected";
  let checksResult: ApprovedChecksResult | undefined;
  let postApplyPatchSha256: string | undefined;
  let postApplyPatchMatches: boolean | undefined;
  let error: string | undefined;
  let errorArtifact: ArtifactRecord | undefined;
  let patchApplied = false;
  try {
    if (handoff.status !== "completed") throw new Error(`handoff status ${handoff.status} is not eligible for integration`);
    if (handoff.validation !== "passed") throw new Error(`handoff validation is ${handoff.validation}; approved checks must pass before integration`);
    const review = JSON.parse(await readFile(join(handoff.workspace, "..", "review.json"), "utf8")) as Partial<ReviewRecord>;
    if (review.schemaVersion !== 1 || review.taskId !== handoff.taskId || review.revision !== handoff.revision || review.patchSha256 !== handoff.patchSha256 || review.status !== "completed" || review.reportArtifact?.complete !== true) throw new Error("review evidence is missing, failed, or does not match this handoff");
    if (checks.length === 0) throw new Error("integration requires at least one approved post-apply check");
    if (signal?.aborted) throw new Error("integration aborted before applying handoff");
    const dirty = await git(base, ["status", "--porcelain", "--untracked-files=all", "--", ".", `:(exclude)${HANDOFF_DIR}`]);
    if (dirty) throw new Error("target checkout is dirty; refusing integration to preserve unrelated work");
    targetRevision = await git(base, ["rev-parse", "HEAD"]);
    if (targetRevision !== handoff.revision) throw new Error(`target base changed: expected ${handoff.revision}, found ${targetRevision}`);
    // Reload and copy the handoff under the integration lease. Applying the
    // original path after a separate validation would permit a patch-path
    // replacement race; the private snapshot is the immutable input below.
    handoff = await loadHandoff(base, taskId);
    if (handoff.status !== "completed" || handoff.validation !== "passed") throw new Error("handoff status or validation changed while acquiring integration lease");
    const currentReview = JSON.parse(await readFile(join(handoff.workspace, "..", "review.json"), "utf8")) as Partial<ReviewRecord>;
    if (currentReview.schemaVersion !== 1 || currentReview.taskId !== handoff.taskId || currentReview.revision !== handoff.revision || currentReview.patchSha256 !== handoff.patchSha256 || currentReview.status !== "completed" || currentReview.reportArtifact?.complete !== true) throw new Error("review evidence changed while acquiring integration lease");
    const recheckedDirty = await git(base, ["status", "--porcelain", "--untracked-files=all", "--", ".", `:(exclude)${HANDOFF_DIR}`]);
    const recheckedRevision = await git(base, ["rev-parse", "HEAD"]);
    if (recheckedDirty || recheckedRevision !== targetRevision) throw new Error("target changed while acquiring integration lease");
    const patch = await readFile(handoff.patchPath!);
    if (createHash("sha256").update(patch).digest("hex") !== handoff.patchSha256) throw new Error("handoff patch changed while acquiring integration lease");
    await writeFile(patchSnapshotPath, patch, { mode: 0o600, flag: "wx" });
    await exec("git", ["-C", base, "apply", "--check", "--binary", patchSnapshotPath], { maxBuffer: 2 * 1024 * 1024 });
    await gitWithEnv(base, ["read-tree", handoff.revision], { GIT_INDEX_FILE: targetIndexPath });
    await exec("git", ["-C", base, "apply", "--cached", "--binary", patchSnapshotPath], { maxBuffer: 2 * 1024 * 1024, env: { ...process.env, GIT_INDEX_FILE: targetIndexPath } });
    await exec("git", ["-C", base, "apply", "--binary", patchSnapshotPath], { maxBuffer: 2 * 1024 * 1024 });
    patchApplied = true;
    const appliedPatch = await gitWithEnv(base, ["diff", "--cached", "--binary", handoff.revision], { GIT_INDEX_FILE: targetIndexPath }, false);
    if (createHash("sha256").update(appliedPatch).digest("hex") !== handoff.patchSha256) throw new Error("applied target patch identity does not match reviewed handoff");
    checksResult = await runApprovedChecksAt(base, handoff, checks, signal);
    const finalRevision = await git(base, ["rev-parse", "HEAD"]);
    if (finalRevision !== targetRevision) throw new Error(`target revision changed during post-apply checks: expected ${targetRevision}, found ${finalRevision}`);
    await gitWithEnv(base, ["add", "--all", "--", ".", `:(exclude)${HANDOFF_DIR}`], { GIT_INDEX_FILE: targetIndexPath });
    const postApplyPatch = await gitWithEnv(base, ["diff", "--cached", "--binary", handoff.revision], { GIT_INDEX_FILE: targetIndexPath }, false);
    postApplyPatchSha256 = createHash("sha256").update(postApplyPatch).digest("hex");
    postApplyPatchMatches = postApplyPatchSha256 === handoff.patchSha256;
    if (!checksResult.passed) {
      status = "failed";
      throw new Error("post-apply approved checks failed; applied changes were retained for inspection");
    }
    if (!postApplyPatchMatches) throw new Error("post-apply checks changed the target beyond the reviewed patch; changes were retained for inspection");
    status = "applied";
  } catch (caught) {
    error = caught instanceof Error ? caught.message : String(caught);
    if (status !== "failed") status = patchApplied ? "failed" : error.includes("target base changed") || error.includes("target changed") || error.includes("dirty") || error.includes("apply") ? "conflict" : "rejected";
    try { errorArtifact = await writeTextArtifact(integrationDirectory, "error.txt", error); } catch (artifactError) { error = `${error}; integration error artifact failed: ${artifactError instanceof Error ? artifactError.message : String(artifactError)}`; }
  }
  const result: HandoffIntegrationResult = { schemaVersion: 1, taskId, target: base, expectedRevision: handoff.revision, targetRevision, patchPath, patchSnapshotPath, patchSha256: handoff.patchSha256!, status, recordPath, ...(checksResult ? { checks: checksResult } : {}), ...(postApplyPatchSha256 ? { postApplyPatchSha256 } : {}), ...(postApplyPatchMatches !== undefined ? { postApplyPatchMatches } : {}), ...(errorArtifact ? { errorArtifact } : {}), ...(error ? { error } : {}), recordedAt: new Date().toISOString() };
  await writeFile(recordPath, `${JSON.stringify(result, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
  if (lock) await releaseIntegrationLock(lock, taskId);
  return result;
}
