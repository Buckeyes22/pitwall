// Ported from packages/pi-workbench/tests/native-extension.test.ts (vitest -> node:test).
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm, readdir, readFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { describe, it, mock } from "node:test";
import { any, arrayContaining, assertMatch, errorLike, importPi, keepEventLoopAlive, loadExtension, objectContaining, piTest, skipReason, stringContaining, stringMatching, waitFor } from "./support.mjs";

keepEventLoopAlive();

const native = skipReason ? {} : await loadExtension("native-extension.js");
const { childInstructionProvenance, credentialClassForNativeProfile, installTintinChildToolDeadline, makeTintinDelegation } = native;
const registerNativeExtension = native.default;
const { TaskRecordStore } = skipReason ? {} : await loadExtension("task-record.js");
const { setprivSupportsSeccompFilter } = skipReason ? {} : await loadExtension("setpriv.js");
// The real seccomp boundary needs util-linux 2.41+ (same gate as tests/workbench/test_restricted.py).
const seccompSkip = skipReason || setprivSupportsSeccompFilter()
    ? undefined
    : "/usr/bin/setpriv lacks --seccomp-filter (util-linux 2.41+ required)";
const { createBashToolDefinition } = skipReason ? {} : await importPi("@earendil-works/pi-coding-agent");

