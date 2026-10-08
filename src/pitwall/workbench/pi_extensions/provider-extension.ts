import { randomUUID, timingSafeEqual } from "node:crypto";
import { enforceRuntimeSettings, safetyReserveTokens } from "./runtime-settings.js";
import { dirname, join } from "node:path";
import { lstat, readFile, writeFile } from "node:fs/promises";
import { createAssistantMessageEventStream, lazyStream, type AssistantMessage, type AssistantMessageEvent, type Context, type Model, type SimpleStreamOptions, type AssistantMessageEventStream } from "@earendil-works/pi-ai";
import { streamSimple as completions } from "@earendil-works/pi-ai/api/openai-completions";
import { streamSimple as responses } from "@earendil-works/pi-ai/api/openai-responses";
import { streamSimple as anthropic } from "@earendil-works/pi-ai/api/anthropic-messages";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { SharedRequestAdmission } from "./shared-admission.js";
import { AccountBudgetAdmission, accountBudgetPolicyFromProfile, type AccountReservation } from "./account-budget.js";
import { appendAccounting, safeUsage, shapePayload } from "./accounting.js";
import { withResponseIdleTimeout } from "./timeouts.js";
import { validateWorkbenchProfileKeys, validateWorkbenchProfilePolicy, type CompiledProfile } from "./profile.js";

export type AdmissionProfile = {
  profile: { accountRef?: string; accountGroup?: string; accountMaxConcurrent?: number; accountInFlightTokenBudget?: number; accountUnknownUsage?: "hold" | "release"; provider: string; modelId?: string; endpoint?: string; apiKeyEnv?: string; api: "openai-completions" | "openai-responses" | "anthropic-messages"; resourceGroup: string; servedContextTokens?: number; maxCompletionTokens?: number; contextReserveTokens?: number; requestTimeoutMs?: number; requestInactivityTimeoutMs?: number; generationTimeoutMs?: number; allowProviderFallback?: boolean };
  providerConfig: Record<string, unknown>;
};
const estimateContextTokens = (context: Context): number => Math.ceil(Buffer.byteLength(JSON.stringify(context)) / 4);

function responseModelFromEvent(event: AssistantMessageEvent): string | undefined {
  const message = event.type === "done" ? event.message : event.type === "error" ? event.error : event.partial;
  // OpenAI-compatible streams preserve the selected model in `model` and put
  // the provider's response model in `responseModel`; other APIs may mutate
  // the partial message's `model` directly (for example at message_start).
  if (typeof message.responseModel === "string" && message.responseModel.length > 0) return message.responseModel;
  return typeof message.model === "string" && message.model.length > 0 ? message.model : undefined;
}

function responseModelMismatch(message: AssistantMessage, expectedModel: string, actualModel: string): AssistantMessage {
  return {
    ...message,
    model: expectedModel,
    responseModel: actualModel,
    stopReason: "error",
    errorMessage: `provider response model ${JSON.stringify(actualModel)} differs from selected model ${JSON.stringify(expectedModel)}`,
  };
}

function validateResponseModel(inner: AssistantMessageEventStream, model: Model<any>, abort: (reason: Error) => void): AssistantMessageEventStream {
  const validated = createAssistantMessageEventStream();
  void (async () => {
    let mismatch: AssistantMessage | undefined;
    try {
      for await (const event of inner) {
        const observedModel = responseModelFromEvent(event);
        if (!mismatch && observedModel && observedModel !== model.id) {
          const message = event.type === "done" ? event.message : event.type === "error" ? event.error : event.partial;
          mismatch = responseModelMismatch(message, model.id, observedModel);
          abort(new Error(mismatch.errorMessage));
          continue;
        }
        if (!mismatch) validated.push(event);
      }
      if (mismatch) validated.push({ type: "error", reason: "error", error: mismatch });
    } catch (error) {
      const message: AssistantMessage = {
        role: "assistant", content: [], api: model.api, provider: model.provider, model: model.id,
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
        stopReason: "error", errorMessage: error instanceof Error ? error.message : String(error), timestamp: Date.now(),
      };
      validated.push({ type: "error", reason: "error", error: message });
    }
  })();
  return validated;
}

