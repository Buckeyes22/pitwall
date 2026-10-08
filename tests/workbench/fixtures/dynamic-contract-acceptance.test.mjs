// Ported from packages/pi-workbench/tests/dynamic-contract-acceptance.test.ts (vitest -> node:test).
// Run by tests/workbench/test_dynamic_contract_acceptance.py; it reuses the Pi extension test support.
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe } from "node:test";
import { assertMatch, errorLike, keepEventLoopAlive, loadExtension, piTest as it, skipReason, waitFor } from "../pi_extensions/support.mjs";

keepEventLoopAlive();

const observerModule = skipReason ? {} : await loadExtension("comparison-lifecycle-observer.js");
const observer = observerModule.default;
const providerModule = skipReason ? {} : await loadExtension("provider-extension.js");
const { registerConfiguredProvider, registerAdmissionProvider } = providerModule;
const native = skipReason ? {} : await loadExtension("native-extension.js");
const { makeTintinDelegation } = native;
const registerNativeExtension = native.default;
const restrictedModule = skipReason ? {} : await loadExtension("restricted.js");
const { registerRestrictedBashTool } = restrictedModule;

class Bus {
    handlers = new Map();
    on(channel, handler) {
        const set = this.handlers.get(channel) ?? new Set();
        set.add(handler);
        this.handlers.set(channel, set);
        return () => set.delete(handler);
    }
    emit(channel, value) { for (const handler of [...(this.handlers.get(channel) ?? [])]) handler(value); }
    listenerCount(channel) { return this.handlers.get(channel)?.size ?? 0; }
}

const piFor = (bus) => ({ events: bus });

function lifecycleFor() {
    const lifecycle = new Map();
    const pi = {
        on(channel, handler) {
            const set = lifecycle.get(channel) ?? new Set();
            set.add(handler);
            lifecycle.set(channel, set);
            return () => set.delete(handler);
        },
    };
    return { lifecycle, pi };
}

const tempDirs = [];
async function tempRoot(prefix) {
    const dir = await mkdtemp(join(tmpdir(), prefix));
    tempDirs.push(dir);
    return dir;
}
afterEach(async () => {
    for (const dir of tempDirs.splice(0)) await rm(dir, { recursive: true, force: true });
    delete globalThis[Symbol.for("pi-subagents:manager")];
});

