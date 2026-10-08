"""One literal test per Pi extension surface discovery reports (port of ``surfaces.test.ts``).

The accounting and deadline hooks, the native commands, tools, and input hook, and the comparison
observer's async subscriptions live in the packaged JavaScript extensions, so those cases run as a
Node built-in test file against the committed ``.js`` modules. The launch flags and the command
group are Python surfaces and are asserted directly. The Node file skips itself with a reason when
the Pi packages are not installed (set ``PITWALL_PI_MODULES`` to a ``node_modules`` directory that
contains ``@earendil-works``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pitwall import cli as pitwall_cli
from pitwall.workbench import cli

SUPPORT = (Path(__file__).resolve().parent / "pi_extensions" / "support.mjs").as_uri()

NODE_SURFACES = f"""
// Ported from packages/pi-workbench/tests/surfaces.test.ts (vitest -> node:test).
import assert from "node:assert/strict";
import {{ mkdir, mkdtemp, readFile, rm, writeFile }} from "node:fs/promises";
import {{ tmpdir }} from "node:os";
import {{ join }} from "node:path";
import {{ afterEach, describe, it }} from "node:test";
import {{ keepEventLoopAlive, loadExtension, skipReason }} from "{SUPPORT}";

keepEventLoopAlive();
const registerExtension = skipReason ? undefined : (await loadExtension("extension.js")).default;
const registerNativeExtension = skipReason ? undefined : (await loadExtension("native-extension.js")).default;
const observer = skipReason ? undefined : (await loadExtension("comparison-lifecycle-observer.js")).default;

const dirs = [];
const envKeys = ["PITWALL_WORKBENCH_PROVIDER_PROFILE", "PITWALL_WORKBENCH_ACCOUNTING_PATH", "PITWALL_WORKBENCH_NATIVE_PROFILE", "COMPARISON_LIFECYCLE_PATH"];
const savedEnv = Object.fromEntries(envKeys.map((key) => [key, process.env[key]]));
afterEach(async () => {{
  for (const key of envKeys) {{ if (savedEnv[key] === undefined) delete process.env[key]; else process.env[key] = savedEnv[key]; }}
  for (const dir of dirs.splice(0)) await rm(dir, {{ recursive: true, force: true }});
  delete globalThis[Symbol.for("pi-subagents:manager")];
}});
async function tempDir(prefix) {{ const dir = await mkdtemp(join(tmpdir(), prefix)); dirs.push(dir); return dir; }}

function fakePi() {{
  const handlers = new Map(); const commands = new Map(); const tools = new Map();
  const pi = {{
    events: {{ on: () => () => undefined, emit: () => undefined }},
    on(event, handler) {{ handlers.set(event, [...(handlers.get(event) ?? []), handler]); }},
    registerProvider() {{}}, registerTool(tool) {{ tools.set(tool.name, tool); }},
    registerCommand(name, command) {{ commands.set(name, command); }}, setActiveTools() {{}},
  }};
  return {{ pi, handlers, commands, tools }};
}}
const opts = {{ skip: skipReason }};

describe("accounting extension hooks", opts, () => {{
  async function registered() {{
    const dir = await tempDir("pitwall-surfaces-extension-");
    const profilePath = join(dir, "provider-profile.json");
    const profile = {{ profile: {{ provider: "surface-provider", modelId: "fixture-model", endpoint: "http://127.0.0.1:1/v1", api: "openai-completions", keyless: "dummy", servedContextTokens: 32768, maxCompletionTokens: 4096, resourceGroup: "surfaces", allowProviderFallback: false, toolTimeoutMs: 5000 }}, providerConfig: {{ baseUrl: "http://127.0.0.1:1/v1", api: "openai-completions", models: [] }} }};
    await writeFile(profilePath, `${{JSON.stringify(profile)}}\\n`, {{ mode: 0o600 }});
    process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE = profilePath;
    process.env.PITWALL_WORKBENCH_ACCOUNTING_PATH = join(dir, "accounting.jsonl");
    const fake = fakePi();
    await registerExtension(fake.pi);
    const fire = async (event, value) => {{ for (const handler of fake.handlers.get(event) ?? []) await handler(value, {{ cwd: dir }}); }};
    const records = async () => (await readFile(process.env.PITWALL_WORKBENCH_ACCOUNTING_PATH, "utf8")).trim().split("\\n").filter(Boolean).map((line) => JSON.parse(line));
    return {{ fire, records }};
  }}

  it("tool_call caps a bash command at the profile's solo tool deadline", async () => {{
    const {{ fire }} = await registered();
    const unbounded = {{ toolName: "bash", input: {{}} }};
    const longer = {{ toolName: "bash", input: {{ timeout: 60 }} }};
    const read = {{ toolName: "read", input: {{}} }};
    await fire("tool_call", unbounded); await fire("tool_call", longer); await fire("tool_call", read);
    assert.deepEqual([unbounded.input.timeout, longer.input.timeout, read.input.timeout], [5, 5, undefined]);
  }});

  it("before_provider_request records the request shape without its text", async () => {{
    const {{ fire, records }} = await registered();
    await fire("before_provider_request", {{ payload: {{ messages: [{{ role: "user", content: "PROMPT_TEXT_CANARY" }}] }} }});
    const [record] = await records();
    assert.equal(record.type, "provider_request");
    assert.ok(!JSON.stringify(record).includes("PROMPT_TEXT_CANARY"));
  }});

  it("message_end records assistant usage and ignores other roles", async () => {{
    const {{ fire, records }} = await registered();
    await fire("message_end", {{ message: {{ role: "user", usage: {{ input: 9 }} }} }});
    await fire("message_end", {{ message: {{ role: "assistant", usage: {{ input: 3, output: 4 }} }} }});
    assert.deepEqual(await records(), [{{ type: "usage", usage: {{ input: 3, output: 4 }} }}]);
  }});

  it("session_compact records the tokens before compaction", async () => {{
    const {{ fire, records }} = await registered();
    await fire("session_compact", {{ compactionEntry: {{ tokensBefore: 1234 }} }});
    assert.deepEqual(await records(), [{{ type: "compaction", tokensBefore: 1234 }}]);
  }});
}});

