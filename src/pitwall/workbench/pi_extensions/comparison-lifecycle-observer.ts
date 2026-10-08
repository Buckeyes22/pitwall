import { appendFileSync } from "node:fs";

/**
 * Read-only comparison instrumentation. It subscribes to the public Pi event
 * bus and records only lifecycle identity/timing fields; it registers no tools,
 * prompts, providers, or context contributions.
 */
export default function comparisonLifecycleObserver(pi: { events: { on: (channel: string, handler: (value: unknown) => void) => () => void } }): void {
	const path = process.env.COMPARISON_LIFECYCLE_PATH;
	if (!path) return;
	const record = (event: string, raw: unknown, family: "tintin" | "nico-async" | "nico-delegation" | "nico-slash" = "tintin", sourceEvent = event): void => {
		const value = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
		// Nico Bailon 0.69 uses id on async-started and runId on async-complete,
		// while delegation uses requestId. Prefixing those identities keeps two
		// independent public protocols from being accidentally joined.
		const identityKeys = family === "tintin"
			? ["id", "childId", "agentId", "runId"]
			: sourceEvent.endsWith("async-complete")
				? ["runId", "id", "requestId"]
				: sourceEvent.endsWith("response")
					? ["requestId", "runId", "id"]
					: ["id", "runId", "requestId"];
		const identity = identityKeys.map((key) => value[key]).find((candidate): candidate is string => typeof candidate === "string" && candidate.length > 0);
		if (!identity) return;
		const safe = {
			event,
			sourceEvent: family === "tintin" ? undefined : sourceEvent,
			observedAt: Date.now(),
			pid: process.pid,
			id: family === "tintin" ? identity : `${family}:${identity}`,
			...(typeof value.status === "string" ? { status: value.status } : {}),
			...(typeof value.durationMs === "number" && Number.isFinite(value.durationMs) ? { durationMs: value.durationMs } : {}),
		};
		try { appendFileSync(path, `${JSON.stringify(safe)}\n`, { encoding: "utf8", mode: 0o600 }); } catch { /* instrumentation must not affect the candidate */ }
	};
	for (const event of ["subagents:started", "subagents:completed", "subagents:failed"] as const) pi.events.on(event, (value) => record(event, value));
	// Public pi-subagents 0.69 contracts. Async lifecycle is the Nico protocol
	// with an explicit start and terminal event pair. Delegation and slash
	// bridges are also paired, but use requestId and a distinct identity prefix.
	pi.events.on("subagent:async-started", (value) => record("subagents:started", value, "nico-async", "subagent:async-started"));
	pi.events.on("subagent:async-complete", (value) => record("subagents:completed", value, "nico-async", "subagent:async-complete"));
	pi.events.on("prompt-template:subagent:started", (value) => record("subagents:started", value, "nico-delegation", "prompt-template:subagent:started"));
	pi.events.on("prompt-template:subagent:response", (value) => {
		const status = value && typeof value === "object" ? (value as Record<string, unknown>).status : undefined;
		record(status === "completed" ? "subagents:completed" : "subagents:failed", value, "nico-delegation", "prompt-template:subagent:response");
	});
	pi.events.on("subagent:slash:started", (value) => record("subagents:started", value, "nico-slash", "subagent:slash:started"));
	pi.events.on("subagent:slash:response", (value) => {
		const response = value && typeof value === "object" ? value as Record<string, unknown> : {};
		record(response.isError === true ? "subagents:failed" : "subagents:completed", value, "nico-slash", "subagent:slash:response");
	});
}