async function writeNativeProfile(dir, extra = {}) {
    const profilePath = join(dir, "native-profile.json");
    const { profile: profileOverride, ...rest } = extra;
    const profile = { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture", ...profileOverride };
    const providerConfig = { baseUrl: "http://127.0.0.1:1/v1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] };
    await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", ...rest, profile, providerConfig }));
    if (rest.planning === true) {
        // Planning mode validates the managed read-only role files beside the profile.
        const role = (name) => `---\nname: ${name}\nmodel: "${profile.provider}/${profile.modelId}"\ntools: read, grep, find, ls\nextensions: false\nskills: false\ninherit_context: false\nisolated: true\nallowed_subagents: none\nisolation: off\n---\nrole\n`;
        await mkdir(join(dir, "agents"), { recursive: true });
        for (const name of ["workbench-scout", "workbench-worker", "workbench-reviewer"]) await writeFile(join(dir, "agents", `${name}.md`), role(name));
    }
    return profilePath;
}

async function withNativeProfile(extra, run) {
    const dir = await tempRoot("pi-dynamic-contract-");
    const profilePath = await writeNativeProfile(dir, extra);
    const previous = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
    process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
    try {
        await run(profilePath, dir);
    } finally {
        if (previous === undefined) delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        else process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = previous;
    }
}

describe("dynamic lifecycle observer delivery and file errors", () => {
    const previousPath = process.env.COMPARISON_LIFECYCLE_PATH;
    afterEach(() => {
        if (previousPath === undefined) delete process.env.COMPARISON_LIFECYCLE_PATH;
        else process.env.COMPARISON_LIFECYCLE_PATH = previousPath;
    });

    async function subscribe(dir) {
        const handlers = new Map();
        process.env.COMPARISON_LIFECYCLE_PATH = join(dir, "lifecycle.jsonl");
        observer({ events: { on: (event, handler) => { handlers.set(event, handler); return () => handlers.delete(event); } } });
        return handlers;
    }
    async function records(dir) {
        return (await readFile(join(dir, "lifecycle.jsonl"), "utf8")).trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
    }

    it("registers no lifecycle handlers when COMPARISON_LIFECYCLE_PATH is unset", () => {
        delete process.env.COMPARISON_LIFECYCLE_PATH;
        const subscriptions = [];
        observer({ events: { on: (event) => { subscriptions.push(event); return () => undefined; } } });
        assert.deepStrictEqual(subscriptions, []);
    });

    it("delivers the tintin failed event and resolves identity through fallback keys", async () => {
        const dir = await tempRoot("pi-observer-tintin-fallback-");
        const handlers = await subscribe(dir);
        handlers.get("subagents:failed")({ agentId: "agent-9", status: "failed", durationMs: 0, error: "secret failure" });
        handlers.get("subagents:started")({ runId: "run-4", status: 7, durationMs: Number.NaN });
        const saved = await records(dir);
        assert.strictEqual(saved.length, 2);
        assertMatch(saved[0], { event: "subagents:failed", id: "agent-9", status: "failed", durationMs: 0 });
        assertMatch(saved[1], { event: "subagents:started", id: "run-4" });
        assert.ok(!("status" in saved[1]));
        assert.ok(!("durationMs" in saved[1]));
        assert.ok(!JSON.stringify(saved).includes("secret"));
    });

    it("maps delegation response status and slash isError onto terminal events", async () => {
        const dir = await tempRoot("pi-observer-terminal-mapping-");
        const handlers = await subscribe(dir);
        handlers.get("prompt-template:subagent:started")({ requestId: "req-a" });
        handlers.get("prompt-template:subagent:response")({ requestId: "req-a", status: "completed", output: "secret output" });
        handlers.get("prompt-template:subagent:response")({ requestId: "req-b", status: "error", error: "secret error" });
        handlers.get("subagent:slash:started")({ runId: "slash-1" });
        handlers.get("subagent:slash:response")({ runId: "slash-1", isError: true, result: "secret" });
        handlers.get("subagent:slash:response")({ runId: "slash-2", isError: false });
        const saved = await records(dir);
        assert.strictEqual(saved.length, 6);
        assertMatch(saved[0], { event: "subagents:started", id: "nico-delegation:req-a", sourceEvent: "prompt-template:subagent:started" });
        assertMatch(saved[1], { event: "subagents:completed", id: "nico-delegation:req-a", sourceEvent: "prompt-template:subagent:response" });
        assertMatch(saved[2], { event: "subagents:failed", id: "nico-delegation:req-b", sourceEvent: "prompt-template:subagent:response" });
        assertMatch(saved[3], { event: "subagents:started", id: "nico-slash:slash-1", sourceEvent: "subagent:slash:started" });
        assertMatch(saved[4], { event: "subagents:failed", id: "nico-slash:slash-1", sourceEvent: "subagent:slash:response" });
        assertMatch(saved[5], { event: "subagents:completed", id: "nico-slash:slash-2" });
        assert.ok(!JSON.stringify(saved).includes("secret"));
    });

    it("drops identities that are absent, empty, or non-string", async () => {
        const dir = await tempRoot("pi-observer-drops-");
        const handlers = await subscribe(dir);
        handlers.get("subagents:started")({ id: 7 });
        handlers.get("subagents:started")({});
        handlers.get("subagents:started")({ id: "" });
        handlers.get("subagent:async-started")({ id: "" });
        handlers.get("subagents:completed")({ id: "survivor" });
        const saved = await records(dir);
        assert.strictEqual(saved.length, 1);
        assertMatch(saved[0], { event: "subagents:completed", id: "survivor" });
    });

    it("writes lifecycle records as a private file and swallows append failures", async () => {
        const dir = await tempRoot("pi-observer-file-errors-");
        const handlers = await subscribe(dir);
        handlers.get("subagents:started")({ id: "private-child" });
        assert.strictEqual((await stat(join(dir, "lifecycle.jsonl"))).mode & 0o777, 0o600);

        process.env.COMPARISON_LIFECYCLE_PATH = join(dir, "absent-parent", "nested", "lifecycle.jsonl");
        const failing = new Map();
        observer({ events: { on: (event, handler) => { failing.set(event, handler); return () => failing.delete(event); } } });
        assert.doesNotThrow(() => failing.get("subagents:started")({ id: "unwritable" }));
        assert.doesNotThrow(() => failing.get("subagent:slash:response")({ runId: "unwritable", isError: true }));
    });
});

describe("native correlated rpc reply and cleanup contracts", () => {
    it("resolves a rejected spawn reply, clears active state, and removes correlation listeners", async () => {
        const bus = new Bus();
        const holder = { current: undefined };
        let requestId = "";
        bus.on("subagents:rpc:spawn", (raw) => { requestId = raw.requestId; bus.emit(`subagents:rpc:spawn:reply:${requestId}`, { success: false, error: "spawn rejected" }); });
        const result = await makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, undefined);
        assertMatch(result, { status: "failed", error: "spawn rejected" });
        assert.strictEqual(holder.current, undefined);
        assert.strictEqual(bus.listenerCount(`subagents:rpc:spawn:reply:${requestId}`), 0);
        assert.strictEqual(bus.listenerCount("subagents:completed"), 0);
        assert.strictEqual(bus.listenerCount("subagents:failed"), 0);
    });

    it("ignores a stop reply for an uncorrelated requestId and settles only on the generated topic", async () => {
        const bus = new Bus();
        const holder = { current: undefined };
        const controller = new AbortController();
        let stopRequestId = "";
        globalThis[Symbol.for("pi-subagents:manager")] = { getRecord: (id) => id === "child-x" ? { session: { clearQueue: async () => undefined } } : undefined };
        bus.on("subagents:rpc:spawn", (raw) => bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: "child-x" } }));
        bus.on("subagents:rpc:stop", (raw) => { stopRequestId = raw.requestId; });
        const result = makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, controller.signal);
        await waitFor(() => assert.strictEqual(holder.current?.childId, "child-x"));
        controller.abort();
        await waitFor(() => assert.notStrictEqual(stopRequestId, ""));
        bus.emit("subagents:rpc:stop:reply:uncorrelated", { success: true });
        await new Promise((resolve) => setTimeout(resolve, 20));
        assert.strictEqual(holder.current?.stopError, undefined);
        assert.strictEqual(await Promise.race([result.then(() => "settled"), new Promise((resolve) => setTimeout(() => resolve("pending"), 0))]), "pending");
        bus.emit(`subagents:rpc:stop:reply:${stopRequestId}`, { success: true });
        await waitFor(() => assert.strictEqual(bus.listenerCount(`subagents:rpc:stop:reply:${stopRequestId}`), 0));
        assert.strictEqual(holder.current?.stopError, undefined);
        bus.emit("subagents:failed", { id: "child-x", status: "aborted", error: "cancelled" });
        assertMatch(await result, { status: "cancelled" });
        assert.strictEqual(bus.listenerCount("subagents:completed"), 0);
        assert.strictEqual(bus.listenerCount("subagents:failed"), 0);
    });

    it("records a missing manager record as a stop failure without settling the request", async () => {
        const bus = new Bus();
        const holder = { current: undefined };
        const controller = new AbortController();
        bus.on("subagents:rpc:spawn", (raw) => bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: "child-missing" } }));
        bus.on("subagents:rpc:stop", (raw) => bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: false }));
        const result = makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, controller.signal);
        await waitFor(() => assert.strictEqual(holder.current?.childId, "child-missing"));
        controller.abort();
        await waitFor(() => assert.match(holder.current?.stopError ?? "", /record is unavailable/));
        assert.strictEqual(await Promise.race([result.then(() => "settled"), new Promise((resolve) => setTimeout(() => resolve("pending"), 0))]), "pending");
        bus.emit("subagents:failed", { id: "child-missing", status: "aborted" });
        assertMatch(await result, { status: "cancelled" });
    });

    it("treats a correlated stop reply without an error message as a failed stop", async () => {
        const bus = new Bus();
        const holder = { current: undefined };
        const controller = new AbortController();
        globalThis[Symbol.for("pi-subagents:manager")] = { getRecord: (id) => id === "child-default-error" ? { session: { clearQueue: async () => undefined } } : undefined };
        bus.on("subagents:rpc:spawn", (raw) => bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: "child-default-error" } }));
        bus.on("subagents:rpc:stop", (raw) => bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: false }));
        const result = makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, controller.signal);
        await waitFor(() => assert.strictEqual(holder.current?.childId, "child-default-error"));
        controller.abort();
        await waitFor(() => assert.strictEqual(holder.current?.stopError, "native stop failed"));
        bus.emit("subagents:failed", { id: "child-default-error", status: "aborted" });
        assertMatch(await result, { status: "cancelled" });
    });

    it("unsubscribes the ping reply and keeps the backend not-ready for a wrong version", async () => {
        await withNativeProfile({}, async (profilePath, dir) => {
            const bus = new Bus();
            const { lifecycle, pi } = lifecycleFor();
            let taskTool;
            const fakePi = { ...pi, events: bus, registerProvider() {}, registerTool(value) { if (value.name === "agent_task") taskTool = value; }, registerCommand() {}, setActiveTools() {} };
            const pingTopics = [];
            bus.on("subagents:rpc:ping", (raw) => { pingTopics.push(raw.requestId); bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 1 } }); });
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? []) await handler({}, {});
            assert.strictEqual(pingTopics.length, 1);
            assert.strictEqual(bus.listenerCount(`subagents:rpc:ping:reply:${pingTopics[0]}`), 0);
            const outcome = await taskTool.execute("ping-wrong-version", { task: "delegate" }, undefined, undefined, { cwd: dir });
            assert.strictEqual(outcome.isError, true);
            assert.ok(outcome.content[0].text.includes("native subagent backend is not ready"));
        });
    });
});

