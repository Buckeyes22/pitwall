import { createHash, randomUUID } from "node:crypto";
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { lstatSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { assertTaskArtifactPath, type TaskArtifactKind } from "./artifact-containment.js";

export type ExecutionState = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted" | "waiting";
export type AcceptanceState = "unchecked" | "checks-passed" | "reviewed" | "accepted" | "rejected";

export type TaskArtifact = {
  path: string;
  sha256: string;
  bytes: number;
  complete: true;
  view: string;
  truncated: boolean;
};

export type TaskProfile = { name: string; provider: string; modelId: string; api: string; endpoint: string; resourceGroup: string; credentialClass: "keyless" | "api-key-env"; accountRef?: string };
export type InstructionProvenance = { source: "resource-loader" | "public-context-files" | "final-system-prompt"; complete: boolean; reason?: string; files: Array<{ path: string; sha256: string; bytes: number; estimated: boolean }>; inline?: Array<{ source: "customPrompt" | "appendSystemPrompt"; sha256: string; bytes: number }>; promptSha256?: string; promptBytes?: number };
export type TaskEvidenceLink = {
  taskId: string;
  runId: string;
  attemptId: string;
  workspace: string;
  revision: string;
};
export type TaskVerification = {
  checkId: string;
  exitCode: number;
  evidencePath: string;
  evidence: TaskEvidenceLink;
};
export type TaskUsageSource = "provider-reported" | "estimated" | "unavailable";
export type TaskUsage = {
  source: TaskUsageSource;
  inputTokens: number | null;
  outputTokens: number | null;
  reasoningTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  totalTokens: number | null;
  provenance: TaskEvidenceLink;
};
export type TaskHandoffEvidence = {
  handoffId: string;
  patchPath: string;
  patchSha256: string;
  evidence: TaskEvidenceLink;
};
export type TaskVerificationInput = Omit<TaskVerification, "evidence">;
export type TaskHandoffEvidenceInput = Omit<TaskHandoffEvidence, "evidence">;
export type TaskUsageInput = {
  source: TaskUsageSource;
  inputTokens?: number | null;
  outputTokens?: number | null;
  reasoningTokens?: number | null;
  cacheReadTokens?: number | null;
  cacheWriteTokens?: number | null;
  totalTokens?: number | null;
};
export type TaskEnvelope = {
  schemaVersion: 1;
  taskId: string;
  role: "scout" | "worker" | "reviewer";
  profile: TaskProfile;
  task: string;
  acceptance?: string;
  scope?: string;
  constraints: string[];
  inputs: Record<string, string>;
  validationProfile: string;
  workspacePolicy: string;
  contextPolicy: string;
};

export type TaskRecord = {
  schemaVersion: 1;
  taskId: string;
  runId: string;
  attemptId: string;
  role: "scout" | "worker" | "reviewer";
  parentSessionId: string;
  parentSessionFile?: string;
  parentInstructionProvenancePath?: string;
  ownerPid?: number;
  ownerStartTime?: string;
  childId?: string;
  backendSessionFile?: string;
  backendOutputFile?: string;
  rootSessionId?: string;
  profile: TaskProfile;
  workspace: string;
  baseRevision: string;
  task: string;
  acceptance?: string;
  scope?: string;
  executionState: ExecutionState;
  acceptanceState: AcceptanceState;
  summary?: string;
  error?: string;
  changedFiles: string[];
  verification: TaskVerification[];
  blockers: string[];
  risks: string[];
  usage: TaskUsage;
  handoff?: TaskHandoffEvidence;
  artifacts: { result?: TaskArtifact; summary?: TaskArtifact };
  startedAt: string;
  completedAt?: string;
  updatedAt: string;
  envelope: TaskEnvelope;
  instructionProvenance?: InstructionProvenance;
};

export type TaskRecordCreate = Pick<TaskRecord, "role" | "profile" | "workspace" | "task"> & {
  parentSessionId?: string;
  parentSessionFile?: string;
  parentInstructionProvenancePath?: string;
  baseRevision?: string;
  acceptance?: string;
  scope?: string;
  instructionProvenance?: InstructionProvenance;
  changedFiles?: string[];
  verification?: TaskVerificationInput[];
  blockers?: string[];
  risks?: string[];
  usage?: TaskUsageInput;
  handoff?: TaskHandoffEvidenceInput;
};
export type TaskRecordFinish = {
  executionState: Exclude<ExecutionState, "queued" | "running">;
  acceptanceState?: AcceptanceState;
  result?: string;
  error?: string;
  changedFiles?: string[];
  verification?: TaskVerificationInput[];
  blockers?: string[];
  risks?: string[];
  usage?: TaskUsageInput;
  handoff?: TaskHandoffEvidenceInput;
};

function envelopeFor(input: TaskRecordCreate, taskId: string): TaskEnvelope {
  return {
    schemaVersion: 1, taskId, role: input.role, profile: input.profile, task: input.task,
    ...(input.acceptance ? { acceptance: input.acceptance } : {}), ...(input.scope ? { scope: input.scope } : {}),
    constraints: ["single native child", "no unmanaged child spawning"], inputs: {},
    validationProfile: input.role === "worker" ? "approved-checks" : "read-only-observation",
    workspacePolicy: input.role === "worker" ? "isolated-handoff" : "parent-workspace-read-only",
    contextPolicy: "isolated-without-parent-transcript",
  };
}

function isObject(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function requiredString(value: unknown, field: string): string { if (typeof value !== "string" || !value) throw new Error(`invalid task record ${field}`); return value; }
function optionalString(value: unknown, field: string): void { if (value !== undefined && (typeof value !== "string" || !value)) throw new Error(`invalid task record ${field}`); }
function processStartTime(pid: number): string | undefined {
  if (process.platform !== "linux") return undefined;
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    const endOfCommand = stat.lastIndexOf(") ");
    if (endOfCommand < 0) return undefined;
    const fields = stat.slice(endOfCommand + 2).trim().split(/\s+/);
    return fields[19] || undefined;
  } catch { return undefined; }
}
export function ownerAlive(pid: number, expectedStartTime?: string, startTimeReader: () => string | undefined = () => processStartTime(pid)): boolean {
  try { process.kill(pid, 0); } catch (error) { return (error as NodeJS.ErrnoException).code !== "ESRCH"; }
  if (process.platform !== "linux") return true;
  try {
    if (/^State:\s+Z/m.test(readFileSync(`/proc/${pid}/status`, "utf8"))) return false;
    // Missing start metadata leaves process identity uncertain.  Keep the
    // record owned in that case; recovering it as stale could let a second
    // process write over a live/unknown owner.
    const actualStartTime = startTimeReader();
    return actualStartTime === undefined || expectedStartTime === undefined || actualStartTime === expectedStartTime;
  } catch (error) { return (error as NodeJS.ErrnoException).code !== "ENOENT"; }
}
function requiredTimestamp(value: unknown, field: string): void {
  const text = requiredString(value, field);
  // Records are emitted with Date#toISOString(); accepting Date.parse's broad
  // grammar would admit values such as "1" and non-UTC local dates.
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(text) || Number.isNaN(Date.parse(text)) || new Date(text).toISOString() !== text) throw new Error(`invalid task record ${field}`);
}
function validateArtifact(value: unknown, field: string, artifactRoot?: string, taskId?: string): void {
  if (!isObject(value)) throw new Error(`invalid task record ${field}`);
  const path = requiredString(value.path, `${field}.path`);
  if (artifactRoot !== undefined && taskId !== undefined) {
    const kind = field === "artifacts.result" ? "result" : field === "artifacts.summary" ? "summary" : undefined;
    if (!kind) throw new Error(`invalid task record ${field}`);
    try { assertTaskArtifactPath(path, { root: artifactRoot, taskId, kind: kind as TaskArtifactKind }); }
    catch (error) { throw new Error(`invalid task record ${field}.path: ${error instanceof Error ? error.message : String(error)}`); }
  }
  if (typeof value.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.sha256)) throw new Error(`invalid task record ${field}.sha256`);
  if (typeof value.bytes !== "number" || !Number.isSafeInteger(value.bytes) || value.bytes < 0) throw new Error(`invalid task record ${field}.bytes`);
  if (value.complete !== true || typeof value.view !== "string" || typeof value.truncated !== "boolean") throw new Error(`invalid task record ${field}`);
  let contents: Buffer;
  try { contents = readFileSync(path); }
  catch { throw new Error(`invalid task record ${field}: artifact content is unavailable`); }
  if (contents.byteLength !== value.bytes) throw new Error(`invalid task record ${field}.bytes: artifact content length does not match metadata`);
  if (createHash("sha256").update(contents).digest("hex") !== value.sha256) throw new Error(`invalid task record ${field}.sha256: artifact content hash does not match metadata`);
}
function validateInstructionProvenance(value: unknown): void {
  if (!isObject(value) || !["resource-loader", "public-context-files", "final-system-prompt"].includes(String(value.source)) || typeof value.complete !== "boolean" || !Array.isArray(value.files)) throw new Error("invalid task record instructionProvenance");
  if (value.reason !== undefined) optionalString(value.reason, "instructionProvenance.reason");
  for (const file of value.files) {
    if (!isObject(file) || typeof file.path !== "string" || !file.path || typeof file.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(file.sha256) || typeof file.bytes !== "number" || !Number.isSafeInteger(file.bytes) || file.bytes < 0 || typeof file.estimated !== "boolean") throw new Error("invalid task record instructionProvenance file");
  }
  if (value.promptSha256 !== undefined && (typeof value.promptSha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.promptSha256))) throw new Error("invalid task record instructionProvenance.promptSha256");
  if (value.promptBytes !== undefined && (typeof value.promptBytes !== "number" || !Number.isSafeInteger(value.promptBytes) || value.promptBytes < 0)) throw new Error("invalid task record instructionProvenance.promptBytes");
  if (value.inline !== undefined && (!Array.isArray(value.inline) || value.inline.some((entry) => !isObject(entry) || !["customPrompt", "appendSystemPrompt"].includes(String(entry.source)) || typeof entry.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(entry.sha256) || typeof entry.bytes !== "number" || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0))) throw new Error("invalid task record instructionProvenance.inline");
}