export async function configureProviderProfile(compiled: CompiledProfile, cwd?: string): Promise<{ profilePath: string; env: { PITWALL_WORKBENCH_PROVIDER_PROFILE: string } }> {
  await enforceRuntimeSettings(compiled.agentDir, cwd, compiled.profile);
  const profilePath = join(dirname(compiled.modelsPath), "provider-profile.json");
  const content = JSON.stringify({ profile: compiled.profile, providerConfig: compiled.provider }) + "\n";
  try {
    const stat = await lstat(profilePath);
    if (!stat.isFile() || stat.isSymbolicLink() || (stat.mode & 0o077)) throw new Error("provider profile path is not a private regular file");
    if (await readFile(profilePath, "utf8") !== content) throw new Error("provider profile already contains different content");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    await writeFile(profilePath, content, { mode: 0o600, flag: "wx" });
  }
  return { profilePath, env: { PITWALL_WORKBENCH_PROVIDER_PROFILE: profilePath } };
}

export async function registerConfiguredProvider(pi: ExtensionAPI): Promise<void> {
  const profilePath = process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
  if (!profilePath) return;
  const stat = await lstat(profilePath);
  if (!stat.isFile() || stat.isSymbolicLink() || (stat.mode & 0o077) || stat.uid !== process.getuid?.()) throw new Error("provider profile must be a private user-owned regular file");
  const profile = JSON.parse(await readFile(profilePath, "utf8")) as AdmissionProfile;
  if (!profile || typeof profile !== "object" || !profile.profile || typeof profile.profile !== "object" || !profile.providerConfig || typeof profile.providerConfig !== "object" || typeof profile.profile.provider !== "string" || !["openai-completions", "openai-responses", "anthropic-messages"].includes(profile.profile.api) || typeof profile.profile.resourceGroup !== "string" || typeof profile.providerConfig.baseUrl !== "string" || !Array.isArray(profile.providerConfig.models)) throw new Error("provider profile is invalid");
  validateWorkbenchProfilePolicy(profile.profile);
  registerAdmissionProvider(pi, profile, profilePath);
}