describe("dynamic tool_call policy combinations", () => {
    it("applies both the tool deadline and the planning block when both are enabled", async () => {
        await withNativeProfile({ planning: true, profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture", toolTimeoutMs: 100 }, profileName: "fixture" }, async () => {
            const bus = new Bus();
            const { lifecycle, pi } = lifecycleFor();
            const fakePi = { ...pi, events: bus, registerProvider() {}, registerTool() {}, registerCommand() {}, setActiveTools() {} };
            bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
            await registerNativeExtension(fakePi);
            const handlers = [...(lifecycle.get("tool_call") ?? [])];
            assert.strictEqual(handlers.length, 2);
            const deadlineEvent = { toolName: "bash", input: { command: "sleep 5", timeout: 30 } };
            for (const handler of handlers) await handler(deadlineEvent, {});
            assert.strictEqual(deadlineEvent.input.timeout, 0.1);
            const writeEvent = { toolName: "write", input: { path: "/tmp/forbidden", content: "blocked" } };
            const writeResults = [];
            for (const handler of handlers) writeResults.push(await handler(writeEvent, {}));
            const blocked = writeResults.filter(Boolean);
            assert.strictEqual(blocked.length, 1);
            assertMatch(blocked[0], { block: true, terminate: true });
            const readEvent = { toolName: "read", input: { path: "/tmp/allowed" } };
            for (const handler of handlers) assert.strictEqual(await handler(readEvent, {}), undefined);
        });
    });

    it("registers no tool_call policy when neither deadline nor planning is configured", async () => {
        await withNativeProfile({}, async () => {
            const bus = new Bus();
            const { lifecycle, pi } = lifecycleFor();
            const fakePi = { ...pi, events: bus, registerProvider() {}, registerTool() {}, registerCommand() {}, setActiveTools() {} };
            bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
            await registerNativeExtension(fakePi);
            assert.deepStrictEqual([...(lifecycle.get("tool_call") ?? [])], []);
        });
    });
});