const terminalExecutionStates = new Set<ExecutionState>(["succeeded", "failed", "cancelled", "interrupted"]);
const usageSources = new Set<TaskUsageSource>(["provider-reported", "estimated", "unavailable"]);

function evidenceLinkFor(record: Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">): TaskEvidenceLink {
  return { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: record.workspace, revision: record.baseRevision };
}

function validateEvidenceLink(value: unknown, field: string, record: Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">): void {
  if (!isObject(value)) throw new Error(`invalid task record ${field}`);
  for (const key of ["taskId", "runId", "attemptId", "workspace", "revision"] as const) requiredString(value[key], `${field}.${key}`);
  if (value.taskId !== record.taskId || value.runId !== record.runId || value.attemptId !== record.attemptId || value.workspace !== record.workspace || value.revision !== record.baseRevision) throw new Error(`invalid task record ${field} identity`);
}

function validateNonNegativeToken(value: unknown, field: string): void {
  if (value !== null && (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)) throw new Error(`invalid task record ${field}`);
}

function assertOwnedEvidencePath(path: string, record: Pick<TaskRecord, "workspace">, field: string): void {
  if (!isAbsolute(path)) throw new Error(`invalid task record ${field}.evidencePath: evidence path must be absolute`);
  const workspace = resolve(record.workspace);
  const workspaceParent = dirname(workspace);
  const lexicalRoot = basename(workspace) === "worktree" && basename(workspaceParent).startsWith("task-") ? workspaceParent : workspace;
  const lexicalDistance = relative(lexicalRoot, resolve(path));
  if (lexicalDistance === ".." || lexicalDistance.startsWith(`..${sep}`) || isAbsolute(lexicalDistance)) throw new Error(`invalid task record ${field}.evidencePath: evidence path escapes task workspace`);
  let root: string;
  let resolved: string;
  try {
    root = realpathSync(lexicalRoot);
    resolved = realpathSync(path);
  } catch {
    throw new Error(`invalid task record ${field}.evidencePath: evidence path is unavailable`);
  }
  const distance = relative(root, resolved);
  if (distance === ".." || distance.startsWith(`..${sep}`) || isAbsolute(distance)) throw new Error(`invalid task record ${field}.evidencePath: evidence path escapes task workspace`);
  let stat;
  try { stat = lstatSync(path); } catch { throw new Error(`invalid task record ${field}.evidencePath: evidence path is unavailable`); }
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`invalid task record ${field}.evidencePath: evidence path must be a regular file`);
}

