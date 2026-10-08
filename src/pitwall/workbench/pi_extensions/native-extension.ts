import { readFile, writeFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { Type, type Static } from "@earendil-works/pi-ai";
import { createBashTool, type ExtensionAPI, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { registerAdmissionProvider } from "./provider-extension.js";
import { capturePatch, createHandoffWorkspace, integrateHandoff, loadHandoff, recordReview, runApprovedChecks, updateValidation, type ApprovedCheck, type HandoffManifest, type HandoffWorkspace } from "./workspace.js";
import { TaskRecordStore, type AcceptanceState, type InstructionProvenance, type TaskRecord } from "./task-record.js";
import { applySoloToolDeadline } from "./timeouts.js";
import { validatePlanningRoleProfiles } from "./native-profile.js";
import { restrictedBashOperations, restrictedPathsFromEnvironment } from "./restricted.js";
const TINTIN_RPC_PREFIX = "subagents:rpc:";
const MAX_LAUNCHES_PER_TURN = 4;
export type ChildResponse = { requestId: string; status: string; childId?: string; backendRecord?: { sessionFile?: string; outputFile?: string; rootSessionId?: string }; error?: string; result?: { kind: string; text?: string } };
type TintinBeforeToolContext = { toolCall: { name: string; arguments?: unknown }; args: unknown };
type TintinSession = {
  agent?: {
    beforeToolCall?: (context: TintinBeforeToolContext, signal?: AbortSignal) => Promise<unknown>;
    state?: { tools?: Array<{ name?: string; execute?: (...args: any[]) => unknown }> };
  };
  clearQueue?: () => Promise<unknown>;
  steer?: (message: string) => Promise<unknown>;
};
type TintinRegistry = {
  getRecord?: (id: string) => { session?: TintinSession; sessionFile?: string; outputFile?: string; rootSessionId?: string } | undefined;
  spawn?: (pi: ExtensionAPI, ctx: unknown, type: string, prompt: string, options: Record<string, unknown>) => string;
};

/**
 * Install the Workbench shell deadline on a Tintin child session. Isolated
 * children deliberately load no extensions, so the normal Pi `tool_call`
 * extension event is unavailable there. Tintin's public spawn lifecycle still
 * exposes the child AgentSession through the manager record; its public
 * `agent.beforeToolCall` hook is the supported per-session boundary.
 */
export function installTintinChildToolDeadline(record: { session?: TintinSession } | undefined, timeoutMs: number | undefined, cwd = process.cwd()): boolean {
	const session = record?.session;
	const agent = session?.agent;
	const restricted = process.env.PITWALL_WORKBENCH_RESTRICTED === "1";
	if (timeoutMs === undefined && !restricted) return true;
	let installed = false;
  if (agent && typeof agent.beforeToolCall === "function") {
    const priorBeforeToolCall = agent.beforeToolCall;
    agent.beforeToolCall = async (context, signal) => {
      if (context?.toolCall && context.args && typeof context.args === "object" && !Array.isArray(context.args)) {
        applySoloToolDeadline({ toolName: context.toolCall.name, input: context.args as Record<string, unknown> }, timeoutMs);
      }
      if (context?.toolCall?.arguments && typeof context.toolCall.arguments === "object" && !Array.isArray(context.toolCall.arguments)) {
        applySoloToolDeadline({ toolName: context.toolCall.name, input: context.toolCall.arguments as Record<string, unknown> }, timeoutMs);
      }
      return priorBeforeToolCall(context, signal);
    };
    installed = true;
  }
  // AgentSession captures its beforeToolCall function at prompt start. The
  // built-in tool objects are public too, so wrap execution as a race-safe
  // fallback when a manager record becomes visible after that capture.
	for (const tool of agent?.state?.tools ?? []) {
		if ((tool.name !== "bash" && tool.name !== "powershell") || typeof tool.execute !== "function") continue;
		const priorExecute = tool.execute;
		const restrictedExecute = restricted && tool.name === "bash" ? createBashTool(cwd, { operations: restrictedBashOperations(restrictedPathsFromEnvironment(cwd)) }).execute : priorExecute;
		tool.execute = async (...args: any[]) => {
			const input = args[1];
			if (input && typeof input === "object" && !Array.isArray(input)) applySoloToolDeadline({ toolName: tool.name!, input }, timeoutMs);
			return restrictedExecute(...args);
		};
		installed = true;
  }
  return installed;
}
async function waitForTintinChildSession(getRecord: (id: string) => { session?: TintinSession } | undefined, childId: string, timeoutMs = 5_000): Promise<{ session?: TintinSession } | undefined> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const record = getRecord(childId);
    if (record?.session) return record;
    if (Date.now() >= deadline) return record;
    await new Promise<void>((resolve) => { const timer = setTimeout(resolve, 1); timer.unref(); });
  }
}
async function stopTintin(pi: ExtensionAPI, agentId: string): Promise<void> {
	const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
	const record = registry?.getRecord?.(agentId);
	if (!record?.session || typeof record.session.clearQueue !== "function") throw new Error("active native child record is unavailable");
	await record.session.clearQueue();
	const requestId = `wb-stop-${randomUUID()}`;
	const reply = new Promise<void>((resolve, reject) => {
		let timer: ReturnType<typeof setTimeout> | undefined;
		const unsubscribe = pi.events.on(`${TINTIN_RPC_PREFIX}stop:reply:${requestId}`, (payload: unknown) => {
			if (timer) clearTimeout(timer);
			unsubscribe();
			const value = payload && typeof payload === "object" ? payload as { success?: boolean; error?: string } : {};
			value.success === true ? resolve() : reject(new Error(value.error ?? "native stop failed"));
		});
		timer = setTimeout(() => { unsubscribe(); reject(new Error("native stop acknowledgement timeout")); }, 5_000);
		timer.unref();
	});
	pi.events.emit("subagents:rpc:stop", { requestId, agentId });
	await reply;
}

const taskSchema = Type.Object({
	 task: Type.String({ minLength: 1, maxLength: 4_000 }),
	 acceptance: Type.Optional(Type.String({ maxLength: 1_000 })),
	 scope: Type.Optional(Type.String({ maxLength: 1_000 })),
	 role: Type.Optional(Type.String()),
	 handoffId: Type.Optional(Type.String()),
});
type TaskInput = Static<typeof taskSchema>;