describe("native extension commands, tools, and input hook", opts, () => {{
  async function registered(planning = false) {{
    const dir = await tempDir("pitwall-surfaces-native-");
    const profilePath = join(dir, "native-profile.json");
    const profile = {{ provider: "fixture", modelId: "model", api: "openai-completions", resourceGroup: "fixture" }};
    await writeFile(profilePath, JSON.stringify({{ schemaVersion: 1, backend: "@tintinweb/pi-subagents", version: "0.19.0", ...(planning ? {{ planning: true }} : {{}}), profile, providerConfig: {{ baseUrl: "http://127.0.0.1", api: "openai-completions", models: [{{ id: "model", name: "model", reasoning: false, input: ["text"], contextWindow: 100, maxTokens: 10 }}] }} }}));
    if (planning) {{
      await mkdir(join(dir, "agents"), {{ recursive: true }});
      const role = (name) => `---\\nname: ${{name}}\\nmodel: "fixture/model"\\ntools: read, grep, find, ls\\nextensions: false\\nskills: false\\ninherit_context: false\\nisolated: true\\nallowed_subagents: none\\nisolation: off\\n---\\nrole\\n`;
      for (const name of ["workbench-scout", "workbench-worker", "workbench-reviewer"]) await writeFile(join(dir, "agents", `${{name}}.md`), role(name));
    }}
    process.env.PITWALL_WORKBENCH_NATIVE_PROFILE = profilePath;
    const fake = fakePi();
    await registerNativeExtension(fake.pi);
    const notices = [];
    const ctx = {{ cwd: dir, ui: {{ notify: (message) => notices.push(message) }}, abort() {{}}, getSystemPromptOptions: () => ({{ contextFiles: [] }}) }};
    return {{ ...fake, ctx, notices }};
  }}

  it("input continues and resets only for a genuine new prompt", async () => {{
    const {{ handlers }} = await registered();
    const [input] = handlers.get("input");
    assert.deepEqual(input({{ source: "interactive" }}), {{ action: "continue" }});
    assert.deepEqual(input({{ source: "extension" }}), {{ action: "continue" }});
  }});

  it("agent-control list reports no active task, no records, and the instruction provenance", async () => {{
    const {{ commands, ctx, notices }} = await registered();
    await commands.get("agent-control").handler("list", ctx);
    const [json, provenance] = notices[0].split("\\nPARENT_INSTRUCTION_PROVENANCE=");
    assert.deepEqual(JSON.parse(json), {{ active: null, stopped: false, records: [] }});
    assert.ok(provenance);
  }});

  it("agent_control refuses to steer without an active worker and lists as JSON", async () => {{
    const {{ tools }} = await registered();
    const control = tools.get("agent_control");
    const steer = await control.execute("call-1", {{ action: "steer", message: "narrow the scope" }});
    assert.equal(steer.isError, true);
    assert.equal(steer.content[0].text, "no active native worker");
    const list = await control.execute("call-2", {{ action: "list" }});
    assert.deepEqual(JSON.parse(list.content[0].text), {{ active: null, stopped: false, records: [] }});
  }});

  it("agent_task is registered as the sequential bounded task tool", async () => {{
    const {{ tools }} = await registered();
    assert.equal(tools.get("agent_task").name, "agent_task");
    assert.equal(tools.get("agent_task").executionMode, "sequential");
  }});

  it("workbench-integrate requires a handoff id and is refused under planning", async () => {{
    const worker = await registered();
    await assert.rejects(worker.commands.get("workbench-integrate").handler("  ", worker.ctx), /workbench-integrate requires a completed handoffId/);
    const planner = await registered(true);
    await assert.rejects(planner.commands.get("workbench-integrate").handler("handoff-1", planner.ctx), /planning policy cannot integrate or modify a checkout/);
  }});

  it("workbench-steer refuses when no native request is active", async () => {{
    const {{ commands, ctx }} = await registered();
    await assert.rejects(commands.get("workbench-steer").handler("narrow the scope", ctx), /no active native workbench request/);
  }});
}});