function validateTaskResult(value: Record<string, unknown>, record: Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">): void {
  if (!Array.isArray(value.changedFiles) || value.changedFiles.some((entry) => typeof entry !== "string" || !entry)) throw new Error("invalid task record changedFiles");
  if (!Array.isArray(value.blockers) || value.blockers.some((entry) => typeof entry !== "string" || !entry)) throw new Error("invalid task record blockers");
  if (!Array.isArray(value.risks) || value.risks.some((entry) => typeof entry !== "string" || !entry)) throw new Error("invalid task record risks");
  if (!Array.isArray(value.verification)) throw new Error("invalid task record verification");
  for (const [index, check] of value.verification.entries()) {
    const field = `verification[${index}]`;
    if (!isObject(check)) throw new Error(`invalid task record ${field}`);
    requiredString(check.checkId, `${field}.checkId`);
    if (typeof check.exitCode !== "number" || !Number.isSafeInteger(check.exitCode) || check.exitCode < 0) throw new Error(`invalid task record ${field}.exitCode`);
    const evidencePath = requiredString(check.evidencePath, `${field}.evidencePath`);
    assertOwnedEvidencePath(evidencePath, record, field);
    validateEvidenceLink(check.evidence, `${field}.evidence`, record);
  }
  if (!isObject(value.usage) || !usageSources.has(value.usage.source as TaskUsageSource)) throw new Error("invalid task record usage");
  for (const field of ["inputTokens", "outputTokens", "reasoningTokens", "cacheReadTokens", "cacheWriteTokens", "totalTokens"] as const) validateNonNegativeToken(value.usage[field], `usage.${field}`);
  validateEvidenceLink(value.usage.provenance, "usage.provenance", record);
  if (value.handoff !== undefined) {
    if (!isObject(value.handoff)) throw new Error("invalid task record handoff");
    requiredString(value.handoff.handoffId, "handoff.handoffId");
    const patchPath = requiredString(value.handoff.patchPath, "handoff.patchPath");
    assertOwnedEvidencePath(patchPath, record, "handoff");
    if (typeof value.handoff.patchSha256 !== "string" || !/^[0-9a-f]{64}$/.test(value.handoff.patchSha256)) throw new Error("invalid task record handoff.patchSha256");
    if (createHash("sha256").update(readFileSync(patchPath)).digest("hex") !== value.handoff.patchSha256) throw new Error("invalid task record handoff.patchSha256: patch hash mismatch");
    validateEvidenceLink(value.handoff.evidence, "handoff.evidence", record);
  }
}

