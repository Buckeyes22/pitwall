// Ported from packages/pi-workbench/tests/comparison-lifecycle-observer.test.ts (vitest -> node:test).
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { assertMatch, loadExtension } from "./support.mjs";

const { default: observer } = await loadExtension("comparison-lifecycle-observer.js");

describe("comparison lifecycle observer", () => {
    it("records only public child lifecycle identity and timing fields", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-comparison-observer-"));
        const path = join(dir, "lifecycle.jsonl");
        const previous = process.env.COMPARISON_LIFECYCLE_PATH;
        const handlers = new Map();
        try {
            process.env.COMPARISON_LIFECYCLE_PATH = path;
            observer({ events: { on: (event, handler) => { handlers.set(event, handler); return () => handlers.delete(event); } } });
            handlers.get("subagents:started")({ id: "child-a", description: "secret task text" });
            handlers.get("subagents:completed")({ id: "child-a", status: "completed", durationMs: 42, result: "secret child output" });
            const records = (await readFile(path, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
            assert.strictEqual(records.length, 2);
            assertMatch(records[0], { event: "subagents:started", id: "child-a" });
            assertMatch(records[1], { event: "subagents:completed", id: "child-a", status: "completed", durationMs: 42 });
            assert.ok(!JSON.stringify(records).includes("secret"));
        }
        finally {
            if (previous === undefined)
                delete process.env.COMPARISON_LIFECYCLE_PATH;
            else
                process.env.COMPARISON_LIFECYCLE_PATH = previous;
            await rm(dir, { recursive: true, force: true });
        }
    });
    it("adapts the pinned pi-subagents 0.69 contracts without joining protocols", async () => {
        const dir = await mkdtemp(join(tmpdir(), "pi-comparison-observer-nico-"));
        const path = join(dir, "lifecycle.jsonl");
        const previous = process.env.COMPARISON_LIFECYCLE_PATH;
        const handlers = new Map();
        try {
            process.env.COMPARISON_LIFECYCLE_PATH = path;
            observer({ events: { on: (event, handler) => { handlers.set(event, handler); return () => handlers.delete(event); } } });
            handlers.get("subagent:async-started")({ id: "run-7", task: "secret task" });
            handlers.get("subagent:async-complete")({ runId: "run-7", status: "completed", result: "secret output" });
            // A different public protocol with the same raw identifier must remain
            // distinct, so downstream correlation cannot combine unrelated children.
            handlers.get("prompt-template:subagent:started")({ requestId: "run-7" });
            const records = (await readFile(path, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
            assert.strictEqual(records.length, 3);
            assertMatch(records[0], { event: "subagents:started", id: "nico-async:run-7", sourceEvent: "subagent:async-started" });
            assertMatch(records[1], { event: "subagents:completed", id: "nico-async:run-7", sourceEvent: "subagent:async-complete" });
            assert.strictEqual(records[0].pid, process.pid);
            assert.strictEqual(records[2].id, "nico-delegation:run-7");
            assert.ok(!JSON.stringify(records).includes("secret"));
        }
        finally {
            if (previous === undefined)
                delete process.env.COMPARISON_LIFECYCLE_PATH;
            else
                process.env.COMPARISON_LIFECYCLE_PATH = previous;
            await rm(dir, { recursive: true, force: true });
        }
    });
});