describe("provider registration contract", () => {
    const previous = process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
    afterEach(() => {
        if (previous === undefined) delete process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
        else process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE = previous;
    });

    it("registers the validated profile provider identity, providerConfig, api, and stream wrapper", async () => {
        const dir = await tempRoot("pi-provider-registration-");
        const profilePath = join(dir, "provider-profile.json");
        const profile = { profile: { provider: "profile-provider-x", modelId: "fixture-model", endpoint: "http://127.0.0.1:1/v1", api: "openai-responses", keyless: "dummy", servedContextTokens: 32768, maxCompletionTokens: 4096, resourceGroup: "profile-registration", allowProviderFallback: false }, providerConfig: { baseUrl: "http://127.0.0.1:1/v1", api: "openai-responses", models: [], marker: "profile-config" } };
        // The provider extension accepts only a private user-owned regular profile file.
        await writeFile(profilePath, `${JSON.stringify(profile)}\n`, { mode: 0o600 });
        const calls = [];
        const pi = { registerProvider: (name, config) => calls.push({ name, config }) };
        delete process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
        await registerConfiguredProvider(pi);
        assert.deepStrictEqual(calls, []);
        process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE = profilePath;
        await registerConfiguredProvider(pi);
        assert.strictEqual(calls.length, 1);
        assert.strictEqual(calls[0].name, "profile-provider-x");
        assertMatch(calls[0].config, { baseUrl: "http://127.0.0.1:1/v1", marker: "profile-config", api: "openai-responses" });
        assert.strictEqual(typeof calls[0].config.streamSimple, "function");
    });

    it("rejects admission registration without a resourceGroup", () => {
        assert.throws(() => registerAdmissionProvider({ registerProvider: () => undefined }, { profile: { provider: "fixture", api: "openai-completions", resourceGroup: "" }, providerConfig: { baseUrl: "http://127.0.0.1:1/v1", api: "openai-completions", models: [] } }, "/tmp/no-resource-group.json"), errorLike("resourceGroup"));
    });
});