function normalizedVerification(record: Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">, verification: TaskVerificationInput[] | undefined): TaskVerification[] {
  const evidence = evidenceLinkFor(record);
  return (verification ?? []).map((check) => ({ ...check, evidence }));
}

function normalizedUsage(record: Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">, usage: TaskUsageInput | undefined): TaskUsage {
  return {
    source: usage?.source ?? "unavailable",
    inputTokens: usage?.inputTokens ?? null,
    outputTokens: usage?.outputTokens ?? null,
    reasoningTokens: usage?.reasoningTokens ?? null,
    cacheReadTokens: usage?.cacheReadTokens ?? null,
    cacheWriteTokens: usage?.cacheWriteTokens ?? null,
    totalTokens: usage?.totalTokens ?? null,
    provenance: evidenceLinkFor(record),
  };
}

function normalizedHandoff(record: Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">, handoff: TaskHandoffEvidenceInput | undefined): TaskHandoffEvidence | undefined {
  return handoff ? { ...handoff, evidence: evidenceLinkFor(record) } : undefined;
}

export function validateTaskRecord(value: unknown, expectedTaskId?: string, artifactRoot?: string): TaskRecord {
  if (!isObject(value) || value.schemaVersion !== 1) throw new Error("invalid task record schemaVersion");
  const taskId = requiredString(value.taskId, "taskId");
  if (expectedTaskId !== undefined && taskId !== expectedTaskId) throw new Error("task record identity mismatch");
  if (!/^task-[A-Za-z0-9_-]+$/.test(taskId) || !/^run-[A-Za-z0-9_-]+$/.test(requiredString(value.runId, "runId")) || !/^attempt-[A-Za-z0-9_-]+$/.test(requiredString(value.attemptId, "attemptId"))) throw new Error("invalid task record lineage identity");
  if (!["scout", "worker", "reviewer"].includes(String(value.role))) throw new Error("invalid task record role");
  if (!["queued", "running", "succeeded", "failed", "cancelled", "interrupted", "waiting"].includes(String(value.executionState))) throw new Error("invalid task record executionState");
  if (!["unchecked", "checks-passed", "reviewed", "accepted", "rejected"].includes(String(value.acceptanceState))) throw new Error("invalid task record acceptanceState");
  requiredString(value.parentSessionId, "parentSessionId"); optionalString(value.parentSessionFile, "parentSessionFile"); optionalString(value.parentInstructionProvenancePath, "parentInstructionProvenancePath");
  if (value.ownerPid !== undefined && (typeof value.ownerPid !== "number" || !Number.isSafeInteger(value.ownerPid) || value.ownerPid <= 0)) throw new Error("invalid task record ownerPid");
  optionalString(value.ownerStartTime, "ownerStartTime");
  if (value.ownerStartTime !== undefined && value.ownerPid === undefined) throw new Error("invalid task record owner identity");
  optionalString(value.childId, "childId"); optionalString(value.backendSessionFile, "backendSessionFile"); optionalString(value.backendOutputFile, "backendOutputFile"); optionalString(value.rootSessionId, "rootSessionId"); requiredString(value.workspace, "workspace"); requiredString(value.baseRevision, "baseRevision"); requiredString(value.task, "task"); optionalString(value.acceptance, "acceptance"); optionalString(value.scope, "scope"); optionalString(value.summary, "summary"); optionalString(value.error, "error");
  requiredTimestamp(value.startedAt, "startedAt"); requiredTimestamp(value.updatedAt, "updatedAt"); if (value.completedAt !== undefined) requiredTimestamp(value.completedAt, "completedAt");
  if (Date.parse(value.updatedAt as string) < Date.parse(value.startedAt as string)) throw new Error("invalid task record updatedAt ordering");
  if (value.completedAt !== undefined && (Date.parse(value.completedAt as string) < Date.parse(value.startedAt as string) || Date.parse(value.completedAt as string) > Date.parse(value.updatedAt as string))) throw new Error("invalid task record completedAt ordering");
  if (terminalExecutionStates.has(value.executionState as ExecutionState) && value.completedAt === undefined) throw new Error("invalid task record terminal completion");
  if (!isObject(value.profile) || !["keyless", "api-key-env"].includes(String(value.profile.credentialClass))) throw new Error("invalid task record profile");
  for (const field of ["name", "provider", "modelId", "api", "endpoint", "resourceGroup"]) requiredString(value.profile[field], `profile.${field}`);
  if (value.profile.accountRef !== undefined) requiredString(value.profile.accountRef, "profile.accountRef");
  if (!isObject(value.artifacts)) throw new Error("invalid task record artifacts");
  if (value.artifacts.result !== undefined) validateArtifact(value.artifacts.result, "artifacts.result", artifactRoot, taskId);
  if (value.artifacts.summary !== undefined) validateArtifact(value.artifacts.summary, "artifacts.summary", artifactRoot, taskId);
  validateTaskResult(value, value as unknown as Pick<TaskRecord, "taskId" | "runId" | "attemptId" | "workspace" | "baseRevision">);
  if (value.instructionProvenance !== undefined) validateInstructionProvenance(value.instructionProvenance);
  if (!isObject(value.envelope) || value.envelope.schemaVersion !== 1 || value.envelope.taskId !== taskId || value.envelope.role !== value.role || !isObject(value.envelope.profile)) throw new Error("invalid task record envelope");
  if (requiredString(value.envelope.task, "envelope.task") !== value.task) throw new Error("invalid task record envelope task"); requiredString(value.envelope.validationProfile, "envelope.validationProfile"); requiredString(value.envelope.workspacePolicy, "envelope.workspacePolicy"); requiredString(value.envelope.contextPolicy, "envelope.contextPolicy");
  if ((value.envelope.acceptance ?? undefined) !== (value.acceptance ?? undefined) || (value.envelope.scope ?? undefined) !== (value.scope ?? undefined)) throw new Error("invalid task record envelope scope");
  if (value.envelope.validationProfile !== (value.role === "worker" ? "approved-checks" : "read-only-observation") || value.envelope.workspacePolicy !== (value.role === "worker" ? "isolated-handoff" : "parent-workspace-read-only") || value.envelope.contextPolicy !== "isolated-without-parent-transcript") throw new Error("invalid task record envelope policy");
  if (!Array.isArray(value.envelope.constraints) || value.envelope.constraints.some((entry) => typeof entry !== "string") || !isObject(value.envelope.inputs) || Object.entries(value.envelope.inputs).some(([key, entry]) => !key || typeof entry !== "string")) throw new Error("invalid task record envelope inputs");
  if (JSON.stringify(value.envelope.profile) !== JSON.stringify(value.profile)) throw new Error("invalid task record envelope profile");
  return value as unknown as TaskRecord;
}