type NativeProfile = {
  schemaVersion: 1;
  backend: "@tintinweb/pi-subagents";
  version: "0.19.0";
  profileName?: string;
  planning?: boolean;
  profile: { provider: string; modelId: string; api: "openai-completions" | "openai-responses" | "anthropic-messages"; resourceGroup: string; reasoningLevel?: string; accountRef?: string; taskTimeoutMs?: number; toolTimeoutMs?: number };
	providerConfig: {
		baseUrl: string;
		api: string;
		apiKey?: string;
		models: Array<Record<string, unknown>>;
		[key: string]: unknown;
	};
};

type ActiveRequest = {
	requestId: string;
	ownerRunId: string;
	nodeId: string;
	runId?: string;
	childId?: string;
	taskId?: string;
	stopError?: string;
	stopPromise?: Promise<void>;
	persistenceError?: string;
	requestStop: () => void;
	settled: Promise<ChildResponse>;
	resolve: (response: ChildResponse) => void;
};

const textResult = (text: string, isError = false) => ({
	content: [{ type: "text" as const, text }],
	isError,
	details: undefined,
});

function bounded(text: string, limit = 4_000): string {
	return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function configuredChecks(): ApprovedCheck[] {
	const raw = process.env.PITWALL_WORKBENCH_APPROVED_CHECKS;
	if (!raw) throw new Error("PITWALL_WORKBENCH_APPROVED_CHECKS is required for explicit integration");
	const checks: unknown = JSON.parse(raw);
	if (!Array.isArray(checks) || checks.length === 0 || checks.some((check) => !check || typeof check !== "object" || typeof (check as Record<string, unknown>).command !== "string")) throw new Error("PITWALL_WORKBENCH_APPROVED_CHECKS must be a non-empty array of commands");
	return checks as ApprovedCheck[];
}

function instructionProvenance(ctx: unknown): InstructionProvenance {
	const value = ctx as { getSystemPromptOptions?: () => { contextFiles?: Array<{ path: string; content: string }>; customPrompt?: string; appendSystemPrompt?: string }; getSystemPrompt?: () => string };
	try {
		const options = value.getSystemPromptOptions?.();
		if (options && Array.isArray(options.contextFiles)) {
			const contextFiles = options.contextFiles;
			const inline = [
				...(options.customPrompt ? [{ source: "customPrompt" as const, value: options.customPrompt }] : []),
				...(options.appendSystemPrompt ? [{ source: "appendSystemPrompt" as const, value: options.appendSystemPrompt }] : []),
			].map((entry) => ({ source: entry.source, sha256: createHash("sha256").update(entry.value).digest("hex"), bytes: Buffer.byteLength(entry.value) }));
			return { source: "public-context-files", complete: inline.length === 0, ...(inline.length ? { reason: "inline system-prompt contributions are hashed but do not expose source files" } : {}), files: contextFiles.map((file) => ({ path: file.path, sha256: createHash("sha256").update(file.content).digest("hex"), bytes: Buffer.byteLength(file.content), estimated: false })), ...(inline.length ? { inline } : {}) };
		}
	} catch { /* Context provenance is best effort when a runtime does not expose command options. */ }
	try {
		const prompt = value.getSystemPrompt?.() ?? "";
		return { source: "final-system-prompt", complete: false, reason: "ExtensionContext exposes aggregate system prompt but not ResourceLoader context-file paths for a tool invocation", files: [{ path: "<final-system-prompt>", sha256: createHash("sha256").update(prompt).digest("hex"), bytes: Buffer.byteLength(prompt), estimated: true }], promptSha256: createHash("sha256").update(prompt).digest("hex"), promptBytes: Buffer.byteLength(prompt) };
	} catch {
		return { source: "final-system-prompt", complete: false, reason: "runtime did not expose public instruction provenance", files: [], promptBytes: 0 };
	}
}

type ParentInstructionCapture = {
	path: string;
	parentSessionId: string;
	parentSessionFile?: string;
	provenance: InstructionProvenance;
};

function parentInstructionCaptureIdentity(ctx: unknown, sessionKey: string): { parentSessionId: string; parentSessionFile?: string; sessionKey: string } {
	const value = ctx as { sessionManager?: { getSessionId?: () => string; getSessionFile?: () => string } };
	const parentSessionId = value.sessionManager?.getSessionId?.() || `extension-${sessionKey}`;
	const parentSessionFile = value.sessionManager?.getSessionFile?.();
	return { parentSessionId, ...(parentSessionFile ? { parentSessionFile } : {}), sessionKey };
}

async function persistParentInstructionProvenance(ctx: unknown, profilePath: string, sessionKey: string): Promise<ParentInstructionCapture> {
	const identity = parentInstructionCaptureIdentity(ctx, sessionKey);
	const provenance = instructionProvenance(ctx);
	const provenanceHash = createHash("sha256").update(JSON.stringify(provenance)).digest("hex").slice(0, 20);
	const sessionHash = createHash("sha256").update(`${identity.parentSessionId}\n${identity.parentSessionFile ?? ""}\n${identity.sessionKey}`).digest("hex").slice(0, 20);
	const path = join(dirname(profilePath), `parent-instruction-provenance-${sessionHash}-${provenanceHash}.json`);
	const envelope = { schemaVersion: 1, parentSessionId: identity.parentSessionId, ...(identity.parentSessionFile ? { parentSessionFile: identity.parentSessionFile } : {}), provenance };
	try { await writeFile(path, `${JSON.stringify(envelope, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" }); }
	catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
	return { path, parentSessionId: identity.parentSessionId, ...(identity.parentSessionFile ? { parentSessionFile: identity.parentSessionFile } : {}), provenance };
}

function provenanceReport(capture: ParentInstructionCapture): string {
	const { path, parentSessionId, parentSessionFile, provenance } = capture;
	return JSON.stringify({ path, parentSessionId, ...(parentSessionFile ? { parentSessionFile } : {}), source: provenance.source, complete: provenance.complete, files: provenance.files.map(file => ({ path: file.path, sha256: file.sha256, bytes: file.bytes, estimated: file.estimated })), ...(provenance.inline ? { inline: provenance.inline } : {}), ...(provenance.reason ? { reason: provenance.reason } : {}) });
}

export function credentialClassForNativeProfile(providerConfig: { apiKey?: unknown }): "keyless" | "api-key-env" {
	return typeof providerConfig.apiKey === "string" && providerConfig.apiKey.startsWith("$") ? "api-key-env" : "keyless"; // pragma: allowlist secret
}

export async function childInstructionProvenance(childId: string): Promise<InstructionProvenance | undefined> {
	const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
	const session = registry?.getRecord?.(childId)?.session as (Record<string, unknown> & { resourceLoader?: { getAgentsFiles?: () => { agentsFiles?: Array<{ path: string; content: string }> }; getSystemPrompt?: () => string; getSystemPromptSource?: () => { path: string } | undefined; getAppendSystemPrompt?: () => string[]; getAppendSystemPromptSources?: () => Array<{ path: string }> } }) | undefined;
	const loader = session?.resourceLoader;
	if (!loader?.getAgentsFiles) return undefined;
    const files: Array<{ path: string; sha256: string; bytes: number; estimated: boolean }> = [];
    try {
      const agentsResult = loader.getAgentsFiles();
      const missing = [
        ...(!Array.isArray(agentsResult?.agentsFiles) ? ["agentsFiles"] : []),
        ...(typeof loader.getSystemPrompt !== "function" ? ["getSystemPrompt"] : []),
        ...(typeof loader.getAppendSystemPrompt !== "function" ? ["getAppendSystemPrompt"] : []),
      ];
      // ResourceLoader exposes the bytes already loaded into the child prompt.
      // Hash those values directly; rereading source paths at completion would
      // let a later mutation falsify the provenance of the executed child.
      for (const file of agentsResult?.agentsFiles ?? []) files.push({ path: file.path, sha256: createHash("sha256").update(file.content).digest("hex"), bytes: Buffer.byteLength(file.content), estimated: false });
      const systemPrompt = loader.getSystemPrompt?.() ?? "";
      const systemSource = loader.getSystemPromptSource?.();
      // An undefined source is valid for Pi's built-in prompt; the loaded
      // bytes above still give an exact hash. A missing getter is uncertainty.
      if (systemPrompt && typeof loader.getSystemPromptSource !== "function") missing.push("getSystemPromptSource");
      if (systemPrompt) files.push({ path: systemSource?.path ?? "<system-prompt>", sha256: createHash("sha256").update(systemPrompt).digest("hex"), bytes: Buffer.byteLength(systemPrompt), estimated: false });
      const appendPrompts = loader.getAppendSystemPrompt?.() ?? [];
      const appendSources = loader.getAppendSystemPromptSources?.() ?? [];
      if (appendPrompts.some(Boolean) && typeof loader.getAppendSystemPromptSources !== "function") missing.push("getAppendSystemPromptSources");
      appendPrompts.forEach((content, index) => { if (content) files.push({ path: appendSources[index]?.path ?? `<append-system-prompt:${index}>`, sha256: createHash("sha256").update(content).digest("hex"), bytes: Buffer.byteLength(content), estimated: false }); });
      return { source: "resource-loader", complete: missing.length === 0, ...(missing.length ? { reason: `resource loader did not expose ${missing.join(", ")}` } : {}), files };
    } catch (error) {
      return { source: "resource-loader", complete: false, reason: `resource loader instruction bytes unavailable: ${error instanceof Error ? error.message : String(error)}`, files };
    }
}

function unavailableChildInstructionProvenance(reason = "child resource loader did not expose loaded instruction bytes"): InstructionProvenance {
	return {
		source: "resource-loader",
		complete: false,
		reason,
		files: [],
	};
}

/**
 * Direct manager spawns return an id before Tintin has created the child
 * AgentSession.  Wait for the public ResourceLoader to exist before taking a
 * provenance snapshot; otherwise the first lookup would permanently record an
 * incomplete result even though the child later loaded its instructions.
 */
export async function waitForTintinChildInstructionProvenance(childId: string, timeoutMs = 5_000): Promise<InstructionProvenance> {
	const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
	if (typeof registry?.getRecord !== "function") return unavailableChildInstructionProvenance("native child manager did not expose a public record lookup");
	const deadline = Date.now() + timeoutMs;
	for (;;) {
		const session = registry.getRecord(childId)?.session as (TintinSession & { resourceLoader?: { getAgentsFiles?: unknown } }) | undefined;
		if (typeof session?.resourceLoader?.getAgentsFiles === "function") {
			return await childInstructionProvenance(childId) ?? unavailableChildInstructionProvenance("child ResourceLoader became visible without readable instruction bytes");
		}
		if (Date.now() >= deadline) return unavailableChildInstructionProvenance(`child ResourceLoader was not ready within ${timeoutMs}ms`);
		await new Promise<void>((resolve) => { const timer = setTimeout(resolve, 1); timer.unref(); });
	}
}

async function loadProfile(path: string): Promise<NativeProfile> {
	const raw: unknown = JSON.parse(await readFile(path, "utf8"));
	if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("native profile must be an object");
	const value = raw as Record<string, unknown>;
	const supportedKeys = new Set(["schemaVersion", "backend", "version", "profileName", "planning", "profile", "providerConfig"]);
	const unknownKeys = Object.keys(value).filter((key) => !supportedKeys.has(key));
	if (unknownKeys.length) throw new Error(`unknown native profile field: ${unknownKeys.join(", ")}`);
	const profile = value.profile;
	const providerConfig = value.providerConfig;
  if (value.schemaVersion !== 1 || value.backend !== "@tintinweb/pi-subagents" || value.version !== "0.19.0" || (value.profileName !== undefined && typeof value.profileName !== "string") || (value.planning !== undefined && typeof value.planning !== "boolean") || !profile || typeof profile !== "object" || Array.isArray(profile) || !providerConfig || typeof providerConfig !== "object" || Array.isArray(providerConfig)) {
		throw new Error("native profile schemaVersion 1 with profile/providerConfig is required");
	}
	const p = profile as Record<string, unknown>;
	const c = providerConfig as Record<string, unknown>;
	if (typeof p.provider !== "string" || typeof p.modelId !== "string" || !["openai-completions", "openai-responses", "anthropic-messages"].includes(String(p.api)) || typeof p.resourceGroup !== "string" || !p.resourceGroup) throw new Error("native profile requires provider, API, and resourceGroup");
	if (typeof c.baseUrl !== "string" || c.api !== p.api || !Array.isArray(c.models)) throw new Error("native providerConfig API and models are required");
	const selected = c.models.filter((model): model is Record<string, unknown> => !!model && typeof model === "object" && !Array.isArray(model) && model.id === p.modelId);
	if (selected.length !== 1) throw new Error("native providerConfig must contain exactly one selected model");
	const model = selected[0];
	if (model.api !== undefined && model.api !== p.api) throw new Error("native model API conflicts with provider API");
  const native = { schemaVersion: 1 as const, backend: "@tintinweb/pi-subagents" as const, version: "0.19.0" as const, ...(typeof value.profileName === "string" ? { profileName: value.profileName } : {}), planning: value.planning === true, profile: p as NativeProfile["profile"], providerConfig: c as NativeProfile["providerConfig"] };
  if (native.planning) await validatePlanningRoleProfiles(dirname(path), `${p.provider}/${p.modelId}`);
  return native;
}

export function makeTintinDelegation(pi: ExtensionAPI, state: { current?: ActiveRequest }, cwd: string, task: string, model: string, profileThinking: string | undefined, signal: AbortSignal | undefined, role = "workbench-scout", onChild?: (childId: string, directRegistrySpawn?: boolean, instructionProvenance?: InstructionProvenance) => void, toolTimeoutMs?: number, spawnContext?: unknown, spawnAcknowledgementTimeoutMs = 15_000): Promise<ChildResponse> {
	const requestId = `wb-${randomUUID()}`;
	let resolve!: (response: ChildResponse) => void;
	const settled = new Promise<ChildResponse>((done) => { resolve = done; });
	let requestStop = () => {};
	const active: ActiveRequest = { requestId, ownerRunId: requestId, nodeId: role, settled, resolve, requestStop: () => requestStop() };
	state.current = active;
	let childId: string | undefined;
	let stopRequested = false;
	let childToolReady = toolTimeoutMs === undefined;
	// Direct registry.spawn returns before child instruction provenance is
	// necessarily available. Hold terminal completion until the bounded
	// provenance snapshot has completed (or explicitly recorded incomplete).
	let childProvenanceReady = true;
	const pendingTerminal = new Map<string, { payload: unknown; failed: boolean }>();
	let finished = false;
	let spawnUncertain = false;
	let readinessFailed = false;
	let spawnAckTimer: ReturnType<typeof setTimeout> | undefined;
	requestStop = () => {
		if (stopRequested && (!childId || active.stopPromise)) return;
		stopRequested = true;
		if (childId) active.stopPromise = stopTintin(pi, childId).catch((error: unknown) => { active.stopError = error instanceof Error ? error.message : String(error); });
	};
	const finish = (payload: unknown, failed = false) => {
		if (finished) return;
		if (!payload || typeof payload !== "object" || Array.isArray(payload)) return;
		const value = payload as Record<string, unknown>;
		if (!childId) { if (typeof value.id === "string") pendingTerminal.set(value.id, { payload, failed }); return; }
		if (!childToolReady || !childProvenanceReady) { pendingTerminal.set(childId, { payload, failed }); return; }
		if (value.id !== childId) return;
		finished = true;
		pendingTerminal.delete(childId);
		if (typeof value.id === "string") pi.events.emit("subagents:rpc:consume", { requestId: `wb-consume-${randomUUID()}`, agentId: value.id });
    const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
    const record = registry?.getRecord?.(childId);
		resolve({ requestId, childId, backendRecord: record ? { ...(record.sessionFile ? { sessionFile: record.sessionFile } : {}), ...(record.outputFile ? { outputFile: record.outputFile } : {}), ...(record.rootSessionId ? { rootSessionId: record.rootSessionId } : {}) } : undefined, status: value.status === "aborted" || value.status === "stopped" ? "cancelled" : failed ? "failed" : "completed", error: typeof value.error === "string" ? value.error : undefined, result: typeof value.result === "string" ? { kind: "text", text: value.result } : undefined });
		if (state.current?.requestId === requestId) state.current = undefined;
		if (spawnUncertain || readinessFailed) { unsubscribeComplete(); unsubscribeFailed(); unsubscribeSpawn(); }
	};
	const unsubscribeComplete = pi.events.on("subagents:completed", (payload) => finish(payload));
	const unsubscribeFailed = pi.events.on("subagents:failed", (payload) => finish(payload, true));
	const cancel = () => {
		if (state.current?.requestId !== requestId) return;
		requestStop();
	};
	const failReadiness = (id: string, error: string) => {
		// A failed readiness probe does not prove that a spawned child exited.
		// Keep its ownership until a correlated terminal event arrives.
		readinessFailed = true;
		childProvenanceReady = true;
		resolve({ requestId, childId: id, status: "failed", error });
		childToolReady = true;
		requestStop();
		const terminal = pendingTerminal.get(id);
		if (terminal) finish(terminal.payload, terminal.failed);
	};
	if (signal) signal.addEventListener("abort", cancel, { once: true });
	const spawnReply = `${TINTIN_RPC_PREFIX}spawn:reply:${requestId}`;
	const unsubscribeSpawn = pi.events.on(spawnReply, (payload: unknown) => {
		if (spawnAckTimer) clearTimeout(spawnAckTimer);
		const value = payload && typeof payload === "object" ? payload as { success?: boolean; data?: { id?: string }; error?: string } : {};
		if (spawnUncertain) {
			// The caller has already received an uncertain-launch result. A late
			// child must be stopped, never silently adopted by a new task.
			if (value.success !== true || !value.data?.id) { if (state.current?.requestId === requestId) state.current = undefined; unsubscribeComplete(); unsubscribeFailed(); unsubscribeSpawn(); return; }
			childId = value.data.id;
			active.childId = childId;
			active.ownerRunId = childId;
			requestStop();
			return;
		}
		if (value.success !== true || !value.data?.id) { resolve({ requestId, status: "failed", error: value.error ?? "spawn failed" }); state.current = undefined; return; }
        childId = value.data.id;
        active.childId = childId;
        active.ownerRunId = childId;
		void (async () => {
          const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
          const record = registry?.getRecord?.(childId);
			const readyRecord = toolTimeoutMs === undefined || !registry?.getRecord ? record : await waitForTintinChildSession(registry.getRecord, childId);
			if (!installTintinChildToolDeadline(readyRecord, toolTimeoutMs, cwd)) {
            const capability = !readyRecord ? "record" : !readyRecord.session ? "session" : !readyRecord.session.agent ? "agent" : "beforeToolCall";
            const error = `native child session did not expose the public ${capability} hook; refusing an unbounded shell tool`;
			failReadiness(childId, error);
			return;
          }
          childToolReady = true;
          onChild?.(childId);
		  if (stopRequested) requestStop();
		  const terminal = pendingTerminal.get(childId);
		  if (terminal) finish(terminal.payload, terminal.failed);
        })();
	});
	const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
	if (spawnContext !== undefined && typeof registry?.spawn === "function") {
		try {
			childProvenanceReady = false;
			const modelParts = model.split("/");
			const modelRegistry = (spawnContext as { modelRegistry?: { find?: (provider: string, modelId: string) => unknown } }).modelRegistry;
			const resolvedModel = modelParts.length >= 2 ? modelRegistry?.find?.(modelParts[0], modelParts.slice(1).join("/")) : undefined;
			if (!resolvedModel) throw new Error(`native profile model ${model} is not available in the active model registry`);
			const directId = registry.spawn(pi, spawnContext, role, task, {
				description: task.slice(0, 120), model: resolvedModel, thinkingLevel: profileThinking, isolated: true, inheritContext: false, isBackground: true, cwd,
			});
			childId = directId;
			active.childId = directId;
			active.ownerRunId = directId;
			if (stopRequested) requestStop();
			void (async () => {
				const readyRecord = await waitForTintinChildSession(registry.getRecord ?? (() => undefined), directId);
					if (!installTintinChildToolDeadline(readyRecord, toolTimeoutMs, cwd)) {
					const error = "native child session did not expose the public beforeToolCall hook; refusing an unbounded shell tool";
					failReadiness(directId, error);
						return;
						}
						childToolReady = true;
						const directProvenance = await waitForTintinChildInstructionProvenance(directId);
						childProvenanceReady = true;
					onChild?.(directId, true, directProvenance);
						const terminal = pendingTerminal.get(directId);
					if (terminal) finish(terminal.payload, terminal.failed);
				})();
		} catch (error) {
			resolve({ requestId, status: "failed", error: error instanceof Error ? error.message : String(error) });
			state.current = undefined;
		}
	} else {
		pi.events.emit("subagents:rpc:spawn", { requestId, type: role, prompt: task, options: { description: task.slice(0, 120), model, thinkingLevel: profileThinking, isolated: true, inheritContext: false, isBackground: true, cwd } });
		spawnAckTimer = setTimeout(() => {
			if (childId || finished) return;
			spawnUncertain = true;
			active.stopError = `native spawn acknowledgement timed out after ${spawnAcknowledgementTimeoutMs}ms; child ownership is uncertain and further launches are blocked`;
			resolve({ requestId, status: "interrupted", error: active.stopError });
		}, spawnAcknowledgementTimeoutMs);
		spawnAckTimer.unref();
	}
	void settled.finally(() => { if (!spawnUncertain && !readinessFailed) { unsubscribeComplete(); unsubscribeFailed(); unsubscribeSpawn(); } signal?.removeEventListener("abort", cancel); if (spawnAckTimer) clearTimeout(spawnAckTimer); });
	return settled;
}

export default async function registerNativeExtension(pi: ExtensionAPI): Promise<void> {
	const profilePath = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
	if (!profilePath) return;
	const profile = await loadProfile(profilePath);
	const state: { current?: ActiveRequest; parentInstructionProvenancePath?: string; parentSessionId?: string; parentSessionFile?: string; sessionKey: string } = { sessionKey: randomUUID() };
  const taskStore = new TaskRecordStore(dirname(profilePath));
  let stopLatch = false;
	let nativeReady = false;
	let launchesThisTurn = 0;
	let coordinatorCwd = process.cwd();
	registerAdmissionProvider(pi, profile, profilePath);
	pi.on("input", (event) => {
		// A genuine new prompt opens the stop latch and resets the launch budget.
		// Extension-delivered follow-ups and steering are deliberately excluded.
		if ((event.source === "interactive" || event.source === "rpc") && !event.streamingBehavior) {
			stopLatch = false;
			launchesThisTurn = 0;
		}
		return { action: "continue" };
	});
	pi.on("session_start", async () => {
		state.parentInstructionProvenancePath = undefined;
		state.parentSessionId = undefined;
		state.parentSessionFile = undefined;
		pi.setActiveTools(profile.planning ? ["read", "grep", "find", "ls", "agent_task", "agent_control"] : ["read", "bash", "edit", "write", "agent_task", "agent_control"]);
		const requestId = `wb-ping-${randomUUID()}`;
		const tintinPing = new Promise<boolean>((resolve) => {
			let timer: ReturnType<typeof setTimeout> | undefined;
			const unsubscribe = pi.events.on(`${TINTIN_RPC_PREFIX}ping:reply:${requestId}`, (payload: unknown) => {
				unsubscribe();
				if (timer) clearTimeout(timer);
				const value = payload && typeof payload === "object" ? payload as { success?: boolean; data?: { version?: number } } : {};
				resolve(value.success === true && value.data?.version === 2);
			});
			timer = setTimeout(() => { unsubscribe(); resolve(false); }, 2_000); timer.unref();
		});
		pi.events.emit(`${TINTIN_RPC_PREFIX}ping`, { requestId });
		nativeReady = await tintinPing;
	});
	if (profile.profile.toolTimeoutMs !== undefined) {
		pi.on("tool_call", (event) => {
			applySoloToolDeadline(event as { toolName: string; input: Record<string, unknown> }, profile.profile.toolTimeoutMs);
		});
	}
	if (profile.planning) {
		// Active tools shape the model request, but a model can still emit a
		// fabricated tool call. Keep the policy enforceable at execution time.
		pi.on("tool_call", async (event) => {
			if (["read", "grep", "find", "ls", "agent_task", "agent_control"].includes(event.toolName)) return;
			return { block: true, terminate: true, reason: `planning policy blocks ${event.toolName}; disable planning mode before making changes` };
		});
	}
	const taskTool: ToolDefinition<typeof taskSchema> = {
		name: "agent_task", label: "Agent task", description: "Run a bounded fresh scout, worker, or handoff reviewer task.",
		parameters: taskSchema, executionMode: "sequential",
				execute: async (_toolCallId, params: TaskInput, signal, _onUpdate, ctx) => {
				coordinatorCwd = ctx.cwd;
				if (!nativeReady) return textResult("native subagent backend is not ready", true);
				if (stopLatch) return textResult("native delegation stopped; waiting for new user input", true);
				if (signal?.aborted) return textResult("native request cancelled before launch", true);
				if (state.current) return textResult(state.current.stopError ? `native child stop failed; request remains unresolved: ${state.current.stopError}` : "a native workbench request is still unresolved", true);
				if (launchesThisTurn >= MAX_LAUNCHES_PER_TURN) return textResult(`native launch budget exhausted (${MAX_LAUNCHES_PER_TURN}); a new user input is required before another child can launch`, true);
				const role = params.role ?? "scout";
				if (profile.planning && role === "worker") return textResult("planning policy permits only read-only child roles", true);
				const roleType = role === "scout" ? "workbench-scout" : role === "worker" ? "workbench-worker" : role === "reviewer" ? "workbench-reviewer" : undefined;
				if (!roleType) return textResult("role must be scout, worker, or reviewer", true);
				let handoff: HandoffWorkspace | undefined;
				let preserveHandoff = false;
				let childCwd = ctx.cwd;
				let reviewerEnvelope = "";
				let reviewedHandoff: HandoffManifest | undefined;
				if (role === "worker") {
					try { handoff = await createHandoffWorkspace(ctx.cwd); childCwd = handoff.workspace; }
					catch (error) { return textResult(`writer workspace unavailable: ${error instanceof Error ? error.message : String(error)}`, true); }
				} else if (role === "reviewer") {
					if (!params.handoffId) return textResult("reviewer requires a handoffId from a completed writer", true);
					try {
						reviewedHandoff = await loadHandoff(ctx.cwd, params.handoffId);
						if (!reviewedHandoff.patchPath || !reviewedHandoff.patchSha256) throw new Error("reviewer requires a captured patch");
						childCwd = reviewedHandoff.workspace;
						reviewerEnvelope = `\n\nVerified handoff base revision: ${reviewedHandoff.revision}\nVerified handoff patch path: ${reviewedHandoff.patchPath}\nVerified handoff patch SHA256: ${reviewedHandoff.patchSha256}\nReviewer must read the patch artifact from the verified path and inspect it within the read-only workspace.`;
					}
					catch (error) { return textResult(`handoff unavailable: ${error instanceof Error ? error.message : String(error)}`, true); }
				}
				const envelope = [params.task, params.acceptance ? `Acceptance criteria:\n${params.acceptance}` : "", params.scope ? `Scope:\n${params.scope}` : "", reviewerEnvelope].filter(Boolean).join("\n\n");
				let taskRecord: TaskRecord;
				try {
					taskRecord = taskStore.createSync({
						role: role as "scout" | "worker" | "reviewer",
                        profile: { name: profile.profileName ?? `${profile.profile.provider}/${profile.profile.modelId}`, provider: profile.profile.provider, modelId: profile.profile.modelId, api: profile.profile.api, endpoint: profile.providerConfig.baseUrl, resourceGroup: profile.profile.resourceGroup, credentialClass: credentialClassForNativeProfile(profile.providerConfig), ...(profile.profile.accountRef ? { accountRef: profile.profile.accountRef } : {}) },
						workspace: childCwd,
						baseRevision: role === "reviewer" ? reviewedHandoff?.revision : undefined,
						task: params.task,
						acceptance: params.acceptance,
						scope: params.scope,
						instructionProvenance: instructionProvenance(ctx),
						parentSessionId: parentInstructionCaptureIdentity(ctx, state.sessionKey).parentSessionId,
						parentSessionFile: parentInstructionCaptureIdentity(ctx, state.sessionKey).parentSessionFile,
						...(state.parentSessionId === parentInstructionCaptureIdentity(ctx, state.sessionKey).parentSessionId && state.parentSessionFile === parentInstructionCaptureIdentity(ctx, state.sessionKey).parentSessionFile && state.parentInstructionProvenancePath ? { parentInstructionProvenancePath: state.parentInstructionProvenancePath } : {}),
					});
				} catch (error) {
					return textResult(`task record unavailable: ${error instanceof Error ? error.message : String(error)}`, true);
				}
				launchesThisTurn++;
				let childRecordUpdate: Promise<unknown> = Promise.resolve();
				let deadlineExpired = false;
				const result = makeTintinDelegation(pi, state, childCwd, envelope, `${profile.profile.provider}/${profile.profile.modelId}`, profile.profile.reasoningLevel, signal, roleType, (childId, directRegistrySpawn = false, directProvenance) => {
					const active = state.current as ActiveRequest | undefined;
					const persistence = (async () => {
						// Snapshot the public loader bytes before any persistence await. The
						// record must describe the child role that actually ran, and a late
						// loader mutation must not replace that snapshot with the parent's
						// aggregate provenance.
						const provenance = directRegistrySpawn ? directProvenance ?? await waitForTintinChildInstructionProvenance(childId) : await childInstructionProvenance(childId) ?? unavailableChildInstructionProvenance();
						await taskStore.update(taskRecord.taskId, { ...(deadlineExpired ? {} : { executionState: "running" as const }), childId, instructionProvenance: provenance });
					})();
					// A deadline can return before a late spawn callback finishes. Observe
					// that detached rejection without changing the original promise: a
					// settled request still awaits and surfaces persistence failure.
					void persistence.catch((error: unknown) => {
						const message = error instanceof Error ? error.message : String(error);
						if (active) active.persistenceError = message;
						pi.events.emit("subagents:persistence_error", { taskId: taskRecord.taskId, childId, error: message });
					});
					childRecordUpdate = persistence;
				}, profile.profile.toolTimeoutMs, ctx, (profile.profile.taskTimeoutMs ?? 125_000) + 5_000);
				const activeAfterLaunch = state.current as ActiveRequest | undefined;
				if (activeAfterLaunch) activeAfterLaunch.taskId = taskRecord.taskId;
				let timeout: ReturnType<typeof setTimeout> | undefined;
				try {
				const response = await Promise.race([
				result,
				new Promise<never>((_, reject) => {
						timeout = setTimeout(() => {
							deadlineExpired = true;
							state.current?.requestStop();
						reject(new Error(`native task deadline exceeded after ${profile.profile.taskTimeoutMs ?? 125_000}ms; stop requested and request remains unresolved`));
						}, profile.profile.taskTimeoutMs ?? 125_000);
					timeout.unref();
				}),
			]);
			const responseValue = response as unknown as ChildResponse;
				try { await childRecordUpdate; }
				catch (error) { return textResult(`native task lineage persistence failed: ${error instanceof Error ? error.message : String(error)}`, true); }
			if (responseValue.backendRecord) {
				await taskStore.update(taskRecord.taskId, {
					...(responseValue.backendRecord.sessionFile ? { backendSessionFile: responseValue.backendRecord.sessionFile } : {}),
					...(responseValue.backendRecord.outputFile ? { backendOutputFile: responseValue.backendRecord.outputFile } : {}),
					...(responseValue.backendRecord.rootSessionId ? { rootSessionId: responseValue.backendRecord.rootSessionId } : {}),
				});
			}
			const current = state.current as ActiveRequest | undefined;
				// Correlated terminal events and explicit spawn rejection release ownership.
				// A failed readiness probe or uncertain stop must keep its child owned.
				if (responseValue.status !== "completed" || !responseValue.result || responseValue.result.kind !== "text") {
					const artifactErrors: string[] = [];
					let failedCapture: Awaited<ReturnType<typeof capturePatch>> | undefined;
					if (reviewedHandoff) {
						try { await recordReview(reviewedHandoff, responseValue.error ?? responseValue.status, "failed"); }
						catch (error) { artifactErrors.push(`review artifact failed: ${error instanceof Error ? error.message : String(error)}`); }
					}
					if (handoff) {
						preserveHandoff = true;
						try { failedCapture = await capturePatch(handoff, responseValue.status === "cancelled" ? "cancelled" : "failed"); }
						catch (error) { artifactErrors.push(`handoff capture failed; writer lock retained for recovery: ${error instanceof Error ? error.message : String(error)}`); }
					}
					const artifactNote = artifactErrors.length ? `\nARTIFACT_ERRORS=${artifactErrors.join("; ")}` : "";
					const failureEvidence = failedCapture?.manifest.patchSha256 ? {
						changedFiles: failedCapture.changedFiles,
						handoff: { handoffId: failedCapture.manifest.taskId, patchPath: failedCapture.patchPath, patchSha256: failedCapture.manifest.patchSha256 },
					} : {};
					await taskStore.finish(taskRecord.taskId, { executionState: responseValue.status === "cancelled" ? "cancelled" : responseValue.status === "interrupted" ? "interrupted" : "failed", acceptanceState: "rejected", result: responseValue.error ?? responseValue.status, error: responseValue.error ?? responseValue.status, usage: { source: "unavailable" }, ...failureEvidence });
					return textResult(`${responseValue.error ?? responseValue.status}${handoff ? `; writer workspace preserved at ${handoff.workspace}` : ""}${artifactNote}`, true);
				}
					const fullChildText = responseValue.result.text ?? "";
					let text = bounded(fullChildText);
					let reviewArtifactError = "";
					let acceptanceState: AcceptanceState = "unchecked";
					const verification: Array<{ checkId: string; exitCode: number; evidencePath: string }> = [];
					if (reviewedHandoff) {
						try { await recordReview(reviewedHandoff, fullChildText); verification.push({ checkId: "handoff-review", exitCode: 0, evidencePath: join(reviewedHandoff.workspace, "..", "review.json") }); }
						catch (error) { reviewArtifactError = `\nREVIEW_ARTIFACT_ERROR=${error instanceof Error ? error.message : String(error)}`; }
					if (!reviewArtifactError) acceptanceState = "reviewed";
				}
				if (handoff) {
					let captured;
					try { captured = await capturePatch(handoff); }
					catch (error) {
						preserveHandoff = true;
						await taskStore.finish(taskRecord.taskId, { executionState: "failed", acceptanceState: "rejected", error: error instanceof Error ? error.message : String(error) }).catch(() => undefined);
							return textResult(`${text}\nHANDOFF_ERROR=${error instanceof Error ? error.message : String(error)}\nWRITER_WORKSPACE_PRESERVED=${handoff.workspace}`, true);
						}
						if (!captured.manifest.patchSha256) throw new Error("captured handoff lacks patch digest");
						const resultEvidence = {
							changedFiles: captured.changedFiles,
							handoff: { handoffId: captured.manifest.taskId, patchPath: captured.patchPath, patchSha256: captured.manifest.patchSha256 },
							usage: { source: "unavailable" as const },
						};
						const handoffHeader = `\n\nHANDOFF_ID=${captured.manifest.taskId}\nHANDOFF_BASE=${handoff.revision}\nHANDOFF_PATCH_PATH=${captured.patchPath}\nHANDOFF_PATCH_SHA256=${captured.manifest.patchSha256}\nHANDOFF_PATCH_ARTIFACT=${captured.patchPath}\nAUTO_COMMIT=false`;
						const rawChecks = process.env.PITWALL_WORKBENCH_APPROVED_CHECKS;
						if (!rawChecks) {
							const finished = await taskStore.finish(taskRecord.taskId, { executionState: "succeeded", acceptanceState, result: fullChildText, ...resultEvidence, risks: ["approved validation was not configured"] });
						return textResult(`${text}${handoffHeader}\nVALIDATION=not-run (PITWALL_WORKBENCH_APPROVED_CHECKS is unset)\nTASK_ID=${finished.taskId}\nRUN_ID=${finished.runId}\nATTEMPT_ID=${finished.attemptId}\nRESULT_ARTIFACT=${finished.artifacts.result?.path ?? "unavailable"}\nSUMMARY_ARTIFACT=${finished.artifacts.summary?.path ?? "unavailable"}`);
					}
					let checks: Array<{ command: string; args?: string[]; timeoutMs?: number }>;
						try { checks = JSON.parse(rawChecks); if (!Array.isArray(checks) || checks.some((check) => !check || typeof check.command !== "string" || (check.args !== undefined && (!Array.isArray(check.args) || check.args.some((arg: unknown) => typeof arg !== "string"))) || (check.timeoutMs !== undefined && (!Number.isInteger(check.timeoutMs) || check.timeoutMs <= 0)))) throw new Error("invalid approved checks"); }
					catch (error) {
						await updateValidation(captured.manifest, "failed");
							const finished = await taskStore.finish(taskRecord.taskId, { executionState: "succeeded", acceptanceState: "rejected", result: fullChildText, ...resultEvidence, blockers: [`approved checks configuration is invalid: ${error instanceof Error ? error.message : String(error)}`] });
						return textResult(`${text}${handoffHeader}\nVALIDATION=failed: ${error instanceof Error ? error.message : String(error)}\nTASK_ID=${finished.taskId}\nRESULT_ARTIFACT=${finished.artifacts.result?.path ?? "unavailable"}`, true);
					}
							const validation = await runApprovedChecks(captured.manifest, checks, signal);
						verification.push(...validation.results.map((check, index) => ({ checkId: `approved-check-${index + 1}`, exitCode: check.code, evidencePath: validation.validationPath })));
						await updateValidation(captured.manifest, validation.passed ? "passed" : "failed");
						acceptanceState = validation.passed ? "checks-passed" : "rejected";
						text += `${handoffHeader}\nVALIDATION=${validation.passed ? "passed" : "failed"}`;
						const finished = await taskStore.finish(taskRecord.taskId, { executionState: "succeeded", acceptanceState, result: fullChildText, ...resultEvidence, verification, ...(validation.passed ? {} : { blockers: ["approved validation failed; inspect the linked validation artifact"] }) });
						return textResult(`${text}${reviewArtifactError}\nTASK_ID=${finished.taskId}\nRUN_ID=${finished.runId}\nATTEMPT_ID=${finished.attemptId}\nEXECUTION_STATE=${finished.executionState}\nACCEPTANCE_STATE=${finished.acceptanceState}\nRESULT_ARTIFACT=${finished.artifacts.result?.path ?? "unavailable"}\nSUMMARY_ARTIFACT=${finished.artifacts.summary?.path ?? "unavailable"}`, Boolean(reviewArtifactError));
					}
					const finished = await taskStore.finish(taskRecord.taskId, { executionState: "succeeded", acceptanceState, result: fullChildText, verification, ...(reviewArtifactError ? { blockers: [reviewArtifactError.trim()] } : {}) });
				return textResult(`${text}${reviewArtifactError}\nTASK_ID=${finished.taskId}\nRUN_ID=${finished.runId}\nATTEMPT_ID=${finished.attemptId}\nEXECUTION_STATE=${finished.executionState}\nACCEPTANCE_STATE=${finished.acceptanceState}\nRESULT_ARTIFACT=${finished.artifacts.result?.path ?? "unavailable"}\nSUMMARY_ARTIFACT=${finished.artifacts.summary?.path ?? "unavailable"}`, Boolean(reviewArtifactError));
				} catch (error) {
					await taskStore.finish(taskRecord.taskId, { executionState: "interrupted", acceptanceState: "rejected", error: error instanceof Error ? error.message : String(error) }).catch(() => undefined);
					throw error;
				} finally { if (timeout) clearTimeout(timeout); if (handoff && !preserveHandoff) await handoff.dispose().catch(() => undefined); }
		},
	};
	pi.registerTool(taskTool);
	pi.registerCommand("workbench-stop", { description: "Cancel the active native workbench request.", handler: async (_args, ctx) => {
		if (!state.current) throw new Error("no active native workbench request");
		stopLatch = true;
		// Pi exposes abort() for the parent turn; there is no public queue-clear API.
		// The child is stopped separately below, so queued parent work cannot keep
		// the current turn alive while the native request is being cancelled.
		const active = state.current;
		ctx.abort();
		active.requestStop();
		await active.stopPromise;
		if (active.stopError) throw new Error(`native child stop failed; request remains unresolved: ${active.stopError}`);
	}});
  const control = async (action: string, message?: string) => {
    const active = state.current;
    if (action === "list") {
      const records = await taskStore.list();
      for (let index = 0; index < records.length; index += 1) records[index] = await taskStore.recoverStale(records[index].taskId) ?? records[index];
      return JSON.stringify({ active: active ? { requestId: active.requestId, childId: active.childId ?? null, role: active.nodeId, status: active.stopError ? "stop-failed" : active.stopPromise ? "stopping" : "running" } : null, stopped: stopLatch, records });
    }
    if (action === "inspect") {
      const candidate = message?.trim() ? await taskStore.get(message.trim()) : (await taskStore.list(1))[0];
      const record = candidate ? await taskStore.recoverStale(candidate.taskId) : undefined;
      if (!record) throw new Error("task record not found");
      return JSON.stringify(record);
    }
    if (action === "integrate") {
      if (profile.planning) throw new Error("planning policy cannot integrate or modify a checkout");
      if (!message?.trim()) throw new Error("integrate requires a completed handoffId");
      return JSON.stringify(await integrateHandoff(coordinatorCwd, message.trim(), configuredChecks()));
    }
    if (!active) throw new Error("no active native worker");
    if (action === "steer") {
      if (!message?.trim()) throw new Error("steer requires a message");
      const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
      const session = registry?.getRecord?.(active.childId ?? "")?.session;
      if (!session?.steer) throw new Error("active native child session is unavailable");
      await session.steer(message.trim()); return "Steering queued for the active child; consumption is not confirmed.";
    }
    if (action === "cancel") {
      stopLatch = true; active.requestStop(); await active.stopPromise;
      if (active.stopError) throw new Error(`native child stop failed: ${active.stopError}`);
      return active.childId ? "Stop acknowledged; terminal ownership remains tracked." : "Stop queued until child identity is available.";
    }
    throw new Error("action must be list, inspect, steer, or cancel; resume is unsupported");
  };
  const controlSchema = Type.Object({ action: Type.Union([Type.Literal("list"), Type.Literal("inspect"), Type.Literal("integrate"), Type.Literal("steer"), Type.Literal("cancel")]), message: Type.Optional(Type.String({ maxLength: 4000 })) });
  pi.registerTool({ name: "agent_control", label: "Agent control", description: "Inspect, steer, or cancel the owned native task.", parameters: controlSchema, async execute(_id, params) { try { return textResult(await control(params.action, params.message)); } catch (error) { return textResult(error instanceof Error ? error.message : String(error), true); } } });
	const captureParentInstructions = async (ctx: unknown): Promise<ParentInstructionCapture> => {
		const capture = await persistParentInstructionProvenance(ctx, profilePath, state.sessionKey);
		state.parentInstructionProvenancePath = capture.path;
		state.parentSessionId = capture.parentSessionId;
		state.parentSessionFile = capture.parentSessionFile;
		return capture;
	};
	pi.registerCommand("agent-control", { description: "List, inspect, steer, or cancel the owned native task.", handler: async (message, ctx) => {
		const capture = await captureParentInstructions(ctx);
		const [action, ...rest] = message.trim().split(/\s+/);
		if (action === "cancel") ctx.abort();
		ctx.ui.notify(`${await control(action, rest.join(" "))}\nPARENT_INSTRUCTION_PROVENANCE=${provenanceReport(capture)}`, "info");
	}});
	pi.registerCommand("workbench-instructions", { description: "Capture and report the loaded parent instruction-file provenance.", handler: async (_message, ctx) => {
		const capture = await captureParentInstructions(ctx);
		ctx.ui.notify(provenanceReport(capture), "info");
	}});
	pi.registerCommand("workbench-integrate", { description: "Explicitly apply a validated handoff with the configured approved checks.", handler: async (message, ctx) => {
		if (profile.planning) throw new Error("planning policy cannot integrate or modify a checkout");
		const taskId = message.trim();
		if (!taskId) throw new Error("workbench-integrate requires a completed handoffId");
		const result = await integrateHandoff(ctx.cwd, taskId, configuredChecks());
		ctx.ui.notify(JSON.stringify(result), result.status === "applied" ? "info" : "error");
	}});
	pi.registerCommand("workbench-steer", { description: "Steer the active native workbench scout.", handler: async (message: string, ctx) => {
		if (!state.current || !message.trim()) throw new Error("no active native workbench request");
		const registry = (globalThis as Record<PropertyKey, unknown>)[Symbol.for("pi-subagents:manager")] as TintinRegistry | undefined;
		const session = registry?.getRecord?.(state.current.ownerRunId)?.session;
		if (!session?.steer) throw new Error("active native child session is unavailable");
		await session.steer(message.trim());
		// Tintin's public steer() resolves after queueing the message. It does
		// not acknowledge that the child has consumed it, so report the honest
		// queued disposition to the interactive caller.
		ctx.ui.notify("Steering queued for the active child; consumption is not confirmed.", "info");
	}});
}