class Bus {
    handlers = new Map();
    on(channel, handler) {
        const set = this.handlers.get(channel) ?? new Set();
        set.add(handler);
        this.handlers.set(channel, set);
        return () => set.delete(handler);
    }
    emit(channel, value) { for (const handler of [...(this.handlers.get(channel) ?? [])])
        handler(value); }
}
const piFor = (bus) => ({ events: bus });
const state = () => ({ current: undefined });
piTest("marks partial child instruction-loader reads incomplete", async () => {
    const key = Symbol.for("pi-subagents:manager");
    const previous = globalThis[key];
    try {
        globalThis[key] = { getRecord: () => ({ session: { resourceLoader: { getAgentsFiles: () => ({}), getSystemPrompt: () => "system", getAppendSystemPrompt: () => ["append"] } } }) };
        const partial = await childInstructionProvenance("partial-child");
        assertMatch(partial, { complete: false, source: "resource-loader", files: arrayContaining([{ path: "<system-prompt>", sha256: any(String), bytes: 6, estimated: false }]) });
        assert.ok(partial?.reason.includes("agentsFiles"));
        assert.ok(partial?.reason.includes("getSystemPromptSource"));
        assert.ok(partial?.reason.includes("getAppendSystemPromptSources"));
        globalThis[key] = { getRecord: () => ({ session: { resourceLoader: { getAgentsFiles: () => ({ agentsFiles: [] }) } } }) };
        const absent = await childInstructionProvenance("absent-getters");
        assertMatch(absent, { complete: false });
        assert.ok(absent?.reason.includes("getSystemPrompt"));
        assert.ok(absent?.reason.includes("getAppendSystemPrompt"));
    }
    finally {
        if (previous === undefined)
            delete globalThis[key];
        else
            globalThis[key] = previous;
    }
});
piTest("native lineage records distinguish keyless dummy credentials from environment references", () => {
    assert.strictEqual(credentialClassForNativeProfile({ apiKey: "dummy" }), "keyless"); // pragma: allowlist secret
    assert.strictEqual(credentialClassForNativeProfile({ apiKey: "$PITWALL_PI_LOCAL_KEY" }), "api-key-env"); // pragma: allowlist secret
    assert.strictEqual(credentialClassForNativeProfile({}), "keyless");
});
piTest("native startup rejects unsupported or mismatched backend profile schema before registration", async () => {
    const root = await mkdtemp(join(tmpdir(), "pi-native-schema-gate-"));
    const profilePath = join(root, "native-profile.json");
    const previous = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
    try {
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        const base = {
            schemaVersion: 1,
            backend: "@tintinweb/pi-subagents",
            version: "0.19.0",
            profileName: "fixture",
            profile: { provider: "fixture", modelId: "fixture-model", api: "openai-completions", resourceGroup: "fixture" },
            providerConfig: { baseUrl: "http://127.0.0.1:1/v1", api: "openai-completions", models: [{ id: "fixture-model", api: "anthropic-messages" }] },
        };
        await writeFile(profilePath, JSON.stringify({ ...base, version: "0.99.0" }));
        await await assert.rejects(registerNativeExtension({}), errorLike(/schemaVersion 1/));
        await writeFile(profilePath, JSON.stringify(base));
        await await assert.rejects(registerNativeExtension({}), errorLike(/model API conflicts/));
        const valid = { ...base, providerConfig: { ...base.providerConfig, models: [{ id: "fixture-model", api: "openai-completions" }] } };
        await writeFile(profilePath, JSON.stringify(valid));
        await assert.strictEqual((await registerNativeExtension({ registerProvider() { }, on() { }, registerTool() { }, registerCommand() { }, setActiveTools() { } })), undefined);
        await writeFile(profilePath, JSON.stringify({ ...valid, plannning: true }));
        await await assert.rejects(registerNativeExtension({}), errorLike(/unknown native profile field: plannning/));
    }
    finally {
        if (previous === undefined)
            delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        else
            process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = previous;
        await rm(root, { recursive: true, force: true });
    }
});
piTest("installs the shell deadline through the public isolated child AgentSession hook", async () => {
    const calls = [];
    const args = { command: "sleep 5", timeout: 30 };
    const record = { session: { agent: { beforeToolCall: async (context) => { calls.push(context); return undefined; } } } };
    assert.strictEqual(installTintinChildToolDeadline(record, 100), true);
    await record.session.agent.beforeToolCall({ toolCall: { name: "bash" }, args }, new AbortController().signal);
    assert.strictEqual(args.timeout, 0.1);
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(installTintinChildToolDeadline({ session: {} }, 100), false);
});
piTest("wraps a restricted native child bash tool with the network-denying seccomp boundary", seccompSkip ? { skip: seccompSkip } : {}, async () => {
    const dir = await mkdtemp(join(tmpdir(), "pi-native-restricted-tool-"));
    const agentDir = join(dir, "agent"), runtimeRoot = join(dir, "runtime"), admissionDir = join(dir, "admission"), accountDir = join(dir, "account");
    await Promise.all([agentDir, runtimeRoot, admissionDir, accountDir].map(path => mkdir(path)));
    const names = ["PITWALL_WORKBENCH_RESTRICTED", "PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT", "PI_CODING_AGENT_DIR", "PITWALL_WORKBENCH_RESOURCE_DIR", "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR"];
    const previous = Object.fromEntries(names.map(name => [name, process.env[name]]));
    Object.assign(process.env, { PITWALL_WORKBENCH_RESTRICTED: "1", PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT: runtimeRoot, PI_CODING_AGENT_DIR: agentDir, PITWALL_WORKBENCH_RESOURCE_DIR: admissionDir, PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR: accountDir });
    try {
        const tool = { name: "bash", execute: async () => { throw new Error("unwrapped native shell"); } };
        const record = { session: { agent: { state: { tools: [tool] } } } };
        assert.strictEqual(installTintinChildToolDeadline(record, undefined, dir), true);
        const output = [];
        const result = await tool.execute("network", { command: "node -e \"fetch('http://127.0.0.1:1').then(()=>process.exit(0)).catch(()=>{require('node:fs').writeFileSync(1,'NETWORK_DENIED\\n');process.exit(7)})\"" }, undefined, (update) => output.push(Buffer.from(update.content?.[0]?.text ?? "")), { cwd: dir, sessionManager: { getSessionId: () => "native-test", getSessionFile: () => undefined } });
        // Pi 1.x reports a non-zero exit as an error result instead of throwing.
        assert.strictEqual(result.isError, true);
        assert.ok(result.content[0].text.includes("Command exited with code 7"));
        assert.ok((Buffer.concat(output).toString()).includes("NETWORK_DENIED"));
    }
    finally {
        for (const name of names) {
            const value = previous[name];
            if (value === undefined)
                delete process.env[name];
            else
                process.env[name] = value;
        }
        await rm(dir, { recursive: true, force: true });
    }
});
piTest("fails closed when a planning profile has a writable managed child definition", async () => {
    const dir = await mkdtemp(join(tmpdir(), "pi-native-planning-profile-gate-"));
    const profilePath = join(dir, "native-profile.json");
    const role = (name, tools, model = "fixture/model") => `---\nname: ${name}\nmodel: "${model}"\ntools: ${tools}\nextensions: false\nskills: false\ninherit_context: false\nisolated: true\nallowed_subagents: none\nisolation: off\n---\nrole\n`;
    await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", planning: true, profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model" }] } }));
    await mkdir(join(dir, "agents"), { recursive: true });
    for (const name of ["workbench-scout", "workbench-reviewer"])
        await writeFile(join(dir, "agents", `${name}.md`), role(name, "read, grep, find, ls"));
    await writeFile(join(dir, "agents", "workbench-worker.md"), role("workbench-worker", "read, edit, write, bash"));
    const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
    process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
    try {
        await await assert.rejects(registerNativeExtension({}), errorLike("must expose only read, grep, find, and ls"));
    }
    finally {
        if (oldProfile === undefined)
            delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        else
            process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
        await rm(dir, { recursive: true, force: true });
    }
});
piTest("fails closed when a planning child is bound to an alternate model", async () => {
    const dir = await mkdtemp(join(tmpdir(), "pi-native-planning-model-gate-"));
    const profilePath = join(dir, "native-profile.json");
    const role = (name, model) => `---\nname: ${name}\nmodel: "${model}"\ntools: read, grep, find, ls\nextensions: false\nskills: false\ninherit_context: false\nisolated: true\nallowed_subagents: none\nisolation: off\n---\nrole\n`;
    await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", planning: true, profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model" }] } }));
    await mkdir(join(dir, "agents"), { recursive: true });
    await writeFile(join(dir, "agents", "workbench-scout.md"), role("workbench-scout", "fixture/model"));
    await writeFile(join(dir, "agents", "workbench-reviewer.md"), role("workbench-reviewer", "fixture/model"));
    await writeFile(join(dir, "agents", "workbench-worker.md"), role("workbench-worker", "fixture/alternate-model"));
    const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
    process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
    try {
        await await assert.rejects(registerNativeExtension({}), errorLike("exact model fixture/model"));
    }
    finally {
        if (oldProfile === undefined)
            delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        else
            process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
        await rm(dir, { recursive: true, force: true });
    }
});
piTest("captures parent instruction provenance through the supported command context", async () => {
    const dir = await mkdtemp(join(tmpdir(), "pi-workbench-parent-provenance-test-"));
    const profilePath = join(dir, "native-profile.json");
    await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
    const bus = new Bus();
    const commands = new Map();
    const fakePi = { events: bus, on() { }, registerProvider() { }, registerTool() { }, registerCommand(name, value) { commands.set(name, value); }, setActiveTools() { } };
    const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
    process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
    try {
        await registerNativeExtension(fakePi);
        const notices = [];
        await commands.get("workbench-instructions").handler("", { ui: { notify: (message) => notices.push(message) }, getSystemPromptOptions: () => ({ contextFiles: [{ path: "/fixture/AGENTS.md", content: "loaded parent instruction" }] }), getSystemPrompt: () => "aggregate prompt" });
        const files = (await readdir(dir)).filter(file => /^parent-instruction-provenance-[0-9a-f]+-[0-9a-f]+\.json$/.test(file));
        assert.strictEqual(files.length, 1);
        const saved = JSON.parse(await readFile(join(dir, files[0]), "utf8"));
        assertMatch(saved, { schemaVersion: 1, parentSessionId: stringMatching(/^extension-/), provenance: { source: "public-context-files", complete: true, files: [{ path: "/fixture/AGENTS.md", estimated: false }] } });
        assert.ok(notices[0].includes(files[0]));
        assert.ok(!notices[0].includes("loaded parent instruction"));
        await commands.get("workbench-instructions").handler("", { ui: { notify: (message) => notices.push(message) }, getSystemPromptOptions: () => ({ contextFiles: [{ path: "/fixture/AGENTS.md", content: "changed parent instruction" }], customPrompt: "inline custom instruction", appendSystemPrompt: "inline append instruction" }) });
        const after = (await readdir(dir)).filter(file => /^parent-instruction-provenance-[0-9a-f]+-[0-9a-f]+\.json$/.test(file));
        assert.strictEqual(after.length, 2);
        const inline = JSON.parse(await readFile(join(dir, after.find(file => file !== files[0])), "utf8"));
        assertMatch(inline, { provenance: { complete: false, inline: [{ source: "customPrompt" }, { source: "appendSystemPrompt" }] } });
        assert.ok(!(JSON.stringify(inline)).includes("inline custom instruction"));
        assert.deepStrictEqual(JSON.parse(await readFile(join(dir, files[0]), "utf8")), saved);
        await commands.get("workbench-instructions").handler("", { ui: { notify: (message) => notices.push(message) }, sessionManager: { getSessionId: () => "different-session" }, getSystemPromptOptions: () => ({ contextFiles: [{ path: "/fixture/AGENTS.md", content: "third session instruction" }] }) });
        assert.strictEqual(((await readdir(dir)).filter(file => /^parent-instruction-provenance-[0-9a-f]+-[0-9a-f]+\.json$/.test(file))).length, 3);
    }
    finally {
        if (oldProfile === undefined)
            delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        else
            process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
        await rm(dir, { recursive: true, force: true });
    }
});
describe("native challenger lifecycle", { skip: skipReason }, () => {
    it("persists captured writer paths, handoff identity, and approved-check evidence in the normalized result", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-native-result-evidence-test-"));
        execFileSync("git", ["init", "-q", dir]);
        execFileSync("git", ["-C", dir, "config", "user.email", "fixture@example.invalid"]);
        execFileSync("git", ["-C", dir, "config", "user.name", "Fixture"]);
        await writeFile(join(dir, "base.txt"), "base\n");
        execFileSync("git", ["-C", dir, "add", "base.txt"]);
        execFileSync("git", ["-C", dir, "commit", "-qm", "base"]);
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profileName: "fixture", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        execFileSync("git", ["-C", dir, "add", "native-profile.json"]);
        execFileSync("git", ["-C", dir, "commit", "-qm", "profile"]);
        const checkScript = "if (require('node:fs').readFileSync('worker.txt', 'utf8') !== 'WRITER_OK\\n') process.exit(1)";
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        const oldChecks = process.env.PITWALL_WORKBENCH_APPROVED_CHECKS;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        process.env.PITWALL_WORKBENCH_APPROVED_CHECKS = JSON.stringify([{ command: process.execPath, args: ["-e", checkScript] }]);
        const bus = new Bus();
        const lifecycle = new Map();
        let tool;
        const fakePi = {
            events: bus,
            on(channel, handler) { const set = lifecycle.get(channel) ?? new Set(); set.add(handler); lifecycle.set(channel, set); return () => set.delete(handler); },
            registerProvider() { }, registerTool(value) { if (value.name === "agent_task")
                tool = value; }, registerCommand() { }, setActiveTools() { },
        };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        bus.on("subagents:rpc:spawn", (raw) => {
            const childId = "writer-evidence-child";
            writeFileSync(join(raw.options.cwd, "worker.txt"), "WRITER_OK\n");
            bus.emit("subagents:completed", { id: childId, status: "completed", result: "writer complete" });
            bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: childId } });
        });
        try {
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? [])
                await handler({}, {});
            const result = await tool.execute("writer-evidence", { role: "worker", task: "write worker.txt" }, undefined, undefined, { cwd: dir, sessionManager: { getSessionId: () => "parent" } });
            assertMatch(result, { isError: false, content: [{ text: stringContaining("VALIDATION=passed") }] });
            const records = await new TaskRecordStore(dir).list();
            assert.strictEqual(records.length, 1);
            const record = records[0];
            const manifestPath = execFileSync("find", [join(dir, ".pi-workbench-handoffs"), "-name", ".pi-workbench-handoff.json"], { encoding: "utf8" }).trim();
            const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
            const patchPath = join(manifestPath, "..", "handoff.patch");
            const patch = await readFile(patchPath, "utf8");
            const revision = execFileSync("git", ["-C", dir, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
            assertMatch(record, {
                role: "worker", executionState: "succeeded", acceptanceState: "checks-passed", workspace: manifest.workspace, baseRevision: revision,
                changedFiles: ["worker.txt"],
                handoff: { handoffId: manifest.taskId, patchPath, patchSha256: createHash("sha256").update(patch).digest("hex"), evidence: { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: manifest.workspace, revision } },
                verification: [{ checkId: "approved-check-1", exitCode: 0, evidencePath: stringMatching(/validation\.json$/), evidence: { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: manifest.workspace, revision } }],
                blockers: [], risks: [], usage: { source: "unavailable", provenance: { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: manifest.workspace, revision } },
            });
            const validation = JSON.parse(await readFile(record.verification[0].evidencePath, "utf8"));
            assertMatch(validation.results[0], { code: 0, stdoutArtifact: { path: stringContaining("validation-") }, stderrArtifact: { path: stringContaining("validation-") } });
        }
        finally {
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            if (oldChecks === undefined)
                delete process.env.PITWALL_WORKBENCH_APPROVED_CHECKS;
            else
                process.env.PITWALL_WORKBENCH_APPROVED_CHECKS = oldChecks;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("persists captured writer evidence when the child fails", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-native-failed-result-test-"));
        execFileSync("git", ["init", "-q", dir]);
        execFileSync("git", ["-C", dir, "config", "user.email", "fixture@example.invalid"]);
        execFileSync("git", ["-C", dir, "config", "user.name", "Fixture"]);
        await writeFile(join(dir, "base.txt"), "base\n");
        execFileSync("git", ["-C", dir, "add", "base.txt"]);
        execFileSync("git", ["-C", dir, "commit", "-qm", "base"]);
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profileName: "fixture", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        execFileSync("git", ["-C", dir, "add", "native-profile.json"]);
        execFileSync("git", ["-C", dir, "commit", "-qm", "profile"]);
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        const bus = new Bus();
        const lifecycle = new Map();
        let tool;
        const fakePi = {
            events: bus,
            on(channel, handler) { const set = lifecycle.get(channel) ?? new Set(); set.add(handler); lifecycle.set(channel, set); return () => set.delete(handler); },
            registerProvider() { }, registerTool(value) { if (value.name === "agent_task")
                tool = value; }, registerCommand() { }, setActiveTools() { },
        };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        bus.on("subagents:rpc:spawn", (raw) => {
            const childId = "writer-failed-child";
            writeFileSync(join(raw.options.cwd, "failed.txt"), "PRESERVE_FAILED_WORK\n");
            bus.emit("subagents:failed", { id: childId, status: "failed", error: "child reported failure" });
            bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: childId } });
        });
        try {
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? [])
                await handler({}, {});
            const result = await tool.execute("writer-failed", { role: "worker", task: "write failed.txt then fail" }, undefined, undefined, { cwd: dir, sessionManager: { getSessionId: () => "parent" } });
            assertMatch(result, { isError: true, content: [{ text: stringContaining("child reported failure") }] });
            const record = (await new TaskRecordStore(dir).list())[0];
            const manifestPath = execFileSync("find", [join(dir, ".pi-workbench-handoffs"), "-name", ".pi-workbench-handoff.json"], { encoding: "utf8" }).trim();
            const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
            const patchPath = join(manifestPath, "..", "handoff.patch");
            const patch = await readFile(patchPath, "utf8");
            const revision = execFileSync("git", ["-C", dir, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
            assertMatch(record, {
                executionState: "failed", acceptanceState: "rejected", error: "child reported failure", workspace: manifest.workspace, baseRevision: revision,
                changedFiles: ["failed.txt"],
                handoff: { handoffId: manifest.taskId, patchPath, patchSha256: createHash("sha256").update(patch).digest("hex"), evidence: { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: manifest.workspace, revision } },
                usage: { source: "unavailable", provenance: { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: manifest.workspace, revision } },
            });
        }
        finally {
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("caps native child shell commands at the profile tool deadline", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-native-timeout-test-"));
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture", toolTimeoutMs: 100 }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        const bus = new Bus();
        const lifecycle = new Map();
        const fakePi = { events: bus, on(channel, handler) { const set = lifecycle.get(channel) ?? new Set(); set.add(handler); lifecycle.set(channel, set); return () => set.delete(handler); }, registerProvider() { }, registerTool() { }, registerCommand() { }, setActiveTools() { } };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        try {
            await registerNativeExtension(fakePi);
            const event = { toolName: "bash", input: { command: "sleep 5", timeout: 30 } };
            for (const handler of lifecycle.get("tool_call") ?? [])
                await handler(event, {});
            assert.strictEqual(event.input.timeout, 0.1);
            const tool = createBashToolDefinition(dir, { exposeSessionEnvironment: false });
            const started = Date.now();
            await await assert.rejects(tool.execute("native-timeout-test", event.input, new AbortController().signal, undefined, { cwd: dir }), errorLike("Command timed out"));
            assert.ok(Date.now() - started < 1500);
        }
        finally {
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("enforces planning mode at the parent tool surface and execution hook", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-planning-test-"));
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", planning: true, profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        await mkdir(join(dir, "agents"), { recursive: true });
        const planningRole = (name) => `---\nname: ${name}\nmodel: "fixture/model"\ntools: read, grep, find, ls\nextensions: false\nskills: false\ninherit_context: false\nisolated: true\nallowed_subagents: none\nisolation: off\n---\nRead-only role.\n`;
        for (const name of ["workbench-scout", "workbench-worker", "workbench-reviewer"])
            await writeFile(join(dir, "agents", `${name}.md`), planningRole(name));
        const bus = new Bus();
        const lifecycle = new Map();
        let tools = [];
        let taskTool;
        const fakePi = {
            events: bus,
            on(channel, handler) { const set = lifecycle.get(channel) ?? new Set(); set.add(handler); lifecycle.set(channel, set); return () => set.delete(handler); },
            registerProvider() { }, registerTool(value) { if (value.name === "agent_task")
                taskTool = value; }, registerCommand() { }, setActiveTools(value) { tools = value; },
        };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        try {
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? [])
                await handler({}, {});
            assert.deepStrictEqual(tools.sort(), ["agent_control", "agent_task", "find", "grep", "ls", "read"]);
            const blocked = await [...(lifecycle.get("tool_call") ?? [])][0]({ toolName: "write", input: { path: join(dir, "forbidden.txt"), content: "blocked" } }, {});
            assertMatch(blocked, { block: true, terminate: true });
            await assertMatch((await taskTool.execute("call", { role: "worker", task: "write a file" }, undefined, undefined, { cwd: dir })), { isError: true, content: [{ text: stringContaining("read-only child roles") }] });
        }
        finally {
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("correlates a terminal event that arrives before the spawn reply", async () => {
        const bus = new Bus();
        const holder = state();
        bus.on("subagents:rpc:spawn", (raw) => {
            const requestId = raw.requestId;
            bus.emit("subagents:completed", { id: "child-1", status: "completed", result: "early" });
            bus.emit(`subagents:rpc:spawn:reply:${requestId}`, { success: true, data: { id: "child-1" } });
        });
        const result = await makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, undefined);
        assert.strictEqual(result.result?.text, "early");
        assert.strictEqual(holder.current, undefined);
    });
    it("waits for a direct registry child session before capturing loaded instructions", async () => {
        const bus = new Bus();
        const holder = state();
        const key = Symbol.for("pi-subagents:manager");
        const previous = globalThis[key];
        const childId = "direct-delayed-child";
        let record;
        let captured;
        globalThis[key] = {
            spawn: () => childId,
            getRecord: (id) => id === childId ? record : undefined,
        };
        try {
            const result = makeTintinDelegation(piFor(bus), holder, "/tmp", "direct task", "fixture/model", undefined, undefined, "workbench-scout", (id) => { captured = childInstructionProvenance(id); }, undefined, { modelRegistry: { find: () => ({ id: "fixture-model" }) } });
            setTimeout(() => { record = { session: {} }; }, 20);
            setTimeout(() => {
                record = { session: { resourceLoader: {
                            getAgentsFiles: () => ({ agentsFiles: [{ path: "/fixture/AGENTS.md", content: "direct instruction bytes" }] }),
                            getSystemPrompt: () => "direct system bytes",
                            getSystemPromptSource: () => ({ path: "/fixture/system.md" }),
                            getAppendSystemPrompt: () => ["direct append bytes"],
                            getAppendSystemPromptSources: () => [{ path: "/fixture/append.md" }],
                        } } };
            }, 40);
            setTimeout(() => bus.emit("subagents:completed", { id: childId, status: "completed", result: "direct complete" }), 60);
            await assertMatch((await result), { childId, status: "completed" });
            assert.notStrictEqual(captured, undefined);
            await assertMatch((await captured), {
                source: "resource-loader", complete: true,
                files: arrayContaining([
                    objectContaining({ path: "/fixture/AGENTS.md", estimated: false }),
                    objectContaining({ path: "/fixture/system.md", estimated: false }),
                    objectContaining({ path: "/fixture/append.md", estimated: false }),
                ]),
            });
            assert.strictEqual(holder.current, undefined);
        }
        finally {
            if (previous === undefined)
                delete globalThis[key];
            else
                globalThis[key] = previous;
        }
    });
    it("returns an explicit uncertain launch when the backend never acknowledges spawn", async () => {
        const bus = new Bus();
        const holder = state();
        let requestId = "";
        bus.on("subagents:rpc:spawn", (raw) => { requestId = raw.requestId; });
        const result = await makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, undefined, "workbench-scout", undefined, undefined, undefined, 20);
        assertMatch(result, { status: "interrupted", error: stringContaining("child ownership is uncertain") });
        assert.strictEqual(holder.current?.requestId, requestId);
        assert.ok(holder.current?.stopError.includes("further launches are blocked"));
        bus.emit(`subagents:rpc:spawn:reply:${requestId}`, { success: false, error: "late rejection" });
        assert.strictEqual(holder.current, undefined);
    });
    it("stops a child acknowledged after the uncertain-launch result", async () => {
        const bus = new Bus();
        const holder = state();
        let requestId = "";
        let stopped = "";
        const key = Symbol.for("pi-subagents:manager");
        const previous = globalThis[key];
        globalThis[key] = { getRecord: (id) => id === "late-child" ? { session: { clearQueue: async () => undefined } } : undefined };
        bus.on("subagents:rpc:spawn", (raw) => { requestId = raw.requestId; });
        bus.on("subagents:rpc:stop", (raw) => { stopped = raw.agentId; bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: true }); bus.emit("subagents:failed", { id: stopped, status: "aborted" }); });
        try {
            const result = await makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, undefined, "workbench-scout", undefined, undefined, undefined, 20);
            assert.strictEqual(result.status, "interrupted");
            bus.emit(`subagents:rpc:spawn:reply:${requestId}`, { success: true, data: { id: "late-child" } });
            await waitFor(() => assert.strictEqual(stopped, "late-child"));
            assert.strictEqual(holder.current, undefined);
        }
        finally {
            if (previous === undefined)
                delete globalThis[key];
            else
                globalThis[key] = previous;
        }
    });
    it("retains ownership after readiness failure until the child terminates", async () => {
        const bus = new Bus();
        const holder = state();
        let stopped = "";
        const key = Symbol.for("pi-subagents:manager");
        const previous = globalThis[key];
        globalThis[key] = { getRecord: (id) => id === "unready-child" ? { session: { clearQueue: async () => undefined } } : undefined };
        bus.on("subagents:rpc:spawn", (raw) => bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: "unready-child" } }));
        bus.on("subagents:rpc:stop", (raw) => { stopped = raw.agentId; bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: true }); });
        try {
            const result = await makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, undefined, "workbench-scout", undefined, 10);
            assertMatch(result, { status: "failed", childId: "unready-child", error: stringContaining("public agent hook") });
            await waitFor(() => assert.strictEqual(stopped, "unready-child"));
            assert.strictEqual(holder.current?.childId, "unready-child");
            bus.emit("subagents:failed", { id: "unready-child", status: "aborted", error: "stopped" });
            assert.strictEqual(holder.current, undefined);
        }
        finally {
            if (previous === undefined)
                delete globalThis[key];
            else
                globalThis[key] = previous;
        }
    });
    it("latches abort before spawn reply and stops the actual child", async () => {
        const bus = new Bus();
        const holder = state();
        const controller = new AbortController();
        let stopped = "";
        globalThis[Symbol.for("pi-subagents:manager")] = { getRecord: (id) => id === "child-2" ? { session: { clearQueue: async () => undefined } } : undefined };
        bus.on("subagents:rpc:spawn", (raw) => {
            const requestId = raw.requestId;
            setTimeout(() => bus.emit(`subagents:rpc:spawn:reply:${requestId}`, { success: true, data: { id: "child-2" } }), 5);
        });
        bus.on("subagents:rpc:stop", (raw) => { stopped = raw.agentId; bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: true }); bus.emit("subagents:failed", { id: stopped, status: "aborted", error: "cancelled" }); });
        const result = makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, controller.signal);
        controller.abort();
        await assertMatch((await result), { status: "cancelled" });
        assert.strictEqual(stopped, "child-2");
        delete globalThis[Symbol.for("pi-subagents:manager")];
    });
    it("times out, retains ownership, and stops a late child", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-native-test-"));
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture", taskTimeoutMs: 50 }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        const bus = new Bus();
        let tool;
        let spawnRequest;
        let stopped = "";
        const commands = new Map();
        const lifecycle = new Map();
        const fakePi = {
            events: bus,
            on(channel, handler) {
                const set = lifecycle.get(channel) ?? new Set();
                set.add(handler);
                lifecycle.set(channel, set);
                return () => set.delete(handler);
            },
            registerProvider() { }, registerTool(value) { if (value.name === "agent_task")
                tool = value; }, registerCommand(name, value) { commands.set(name, value); }, setActiveTools() { },
        };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        bus.on("subagents:rpc:spawn", (raw) => { spawnRequest = raw; });
        bus.on("subagents:rpc:stop", (raw) => { stopped = raw.agentId; bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: true }); });
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        try {
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? [])
                await handler({}, {});
            const pending = tool.execute("call", { task: "slow" }, undefined, undefined, { cwd: dir });
            const observed = pending.then(() => undefined, (error) => error);
            await assertMatch((await observed), { message: stringMatching(/remains unresolved/) });
            const childId = "late-child";
            globalThis[Symbol.for("pi-subagents:manager")] = { getRecord: (id) => id === childId ? { session: { clearQueue: async () => undefined } } : undefined };
            bus.emit(`subagents:rpc:spawn:reply:${spawnRequest.requestId}`, { success: true, data: { id: childId } });
            await waitFor(() => assert.strictEqual(stopped, childId));
            await waitFor(async () => {
                const records = await new TaskRecordStore(dir).list();
                assert.strictEqual(records.length, 1);
                assertMatch(records[0], { executionState: "interrupted", acceptanceState: "rejected", childId });
            });
            await commands.get("workbench-stop").handler("", { abort() { } });
            bus.emit("subagents:failed", { id: childId, status: "aborted", error: "cancelled" });
            const spawnBeforeLatchCheck = spawnRequest;
            await assertMatch((await tool.execute("call", { task: "queued continuation" }, undefined, undefined, { cwd: dir })), { isError: true });
            assert.strictEqual(spawnRequest, spawnBeforeLatchCheck);
        }
        finally {
            delete globalThis[Symbol.for("pi-subagents:manager")];
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("retains the active request when stop acknowledgement fails", async () => {
        const bus = new Bus();
        const holder = state();
        const controller = new AbortController();
        const childId = "child-stop-error";
        globalThis[Symbol.for("pi-subagents:manager")] = {
            getRecord: (id) => id === childId ? { session: { clearQueue: async () => undefined } } : undefined,
        };
        bus.on("subagents:rpc:spawn", (raw) => bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: childId } }));
        bus.on("subagents:rpc:stop", (raw) => bus.emit(`subagents:rpc:stop:reply:${raw.requestId}`, { success: false, error: "stop denied" }));
        try {
            const result = makeTintinDelegation(piFor(bus), holder, "/tmp", "task", "fixture/model", undefined, controller.signal);
            await waitFor(() => assert.strictEqual(holder.current?.childId, childId));
            controller.abort();
            await waitFor(() => assert.strictEqual(holder.current?.stopError, "stop denied"));
            assert.strictEqual(await Promise.race([result.then(() => "settled"), Promise.resolve("pending")]), "pending");
            assert.strictEqual(holder.current?.childId, childId);
        }
        finally {
            delete globalThis[Symbol.for("pi-subagents:manager")];
        }
    });
    it("surfaces a settled child lineage persistence failure instead of accepting it", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-native-persistence-test-"));
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        const bus = new Bus();
        let tool;
        const lifecycle = new Map();
        const persistenceErrors = [];
        const fakePi = {
            events: bus,
            on(channel, handler) { const set = lifecycle.get(channel) ?? new Set(); set.add(handler); lifecycle.set(channel, set); return () => set.delete(handler); },
            registerProvider() { }, registerTool(value) { if (value.name === "agent_task")
                tool = value; }, registerCommand() { }, setActiveTools() { },
        };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        bus.on("subagents:persistence_error", (payload) => persistenceErrors.push(payload));
        bus.on("subagents:rpc:spawn", (raw) => {
            const requestId = raw.requestId;
            bus.emit(`subagents:rpc:spawn:reply:${requestId}`, { success: true, data: { id: "persist-child" } });
            queueMicrotask(() => bus.emit("subagents:completed", { id: "persist-child", status: "completed", result: "completed" }));
        });
        globalThis[Symbol.for("pi-subagents:manager")] = { getRecord: (id) => id === "persist-child" ? { session: {} } : undefined };
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        const failedUpdate = mock.method(TaskRecordStore.prototype, "update", async () => { throw new Error("persist denied"); }, { times: 1 });
        try {
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? [])
                await handler({}, {});
            const result = await tool.execute("persist-failure", { task: "exercise persistence" }, undefined, undefined, { cwd: dir, sessionManager: { getSessionId: () => "parent" } });
            assertMatch(result, { isError: true, content: [{ text: stringContaining("lineage persistence failed: persist denied") }] });
            assert.strictEqual(failedUpdate.mock.callCount(), 1);
            assert.strictEqual(persistenceErrors.length, 1);
        }
        finally {
            failedUpdate.mock.restore();
            delete globalThis[Symbol.for("pi-subagents:manager")];
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("persists disjoint child lineage and keeps a huge child report in artifacts", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-lineage-test-"));
        const profilePath = join(dir, "native-profile.json");
        await writeFile(profilePath, JSON.stringify({ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", profileName: "fixture", profile: { provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }, providerConfig: { baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }] } }));
        const bus = new Bus();
        const lifecycle = new Map();
        const commands = new Map();
        let tool;
        let childNumber = 0;
        const loaderCalls = new Map();
        const fakePi = {
            events: bus,
            on(channel, handler) { const set = lifecycle.get(channel) ?? new Set(); set.add(handler); lifecycle.set(channel, set); return () => set.delete(handler); },
            registerProvider() { }, registerTool(value) { if (value.name === "agent_task")
                tool = value; }, registerCommand(name, value) { commands.set(name, value); }, setActiveTools() { },
        };
        bus.on("subagents:rpc:ping", (raw) => bus.emit(`subagents:rpc:ping:reply:${raw.requestId}`, { success: true, data: { version: 2 } }));
        bus.on("subagents:rpc:spawn", (raw) => {
            const childId = `lineage-child-${++childNumber}`;
            const result = `${childNumber === 1 ? "SCOUT_ONE" : "SCOUT_TWO"}\n${"HUGE_CHILD_REPORT ".repeat(2_000)}`;
            bus.emit("subagents:completed", { id: childId, status: "completed", result });
            bus.emit(`subagents:rpc:spawn:reply:${raw.requestId}`, { success: true, data: { id: childId } });
        });
        globalThis[Symbol.for("pi-subagents:manager")] = { getRecord: (id) => ({ sessionFile: join(dir, `${id}.jsonl`), session: id === "lineage-child-3" ? {} : { resourceLoader: {
                        getAgentsFiles: () => { const calls = (loaderCalls.get(id) ?? 0) + 1; loaderCalls.set(id, calls); writeFileSync(join(dir, `${id}-system.md`), "MUTATED_AFTER_LOAD\n"); return { agentsFiles: [{ path: join(dir, `${id}-AGENTS.md`), content: `child instruction ${id}${calls > 1 ? " MUTATED" : ""}` }] }; },
                        getSystemPrompt: () => `LOADED_SYSTEM_${id}`,
                        getSystemPromptSource: () => ({ path: join(dir, `${id}-system.md`) }),
                        getAppendSystemPrompt: () => [`LOADED_APPEND_${id}`],
                        getAppendSystemPromptSources: () => [{ path: join(dir, `${id}-append.md`) }],
                    } } }) };
        const oldProfile = process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
        process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
        try {
            await registerNativeExtension(fakePi);
            for (const handler of lifecycle.get("session_start") ?? [])
                await handler({}, {});
            const ctx = { cwd: dir, sessionManager: { getSessionId: () => "parent-session", getSessionFile: () => join(dir, "parent.jsonl") }, getSystemPromptOptions: () => ({ contextFiles: [{ path: join(dir, "AGENTS.md"), content: "hostile instruction is data" }] }), getSystemPrompt: () => "final prompt" };
            const parentNotices = [];
            await commands.get("workbench-instructions").handler("", { ...ctx, ui: { notify: (message) => parentNotices.push(message) } });
            const parentCapturePath = JSON.parse(parentNotices[0]).path;
            const parentCaptureBeforeChildren = await readFile(parentCapturePath, "utf8");
            const first = await tool.execute("one", { task: "first disjoint scout" }, undefined, undefined, ctx);
            const second = await tool.execute("two", { task: "second disjoint scout" }, undefined, undefined, ctx);
            const firstText = first.content[0].text;
            const secondText = second.content[0].text;
            assert.ok(firstText.includes("SCOUT_ONE"));
            assert.ok(secondText.includes("SCOUT_TWO"));
            assert.ok(firstText.length < 6_000);
            await tool.execute("three", { task: "third bounded scout" }, undefined, undefined, ctx);
            await tool.execute("four", { task: "fourth bounded scout" }, undefined, undefined, ctx);
            await assertMatch((await tool.execute("five", { task: "launch storm" }, undefined, undefined, ctx)), { isError: true, content: [{ text: stringContaining("launch budget exhausted") }] });
            for (const handler of lifecycle.get("input") ?? [])
                await handler({ source: "extension" }, ctx);
            await assertMatch((await tool.execute("still-five", { task: "automatic follow-up must not reset budget" }, undefined, undefined, ctx)), { isError: true });
            for (const handler of lifecycle.get("input") ?? [])
                await handler({ source: "interactive" }, ctx);
            await tool.execute("after-input", { task: "new user turn scout" }, undefined, undefined, ctx);
            const recordsDir = join(dir, "task-records");
            const records = (await readdir(recordsDir)).filter((name) => name.endsWith(".json"));
            assert.strictEqual(records.length, 5);
            const saved = await Promise.all(records.map(async (name) => JSON.parse(await readFile(join(recordsDir, name), "utf8"))));
            assert.strictEqual(new Set(saved.map((record) => record.taskId)).size, 5);
            assert.strictEqual(new Set(saved.map((record) => record.runId)).size, 5);
            assert.strictEqual(new Set(saved.map((record) => record.attemptId)).size, 5);
            for (const record of saved) {
                assert.strictEqual(record.parentSessionId, "parent-session");
                assert.match(record.parentInstructionProvenancePath, /parent-instruction-provenance-[0-9a-f]+-[0-9a-f]+\.json$/);
                const parentCapture = JSON.parse(await readFile(record.parentInstructionProvenancePath, "utf8"));
                assertMatch(parentCapture, { schemaVersion: 1, parentSessionId: "parent-session", provenance: { source: "public-context-files", complete: true } });
                if (record.childId === "lineage-child-3") {
                    assertMatch(record.instructionProvenance, { source: "resource-loader", complete: false, files: [], reason: "child resource loader did not expose loaded instruction bytes" });
                }
                else {
                    assertMatch(record.instructionProvenance, { source: "resource-loader", complete: true });
                    assertMatch(record.instructionProvenance.files[0], { estimated: false });
                    assert.ok(record.instructionProvenance.files[0].path.includes("lineage-child-"));
                    assert.strictEqual(record.instructionProvenance.files.some((file) => file.sha256 === createHash("sha256").update(`LOADED_SYSTEM_${record.childId}`).digest("hex")), true);
                    assert.strictEqual(record.instructionProvenance.files.some((file) => file.sha256 === createHash("sha256").update(`LOADED_APPEND_${record.childId}`).digest("hex")), true);
                    assert.strictEqual(loaderCalls.get(record.childId), 1);
                }
                assertMatch(record.profile, { name: "fixture", provider: "fixture", modelId: "model", api: "openai-completions", endpoint: "http://127.0.0.1", credentialClass: "keyless" });
                assert.strictEqual(record.executionState, "succeeded");
                assert.deepStrictEqual(record.changedFiles, []);
                assert.deepStrictEqual(record.verification, []);
                assert.deepStrictEqual(record.blockers, []);
                assert.deepStrictEqual(record.risks, []);
                assert.strictEqual(record.usage.source, "unavailable");
                assertMatch(record.usage.provenance, { taskId: record.taskId, runId: record.runId, attemptId: record.attemptId, workspace: dir });
                assert.match(record.artifacts.result.path, /\.result-[0-9a-f]+\.txt$/);
                const full = await readFile(record.artifacts.result.path, "utf8");
                assert.ok(full.includes("HUGE_CHILD_REPORT"));
                assert.ok(full.length > record.summary.length);
                assert.ok(record.summary.length < 4_500);
            }
            assert.strictEqual(await readFile(parentCapturePath, "utf8"), parentCaptureBeforeChildren);
        }
        finally {
            delete globalThis[Symbol.for("pi-subagents:manager")];
            if (oldProfile === undefined)
                delete process.env.PITWALL_WORKBENCH_NATIVE_PROFILE;
            else
                process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = oldProfile;
            await rm(dir, { recursive: true, force: true });
        }
    });
});
describe("terminal task records", { skip: skipReason }, () => {
    it("refuse late artifacts before writing a file", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-workbench-terminal-artifact-"));
        try {
            const store = new TaskRecordStore(dir);
            const record = await store.create({
                role: "scout", workspace: dir, task: "inspect", baseRevision: "base", parentSessionId: "parent",
                profile: { name: "fixture", provider: "fixture", modelId: "model", api: "openai-completions", endpoint: "http://127.0.0.1", resourceGroup: "fixture", credentialClass: "keyless" },
            });
            await store.finish(record.taskId, { executionState: "succeeded", result: "done" });
            const before = (await readdir(store.directory)).sort();
            await assert.rejects(() => store.artifact(record.taskId, "summary", "late"), /succeeded/);
            assert.deepStrictEqual((await readdir(store.directory)).sort(), before);
        }
        finally {
            await rm(dir, { recursive: true, force: true });
        }
    });
});