const VIEW_HEAD = 2_000;
const VIEW_TAIL = 2_000;

export function compactTaskText(text: string): { view: string; truncated: boolean } {
  if (text.length <= VIEW_HEAD + VIEW_TAIL) return { view: text, truncated: false };
  return { view: `${text.slice(0, VIEW_HEAD)}\n…[truncated; complete result is in the artifact]…\n${text.slice(-VIEW_TAIL)}`, truncated: true };
}

export function resolveBaseRevision(cwd: string): string {
  try {
    const revision = execFileSync("git", ["-C", cwd, "rev-parse", "HEAD"], { encoding: "utf8", timeout: 5_000, stdio: ["ignore", "pipe", "ignore"] }).trim();
    return revision || "unavailable";
  } catch {
    return "unavailable";
  }
}

async function atomicWrite(path: string, value: string): Promise<void> {
  const temporary = `${path}.${randomUUID()}.tmp`;
  await writeFile(temporary, value, { encoding: "utf8", mode: 0o600, flag: "wx" });
  await rename(temporary, path);
}

export class TaskRecordStore {
  readonly directory: string;
  private readonly mutations = new Map<string, Promise<void>>();

  constructor(root: string) {
    this.directory = join(resolve(root), "task-records");
  }

  private path(taskId: string): string {
    if (!/^[A-Za-z0-9_-]+$/.test(taskId)) throw new Error("invalid task record id");
    return join(this.directory, `${taskId}.json`);
  }