export function registerAdmissionProvider(pi: ExtensionAPI, profile: AdmissionProfile, profilePath: string): void {
  validateWorkbenchProfileKeys(profile.profile);
  // Admission-only callers may provide a deliberately narrow profile shape.
  // Once the compiled policy field is present, validate the complete policy so
  // direct registration cannot bypass the no-fallback contract.
  if (Object.prototype.hasOwnProperty.call(profile.profile, "allowProviderFallback")) validateWorkbenchProfilePolicy(profile.profile);
  if (!profile.profile.resourceGroup) throw new Error("provider profile requires resourceGroup");
  const admission = new SharedRequestAdmission(profile.profile.resourceGroup);
  const accountPolicy = accountBudgetPolicyFromProfile(profile.profile);
  if (profile.profile.accountRef && !accountPolicy) throw new Error("hosted provider requires an explicit account budget policy");
  if (profile.profile.accountRef && (!profile.profile.modelId || !profile.profile.endpoint || !profile.profile.apiKeyEnv || profile.providerConfig.apiKey !== `$${profile.profile.apiKeyEnv}`)) {
    throw new Error("hosted provider requires exact model, endpoint, and credential affinity");
  }
  if (profile.profile.accountRef) {
    const configuredModels = profile.providerConfig.models;
    if (profile.providerConfig.api !== profile.profile.api || typeof profile.providerConfig.baseUrl !== "string" || new URL(profile.providerConfig.baseUrl).href !== new URL(profile.profile.endpoint!).href || !Array.isArray(configuredModels) || configuredModels.length !== 1 || !configuredModels[0] || typeof configuredModels[0] !== "object" || configuredModels[0].id !== profile.profile.modelId) {
      throw new Error("hosted provider configuration differs from selected profile");
    }
  }
  const accountBudget = accountPolicy ? new AccountBudgetAdmission(accountPolicy) : undefined;
  const accountingPath = join(dirname(profilePath), "native-accounting.jsonl");
  const original = profile.profile.api === "openai-responses" ? responses : profile.profile.api === "anthropic-messages" ? anthropic : completions;
  const streamSimple = (model: Model<any>, context: Context, options?: SimpleStreamOptions): AssistantMessageEventStream => {
    const signal = options?.signal;
    const queuedAt = Date.now();
    const identity = { requestId: randomUUID(), provider: profile.profile.provider, model: model.id, accountRef: profile.profile.accountRef ?? null, accountGroup: profile.profile.accountGroup ?? null, resourceGroup: profile.profile.resourceGroup };
    return lazyStream(model, async () => {
      if (model.provider !== profile.profile.provider || model.api !== profile.profile.api || (profile.profile.modelId && model.id !== profile.profile.modelId)) {
        throw new Error("selected provider/model differs from profile");
      }
      if (profile.profile.endpoint && new URL(model.baseUrl).href !== new URL(profile.profile.endpoint).href) {
        throw new Error("selected model endpoint differs from profile");
      }
      if (profile.profile.accountRef) {
        const expected = Buffer.from(process.env[profile.profile.apiKeyEnv!] ?? "");
        const supplied = Buffer.from(options?.apiKey ?? "");
        if (!expected.length || expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) {
          throw new Error("selected account credential differs from profile");
        }
      }
      // Pi's HTTP dispatcher enforces inactivity (configured by
      // requestInactivityTimeoutMs). The provider SDK timeout and this
      // controller enforce total generation duration separately.
      const generationTimeout = profile.profile.generationTimeoutMs ?? profile.profile.requestTimeoutMs ?? 120000;
      const inactivityTimeout = profile.profile.requestInactivityTimeoutMs ?? profile.profile.requestTimeoutMs;
      const timeoutController = new AbortController();
      const requestSignal = signal ? AbortSignal.any([signal, timeoutController.signal]) : timeoutController.signal;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let release: (() => Promise<void>) | undefined;
      let acquiredAt: number | undefined;
      let transportStartedAt: number | undefined;
      try { release = await admission.acquire(requestSignal); } catch (error) { clearTimeout(timer); throw error; }
      acquiredAt = Date.now();
      const queueWaitMs = acquiredAt - queuedAt;
      const contextBudget = profile.profile.servedContextTokens ?? model.contextWindow;
      const reserve = profile.profile.contextReserveTokens ?? safetyReserveTokens({ servedContextTokens: contextBudget, maxCompletionTokens: profile.profile.maxCompletionTokens ?? model.maxTokens });
      const estimated = estimateContextTokens(context);
      const requestedMax = Math.min(options?.maxTokens ?? model.maxTokens, profile.profile.maxCompletionTokens ?? model.maxTokens);
      if (estimated + reserve + requestedMax > contextBudget) { release(); clearTimeout(timer); throw new Error(`estimated context ${estimated} plus reserve/output exceeds profile context window ${contextBudget}`); }
      let reservation: AccountReservation | undefined;
      if (accountBudget) {
        try { reservation = await accountBudget.reserve(estimated + requestedMax, requestSignal); }
        catch (error) { release(); clearTimeout(timer); throw error; }
      }
      timer = setTimeout(() => timeoutController.abort(new Error("provider generation timeout")), generationTimeout);
      try {
        if (requestSignal.aborted) throw requestSignal.reason ?? new Error("Request cancelled");
        const names = context.tools?.map(tool => tool.name) ?? [];
        const role = names.includes("agent_task") ? "coordinator" : names.length === 4 && names.every(name => ["read", "grep", "find", "ls"].includes(name)) ? "scout" : "other";
        const wrappedOptions: SimpleStreamOptions = {
          ...options,
          signal: requestSignal,
          timeoutMs: generationTimeout,
          ...(inactivityTimeout !== undefined ? { fetch: withResponseIdleTimeout(options?.fetch ?? globalThis.fetch, inactivityTimeout) } : {}),
          maxTokens: requestedMax,
          // Workbench profiles own retry policy; hosted profiles currently use
          // maxAttempts=1, so a 401/429 must produce exactly one transport call.
          maxRetries: 0,
          onPayload: async (payload, payloadModel) => {
            const transformed = options?.onPayload ? await options.onPayload(payload, payloadModel) : payload;
            transportStartedAt ??= Date.now();
            await appendAccounting(accountingPath, { type: "native_request", ...identity, role, queuedAt, acquiredAt, transportStartedAt, queueWaitMs, estimatedContextTokens: estimated, payload: shapePayload(transformed ?? payload) });
            return transformed ?? payload;
          },
        };
        const transportModel = model.cost ? model : { ...model, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
        const inner = (original as unknown as (model: Model<any>, context: Context, options?: SimpleStreamOptions) => AssistantMessageEventStream)(transportModel, context, wrappedOptions);
        const validated = validateResponseModel(inner, model, reason => timeoutController.abort(reason));
        const settled = validated.result();
        void settled.then(async (message: AssistantMessage) => {
          // Stamp transport settlement before any account reconciliation or
          // accounting I/O. The interval is transport lifecycle time, not
          // ledger work after the provider has already settled.
          const settledAt = Date.now();
          if (message.stopReason === "error" || message.stopReason === "aborted") {
            const reconciliation = reservation && accountBudget ? await accountBudget.reconcile(reservation, undefined).catch(() => undefined) : undefined;
            await appendAccounting(accountingPath, { type: "native_settled", ...identity, role, queuedAt, acquiredAt, transportStartedAt, settledAt, usage: null, outcome: "error", ...(reconciliation ? { accountReconciliation: reconciliation } : {}) });
            return;
          }
          const usage = safeUsage(message.usage);
          let reconciliation: Awaited<ReturnType<AccountBudgetAdmission["reconcile"]>> | undefined; let reconciliationError: string | undefined;
          try { reconciliation = reservation && accountBudget ? await accountBudget.reconcile(reservation, usage ? { input: usage.input, output: usage.output, reasoning: usage.reasoning, totalTokens: usage.totalTokens } : undefined) : undefined; }
          catch (error) { reconciliationError = error instanceof Error ? error.message : String(error); }
          await appendAccounting(accountingPath, { type: "native_settled", ...identity, role, queuedAt, acquiredAt, transportStartedAt, settledAt, usage, ...(reconciliation ? { accountReconciliation: reconciliation } : {}), ...(reconciliationError ? { accountReconciliationError: reconciliationError } : {}) });
        }, async () => {
          // See the success branch: capture the provider rejection boundary
          // before releasing the account reservation or writing its record.
          const settledAt = Date.now();
          const reconciliation = reservation && accountBudget ? await accountBudget.reconcile(reservation, undefined).catch(() => undefined) : undefined;
          await appendAccounting(accountingPath, { type: "native_settled", ...identity, role, queuedAt, acquiredAt, transportStartedAt, settledAt, usage: null, outcome: "error", ...(reconciliation ? { accountReconciliation: reconciliation } : {}) });
        }).finally(async () => {
          clearTimeout(timer);
          // The admission child owns the kernel lease. Await its exit so the
          // recorded boundary is after the release request is confirmed by
          // the child, rather than before accounting or stdin close.
          await release?.();
          const releasedAt = Date.now();
          await appendAccounting(accountingPath, { type: "native_released", ...identity, role, queuedAt, acquiredAt, transportStartedAt, releasedAt });
        }).catch(() => undefined);
        return validated;
      } catch (error) {
        clearTimeout(timer); release(); if (reservation && accountBudget) await accountBudget.reconcile(reservation, undefined).catch(() => undefined);
        throw error;
      }
    });
  };
  pi.registerProvider(profile.profile.provider, { ...profile.providerConfig, api: profile.profile.api, streamSimple } as unknown as Parameters<ExtensionAPI["registerProvider"]>[1]);
}