describe("comparison lifecycle observer async subscriptions", opts, () => {{
  it("subagent:async-started and subagent:async-complete record a nico-async start and completion", async () => {{
    const dir = await tempDir("pitwall-surfaces-observer-");
    process.env.COMPARISON_LIFECYCLE_PATH = join(dir, "lifecycle.jsonl");
    const handlers = new Map();
    observer({{ events: {{ on: (event, handler) => {{ handlers.set(event, handler); return () => handlers.delete(event); }} }} }});
    handlers.get("subagent:async-started")({{ id: "async-1", prompt: "ASYNC_PROMPT_CANARY" }});
    handlers.get("subagent:async-complete")({{ id: "async-1", status: "completed", durationMs: 12 }});
    const saved = (await readFile(join(dir, "lifecycle.jsonl"), "utf8")).trim().split("\\n").map((line) => JSON.parse(line));
    assert.equal(saved.length, 2);
    assert.equal(saved[0].event, "subagents:started");
    assert.equal(saved[0].id, "nico-async:async-1");
    assert.equal(saved[1].event, "subagents:completed");
    assert.equal(saved[1].id, "nico-async:async-1");
    assert.equal(saved[1].status, "completed");
    assert.equal(saved[1].durationMs, 12);
    assert.ok(!JSON.stringify(saved).includes("ASYNC_PROMPT_CANARY"));
  }});
}});
"""


@pytest.mark.parity
def test_extension_surfaces(tmp_path: Path) -> None:
    """Ports the accounting hooks, native commands and tools, and observer cases of surfaces.test.ts."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the Pi extension surfaces need Node 22 or newer")
    script = tmp_path / "surfaces.test.mjs"
    script.write_text(NODE_SURFACES, encoding="utf-8")
    completed = subprocess.run(  # noqa: S603  # reason: fixed argv, node resolved from PATH, no shell
        [node, "--test", "--test-timeout=120000", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
        timeout=600,
    )
    tail = "\n".join(completed.stdout.splitlines()[-40:] + completed.stderr.splitlines()[-10:])
    assert completed.returncode == 0, f"node --test failed:\n{tail}"
    assert "# fail 0" in completed.stdout, tail
    assert "# cancelled 0" in completed.stdout, tail


@pytest.mark.parity
def test_each_launch_flag_sets_its_mode_and_leaves_only_the_positionals() -> None:
    """Source: surfaces.test.ts 'each launch flag sets its mode and leaves only the positionals'."""
    parse = cli.parse_launch_args
    assert parse(["profile.json"]) == cli.LaunchArgs(False, False, False, False, ["profile.json"])
    with_continue = parse(["--continue", "profile.json", "coder", "/work"])
    assert with_continue.continue_session
    assert with_continue.positional == ["profile.json", "coder", "/work"]
    native = parse(["profile.json", "--native-child"])
    assert native.native_child
    assert not native.planning
    for flag in ("--planning", "--plan"):
        planning = parse(["profile.json", flag])
        assert planning.planning
        assert planning.native_child
    restricted = parse(["profile.json", "--restricted"])
    assert restricted.restricted
    assert restricted.positional == ["profile.json"]


@pytest.mark.parity
def test_the_workbench_command_group_is_the_entrypoint() -> None:
    """Source: surfaces.test.ts 'the pi-workbench bin is the built CLI with a node shebang'.

    The Node bin is gone; the equivalent surface is the lazy ``workbench`` group of ``pitwall``.
    """
    groups = {name: (module, function) for name, module, function in pitwall_cli.GROUPS}
    assert groups["workbench"] == ("pitwall.cli.workbench", "cmd_workbench")
    from pitwall.cli.workbench import cmd_workbench

    assert cmd_workbench is not None