  private enqueue<T>(taskId: string, operation: () => Promise<T>): Promise<T> {
    const previous = this.mutations.get(taskId) ?? Promise.resolve();
    const run = previous.then(operation, operation);
    const settled = run.then(() => undefined, () => undefined);
    this.mutations.set(taskId, settled);
    return run.finally(() => {
      if (this.mutations.get(taskId) === settled) this.mutations.delete(taskId);
    });
  }

  private async read(taskId: string): Promise<TaskRecord | undefined> {
    try { return validateTaskRecord(JSON.parse(await readFile(this.path(taskId), "utf8")), taskId, this.directory); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw error; }
  }

  private async updateUnlocked(taskId: string, patch: Partial<Omit<TaskRecord, "schemaVersion" | "taskId" | "runId" | "attemptId" | "startedAt">>): Promise<TaskRecord> {
    const current = await this.read(taskId);
    if (!current) throw new Error(`task record not found: ${taskId}`);
    // A terminal state is immutable.  Detached child callbacks can arrive
    // after a deadline/cancellation. Preserve the terminal result, while
    // allowing the callback to attach the child identity/provenance that is
    // needed to inspect the detached process after the deadline.
    if (terminalExecutionStates.has(current.executionState)) {
      const metadata: Partial<TaskRecord> = {};
      if (current.childId === undefined && patch.childId !== undefined) metadata.childId = patch.childId;
      if (current.instructionProvenance === undefined && patch.instructionProvenance !== undefined) metadata.instructionProvenance = patch.instructionProvenance;
      const patchKeys = Object.keys(patch);
      if (patchKeys.some((key) => key !== "childId" && key !== "instructionProvenance") || Object.keys(metadata).length === 0) return current;
      const updated = { ...current, ...metadata, updatedAt: new Date().toISOString() } as TaskRecord;
      validateTaskRecord(updated, taskId, this.directory);
      await atomicWrite(this.path(taskId), `${JSON.stringify(updated, null, 2)}\n`);
      return updated;
    }
    const updated = { ...current, ...patch, updatedAt: new Date().toISOString() } as TaskRecord;
    for (const field of ["taskId", "runId", "attemptId", "startedAt", "parentSessionId", "ownerPid", "ownerStartTime", "workspace", "baseRevision"] as const) {
      if (updated[field] !== current[field]) throw new Error(`task record ${field} is immutable`);
    }
    // Validate the complete candidate before replacing the last known-good
    // record. In particular, envelope/result identity must not be allowed to
    // make a record unreadable.
    validateTaskRecord(updated, taskId, this.directory);
    await atomicWrite(this.path(taskId), `${JSON.stringify(updated, null, 2)}\n`);
    return updated;
  }

