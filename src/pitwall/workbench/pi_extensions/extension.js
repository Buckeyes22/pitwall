import { appendAccounting, shapePayload, safeUsage } from "./accounting.js";
import { join } from "node:path";
import { readFile } from "node:fs/promises";
import { registerConfiguredProvider } from "./provider-extension.js";
import { applySoloToolDeadline } from "./timeouts.js";
export function accountingPathFor(cwd, env = process.env) {
    return env.PITWALL_WORKBENCH_ACCOUNTING_PATH ?? join(env.PI_CODING_AGENT_DIR ?? cwd, ".pi-workbench-accounting.jsonl");
}
export default async function (pi) {
    await registerConfiguredProvider(pi);
    const profilePath = process.env.PITWALL_WORKBENCH_PROVIDER_PROFILE;
    if (profilePath) {
        const configured = JSON.parse(await readFile(profilePath, "utf8"));
        const toolTimeoutMs = configured.profile?.toolTimeoutMs;
        if (toolTimeoutMs !== undefined)
            pi.on("tool_call", (event) => { applySoloToolDeadline(event, toolTimeoutMs); });
    }
    let file;
    const pathFor = (cwd) => file ??= accountingPathFor(cwd);
    pi.on("before_provider_request", async (event, ctx) => appendAccounting(pathFor(ctx.cwd), { type: "provider_request", shape: shapePayload(event.payload) }));
    pi.on("message_end", async (event, ctx) => {
        const message = event.message;
        if (message.role === "assistant")
            await appendAccounting(pathFor(ctx.cwd), { type: "usage", usage: safeUsage(message.usage) });
    });
    pi.on("session_compact", async (event, ctx) => appendAccounting(pathFor(ctx.cwd), { type: "compaction", tokensBefore: event.compactionEntry.tokensBefore }));
}
