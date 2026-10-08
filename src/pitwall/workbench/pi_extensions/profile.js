import { mkdir, readdir, readFile, lstat, writeFile, open } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join, resolve } from "node:path";
import { deriveCompactionSettings } from "./runtime-settings.js";
const profileKeys = new Set(["provider", "modelId", "endpoint", "api", "apiKeyEnv", "keyless", "servedContextTokens", "maxCompletionTokens", "contextReserveTokens", "requestTimeoutMs", "requestInactivityTimeoutMs", "generationTimeoutMs", "toolTimeoutMs", "taskTimeoutMs", "inputModalities", "thinkingLevelMap", "samplingParams", "compat", "cost", "reasoning", "reasoningLevel", "resourceGroup", "allowProviderFallback", "accountRef", "accountGroup", "accountMaxConcurrent", "accountInFlightTokenBudget", "accountUnknownUsage"]);
const reservedCredentialEnvNames = new Set(["PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ", "TMPDIR", "NO_COLOR", "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "CDPATH", "PITWALL_WORKBENCH_NATIVE_PROFILE", "PITWALL_WORKBENCH_PROVIDER_PROFILE", "PITWALL_WORKBENCH_RESOURCE_DIR", "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR", "PITWALL_WORKBENCH_APPROVED_CHECKS", "PITWALL_WORKBENCH_ACCOUNTING_PATH", "PITWALL_WORKBENCH_RESTRICTED_CREDENTIAL_ENV", "PI_CODING_AGENT_DIR", "PI_TELEMETRY", "PI_OFFLINE"]);
const markerName = ".pi-workbench-owned.json";
const stable = (value) => Array.isArray(value) ? `[${value.map(stable).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(",")}}` : (JSON.stringify(value) ?? "null");
export function validateWorkbenchProfileKeys(profile) {
    if (!profile || typeof profile !== "object" || Array.isArray(profile))
        throw new Error("profile must be an object");
    for (const key of Object.keys(profile))
        if (!profileKeys.has(key))
            throw new Error(`unknown profile field: ${key}`);
}
function assertProfile(name, profile) {
    validateWorkbenchProfilePolicy(profile);
    if (!name || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(name))
        throw new Error("profile name must be lowercase kebab case");
}
export function validateWorkbenchProfilePolicy(profile) {
    validateWorkbenchProfileKeys(profile);
    assertProfilePolicy(profile);
}
function assertProfilePolicy(profile) {
    if (typeof profile.provider !== "string" || !profile.provider)
        throw new Error("profile requires exact provider string");
    if (typeof profile.modelId !== "string" || !profile.modelId || profile.modelId === "SET_FROM_SERVER_DISCOVERY" || /[*?]/.test(profile.modelId))
        throw new Error("profile requires an exact modelId");
    if (!["openai-completions", "openai-responses", "anthropic-messages"].includes(profile.api))
        throw new Error("unsupported api");
    if (profile.reasoningLevel && !["off", "minimal", "low", "medium", "high", "xhigh", "max"].includes(profile.reasoningLevel))
        throw new Error("unsupported reasoningLevel");
    if (profile.reasoning === false && profile.reasoningLevel && profile.reasoningLevel !== "off")
        throw new Error("reasoning capability is disabled for a non-off reasoning level");
    let endpoint;
    try {
        endpoint = new URL(profile.endpoint);
    }
    catch {
        throw new Error("profile endpoint must be an http(s) URL");
    }
    if (!(endpoint.protocol === "http:" || endpoint.protocol === "https:"))
        throw new Error("profile endpoint must be an http(s) URL");
    if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash)
        throw new Error("endpoint must not contain credentials, query parameters, or fragments; credentials must use apiKeyEnv");
    if (typeof profile.resourceGroup !== "string" || !profile.resourceGroup)
        throw new Error("resourceGroup is required");
    if (!Number.isSafeInteger(profile.servedContextTokens) || profile.servedContextTokens <= 0)
        throw new Error("servedContextTokens must be positive");
    if (!Number.isSafeInteger(profile.maxCompletionTokens) || profile.maxCompletionTokens <= 0 || profile.maxCompletionTokens >= profile.servedContextTokens)
        throw new Error("maxCompletionTokens must be below served context");
    if (profile.contextReserveTokens !== undefined && (!Number.isSafeInteger(profile.contextReserveTokens) || profile.contextReserveTokens <= 0 || profile.contextReserveTokens >= profile.servedContextTokens))
        throw new Error("contextReserveTokens must be positive and below served context");
    deriveCompactionSettings(profile);
    for (const [name, value] of [["requestTimeoutMs", profile.requestTimeoutMs], ["requestInactivityTimeoutMs", profile.requestInactivityTimeoutMs], ["generationTimeoutMs", profile.generationTimeoutMs], ["toolTimeoutMs", profile.toolTimeoutMs], ["taskTimeoutMs", profile.taskTimeoutMs]]) {
        if (value !== undefined && (!Number.isSafeInteger(value) || value <= 0 || value > 3_600_000))
            throw new Error(`${name} must be 1..3600000`);
    }
    if (profile.allowProviderFallback !== false)
        throw new Error("provider fallback must be false");
    if (profile.accountRef !== undefined && (typeof profile.accountRef !== "string" || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(profile.accountRef) || !profile.apiKeyEnv))
        throw new Error("accountRef requires an exact account name and apiKeyEnv");
    const accountBudgetFields = [profile.accountGroup, profile.accountMaxConcurrent, profile.accountInFlightTokenBudget, profile.accountUnknownUsage];
    if (accountBudgetFields.some((value) => value !== undefined) && (typeof profile.accountGroup !== "string" || typeof profile.accountMaxConcurrent !== "number" || typeof profile.accountInFlightTokenBudget !== "number" || !profile.accountUnknownUsage))
        throw new Error("hosted account budget policy must specify accountGroup, accountMaxConcurrent, inFlightTokenBudget, and unknownUsage together");
    if (profile.accountGroup !== undefined && !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(profile.accountGroup))
        throw new Error("accountGroup must be a valid shared identity");
    if (profile.accountMaxConcurrent !== undefined && (!Number.isSafeInteger(profile.accountMaxConcurrent) || profile.accountMaxConcurrent < 1))
        throw new Error("accountMaxConcurrent must be positive");
    if (profile.accountInFlightTokenBudget !== undefined && (!Number.isSafeInteger(profile.accountInFlightTokenBudget) || profile.accountInFlightTokenBudget < 1))
        throw new Error("accountInFlightTokenBudget must be positive");
    if (profile.accountUnknownUsage !== undefined && profile.accountUnknownUsage !== "hold" && profile.accountUnknownUsage !== "release")
        throw new Error("accountUnknownUsage must be hold or release");
    if (profile.apiKeyEnv && (!/^[A-Z][A-Z0-9_]*$/.test(profile.apiKeyEnv) || reservedCredentialEnvNames.has(profile.apiKeyEnv)))
        throw new Error("apiKeyEnv must be a non-reserved environment variable name");
    if (profile.apiKeyEnv && profile.keyless)
        throw new Error("keyless dummy policy cannot be combined with apiKeyEnv");
    if (!profile.apiKeyEnv && profile.keyless !== "dummy")
        throw new Error("keyless policy must be explicit as dummy when apiKeyEnv is absent");
    if (profile.inputModalities && (profile.inputModalities.length < 1 || profile.inputModalities.some((modality) => modality !== "text" && modality !== "image") || new Set(profile.inputModalities).size !== profile.inputModalities.length || !profile.inputModalities.includes("text")))
        throw new Error("inputModalities must be unique text or text,image");
    const thinkingLevelMap = profile.thinkingLevelMap;
    if (thinkingLevelMap && Object.keys(thinkingLevelMap).some((level) => !["off", "minimal", "low", "medium", "high", "xhigh", "max"].includes(level) || typeof thinkingLevelMap[level] !== "string"))
        throw new Error("thinkingLevelMap contains an unsupported level");
    if (profile.compat !== undefined && (!profile.compat || typeof profile.compat !== "object" || Array.isArray(profile.compat)))
        throw new Error("compat metadata must be an object");
}
export async function compileProfile(name, profile, agentDir) {
    assertProfile(name, profile);
    const isolatedDir = resolve(agentDir);
    try {
        if ((await lstat(isolatedDir)).isSymbolicLink() || (await lstat(isolatedDir)).isFile())
            throw new Error("agentDir must be a real directory");
    }
    catch (error) {
        if (error.code !== "ENOENT")
            throw error;
    }
    await mkdir(isolatedDir, { recursive: true, mode: 0o700 });
    const stat = await lstat(isolatedDir);
    if ((stat.mode & 0o077) !== 0)
        throw new Error("agentDir permissions are too broad");
    const provider = {
        baseUrl: profile.endpoint,
        api: profile.api,
        ...(profile.apiKeyEnv ? { apiKey: `$${profile.apiKeyEnv}` } : profile.keyless === "dummy" ? { apiKey: "dummy" } : {}), // pragma: allowlist secret
        models: [{
                id: profile.modelId,
                name: profile.modelId,
                reasoning: profile.reasoning ?? profile.reasoningLevel !== undefined,
                input: (profile.inputModalities?.length ? profile.inputModalities : ["text"]),
                ...(profile.cost ? { cost: profile.cost } : {}),
                contextWindow: profile.servedContextTokens,
                maxTokens: profile.maxCompletionTokens,
                ...(profile.thinkingLevelMap ? { thinkingLevelMap: profile.thinkingLevelMap } : {}),
                ...(profile.samplingParams ? { samplingParams: profile.samplingParams } : {}),
                ...(profile.compat ? { compat: profile.compat } : {}),
            }],
    };
    const modelsPath = join(isolatedDir, "models.json");
    const models = JSON.stringify({ providers: { [profile.provider]: provider } }, null, 2) + "\n";
    const profileHash = createHash("sha256").update(stable({ name, profile })).digest("hex");
    const markerPath = join(isolatedDir, markerName);
    try {
        const markerStat = await lstat(markerPath);
        if (!markerStat.isFile() || markerStat.isSymbolicLink())
            throw new Error("owned agentDir marker must be a regular file");
        const modelsStat = await lstat(modelsPath);
        if (!modelsStat.isFile() || modelsStat.isSymbolicLink())
            throw new Error("owned agentDir models file must be a regular file");
        const marker = JSON.parse(await readFile(markerPath, "utf8"));
        const current = await readFile(modelsPath, "utf8");
        if (marker.profileHash !== profileHash || marker.modelsHash !== createHash("sha256").update(current).digest("hex") || current !== models)
            throw new Error("owned agentDir has a profile or models conflict");
    }
    catch (error) {
        if (error.code !== "ENOENT")
            throw error;
        if ((await readdir(isolatedDir)).length)
            throw new Error("agentDir contains unowned files");
        const handle = await open(modelsPath, "wx", 0o600);
        await handle.writeFile(models);
        await handle.close();
        await writeFile(markerPath, JSON.stringify({ schemaVersion: 1, profileHash, modelsHash: createHash("sha256").update(models).digest("hex") }) + "\n", { mode: 0o600, flag: "wx" });
    }
    return { name, profile, agentDir: isolatedDir, modelsPath, provider };
}
export function profileFromConfig(config, name) {
    if (!config || typeof config !== "object" || !("profiles" in config) || config.schemaVersion !== 1)
        throw new Error("invalid workbench config schemaVersion");
    const profiles = config.profiles;
    if (!Object.prototype.hasOwnProperty.call(profiles, name))
        throw new Error(`profile not found: ${name}`);
    const profile = profiles[name];
    if (!profile)
        throw new Error(`profile not found: ${name}`);
    return profile;
}