  async create(input: TaskRecordCreate): Promise<TaskRecord> {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const now = new Date().toISOString();
    const taskId = `task-${randomUUID()}`;
    const runId = `run-${randomUUID()}`;
    const attemptId = `attempt-${randomUUID()}`;
    const workspace = resolve(input.workspace);
    const baseRevision = input.baseRevision ?? resolveBaseRevision(input.workspace);
    const record: TaskRecord = {
      schemaVersion: 1,
      taskId,
      runId,
      attemptId,
      role: input.role,
      parentSessionId: input.parentSessionId ?? "unavailable",
      ownerPid: process.pid,
      ...(processStartTime(process.pid) ? { ownerStartTime: processStartTime(process.pid) } : {}),
      ...(input.parentSessionFile ? { parentSessionFile: input.parentSessionFile } : {}),
      ...(input.parentInstructionProvenancePath ? { parentInstructionProvenancePath: input.parentInstructionProvenancePath } : {}),
      profile: input.profile,
      workspace,
      baseRevision,
      task: input.task,
      ...(input.acceptance ? { acceptance: input.acceptance } : {}),
      ...(input.scope ? { scope: input.scope } : {}),
      executionState: "queued",
      acceptanceState: "unchecked",
      changedFiles: input.changedFiles ?? [],
      verification: [],
      blockers: input.blockers ?? [],
      risks: input.risks ?? [],
      usage: {} as TaskUsage,
      ...(input.handoff ? { handoff: normalizedHandoff({ taskId, runId, attemptId, workspace, baseRevision }, input.handoff) } : {}),
      artifacts: {},
      startedAt: now,
      updatedAt: now,
      envelope: envelopeFor(input, taskId),
      ...(input.instructionProvenance ? { instructionProvenance: input.instructionProvenance } : {}),
    };
    record.verification = normalizedVerification(record, input.verification);
    record.usage = normalizedUsage(record, input.usage);
    record.handoff = normalizedHandoff(record, input.handoff);
    if (!record.handoff) delete record.handoff;
    validateTaskRecord(record, taskId, this.directory);
    await atomicWrite(this.path(taskId), `${JSON.stringify(record, null, 2)}\n`);
    return record;
  }