describe("restricted bash tool registration", () => {
    const keys = ["PITWALL_WORKBENCH_RESTRICTED", "PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT", "PI_CODING_AGENT_DIR", "PITWALL_WORKBENCH_RESOURCE_DIR"];
    afterEach(() => { for (const key of keys) delete process.env[key]; });
    const piWithTools = () => { const tools = []; return { tools, pi: { registerTool: (tool) => { tools.push(tool); } } }; };

    it("registers nothing unless PITWALL_WORKBENCH_RESTRICTED is exactly 1", () => {
        for (const value of [undefined, "0", "true"]) {
            if (value === undefined) delete process.env.PITWALL_WORKBENCH_RESTRICTED;
            else process.env.PITWALL_WORKBENCH_RESTRICTED = value;
            const { tools, pi } = piWithTools();
            registerRestrictedBashTool(pi);
            assert.deepStrictEqual(tools, []);
        }
    });

    it("refuses to register without the runtime, agent, and admission directories", () => {
        process.env.PITWALL_WORKBENCH_RESTRICTED = "1";
        const { tools, pi } = piWithTools();
        assert.throws(() => registerRestrictedBashTool(pi), errorLike("restricted tool boundary requires runtime, agent, and admission directories"));
        assert.deepStrictEqual(tools, []);
    });

    it("registers exactly one bash tool when restricted with its directories", async () => {
        const root = await tempRoot("pi-restricted-registration-");
        process.env.PITWALL_WORKBENCH_RESTRICTED = "1";
        process.env.PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT = join(root, "runtime");
        process.env.PI_CODING_AGENT_DIR = join(root, "agent");
        process.env.PITWALL_WORKBENCH_RESOURCE_DIR = join(root, "admission");
        const { tools, pi } = piWithTools();
        registerRestrictedBashTool(pi);
        assert.deepStrictEqual(tools.map((tool) => tool.name), ["bash"]);
    });
});
