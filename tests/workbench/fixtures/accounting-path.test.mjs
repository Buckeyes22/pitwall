// Ported from packages/pi-workbench/tests/hosted-acceptance.test.ts (vitest -> node:test).
// Run by tests/workbench/test_hosted_acceptance.py; it reuses the Pi extension test support.
import assert from "node:assert/strict";
import { loadExtension, piTest as test, skipReason } from "../pi_extensions/support.mjs";

const { accountingPathFor } = skipReason ? {} : await loadExtension("extension.js");

test("accounting defaults to the private agent directory and honors an explicit destination", () => {
    assert.strictEqual(accountingPathFor("/fixture", { PI_CODING_AGENT_DIR: "/private-agent" }), "/private-agent/.pi-workbench-accounting.jsonl");
    assert.strictEqual(accountingPathFor("/fixture", { PI_CODING_AGENT_DIR: "/private-agent", PITWALL_WORKBENCH_ACCOUNTING_PATH: "/private-report.jsonl" }), "/private-report.jsonl");
});