  createSync(input: TaskRecordCreate): TaskRecord {
    mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    const now = new Date().toISOString();
    const taskId = `task-${randomUUID()}`;
    const runId = `run-${randomUUID()}`;
    const attemptId = `attempt-${randomUUID()}`;
    const workspace = resolve(input.workspace);
    const baseRevision = input.baseRevision ?? resolveBaseRevision(input.workspace);
    const record: TaskRecord = {
      schemaVersion: 1, taskId, runId, attemptId,
      role: input.role, parentSessionId: input.parentSessionId ?? "unavailable",
      ownerPid: process.pid, ...(processStartTime(process.pid) ? { ownerStartTime: processStartTime(process.pid) } : {}),
      ...(input.parentSessionFile ? { parentSessionFile: input.parentSessionFile } : {}), profile: input.profile,
      ...(input.parentInstructionProvenancePath ? { parentInstructionProvenancePath: input.parentInstructionProvenancePath } : {}),
      workspace, baseRevision, task: input.task,
      ...(input.acceptance ? { acceptance: input.acceptance } : {}), ...(input.scope ? { scope: input.scope } : {}),
      executionState: "running", acceptanceState: "unchecked", changedFiles: input.changedFiles ?? [], verification: [], blockers: input.blockers ?? [], risks: input.risks ?? [], usage: {} as TaskUsage, artifacts: {}, startedAt: now, updatedAt: now,
      envelope: envelopeFor(input, taskId),
      ...(input.instructionProvenance ? { instructionProvenance: input.instructionProvenance } : {}),
    };
    record.verification = normalizedVerification(record, input.verification);
    record.usage = normalizedUsage(record, input.usage);
    record.handoff = normalizedHandoff(record, input.handoff);
    if (!record.handoff) delete record.handoff;
    validateTaskRecord(record, taskId, this.directory);
    writeFileSync(this.path(taskId), `${JSON.stringify(record, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
    return record;
  }

  async get(taskId: string): Promise<TaskRecord | undefined> {
    return this.read(taskId);
  }

  async recoverStale(taskId: string): Promise<TaskRecord | undefined> {
    return this.enqueue(taskId, async () => {
      const record = await this.read(taskId);
      if (!record || !record.ownerPid || ownerAlive(record.ownerPid, record.ownerStartTime) || !["queued", "running", "waiting"].includes(record.executionState)) return record;
      return this.updateUnlocked(taskId, {
        executionState: "interrupted",
        acceptanceState: "rejected",
        error: `owning process ${record.ownerPid} is no longer alive; detached child termination is unconfirmed; resume is unsupported`,
        completedAt: new Date().toISOString(),
      });
    });
  }

  async update(taskId: string, patch: Partial<Omit<TaskRecord, "schemaVersion" | "taskId" | "runId" | "attemptId" | "startedAt">>): Promise<TaskRecord> {
    return this.enqueue(taskId, () => this.updateUnlocked(taskId, patch));
  }

  private async artifactUnlocked(taskId: string, kind: "result" | "summary", text: string): Promise<TaskArtifact> {
    const record = await this.read(taskId);
    if (!record) throw new Error(`task record not found: ${taskId}`);
    // A terminal record is immutable: refuse before any artifact file is created.
    if (terminalExecutionStates.has(record.executionState)) throw new Error(`task record ${taskId} is ${record.executionState}; a late ${kind} artifact is refused`);
    const hash = createHash("sha256").update(text).digest("hex");
    const path = join(this.directory, `${taskId}.${kind}-${hash.slice(0, 16)}.txt`);
    try { await writeFile(path, text, { encoding: "utf8", mode: 0o600, flag: "wx" }); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      const existing = await readFile(path, "utf8");
      if (createHash("sha256").update(existing).digest("hex") !== hash) throw new Error("task artifact collision");
    }
    const compact = compactTaskText(text);
    const artifact: TaskArtifact = { path, sha256: hash, bytes: Buffer.byteLength(text), complete: true, view: compact.view, truncated: compact.truncated };
    await this.updateUnlocked(taskId, { artifacts: { ...record.artifacts, [kind]: artifact }, ...(kind === "summary" ? { summary: compact.view } : {}) });
    return artifact;
  }

  async artifact(taskId: string, kind: "result" | "summary", text: string): Promise<TaskArtifact> {
    return this.enqueue(taskId, () => this.artifactUnlocked(taskId, kind, text));
  }

  async finish(taskId: string, input: TaskRecordFinish): Promise<TaskRecord> {
    return this.enqueue(taskId, async () => {
      let record = await this.read(taskId);
      if (!record) throw new Error(`task record not found: ${taskId}`);
      if (terminalExecutionStates.has(record.executionState)) return record;
      if (input.result !== undefined) {
        await this.artifactUnlocked(taskId, "result", input.result);
        await this.artifactUnlocked(taskId, "summary", input.result);
        record = await this.read(taskId) as TaskRecord;
      }
      return this.updateUnlocked(taskId, {
        executionState: input.executionState,
        ...(input.acceptanceState ? { acceptanceState: input.acceptanceState } : {}),
        ...(input.error ? { error: input.error } : {}),
        ...(input.changedFiles ? { changedFiles: input.changedFiles } : {}),
        ...(input.verification ? { verification: normalizedVerification(record, input.verification) } : {}),
        ...(input.blockers ? { blockers: input.blockers } : {}),
        ...(input.risks ? { risks: input.risks } : {}),
        ...(input.usage ? { usage: normalizedUsage(record, input.usage) } : {}),
        ...(input.handoff ? { handoff: normalizedHandoff(record, input.handoff) } : {}),
        completedAt: new Date().toISOString(),
        ...(record.summary ? { summary: record.summary } : {}),
      });
    });
  }

  async list(limit = 20): Promise<TaskRecord[]> {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const names = (await readdir(this.directory)).filter((name) => name.endsWith(".json"));
    const records: TaskRecord[] = [];
    for (const name of names) {
      records.push(validateTaskRecord(JSON.parse(await readFile(join(this.directory, name), "utf8")), name.slice(0, -5), this.directory));
    }
    return records.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, limit);
  }
}
