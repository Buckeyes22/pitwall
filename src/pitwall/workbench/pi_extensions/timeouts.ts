/**
 * Apply the Workbench's solo command deadline at Pi's supported tool_call
 * boundary. Pi's built-in shell tools express their timeout in seconds;
 * Workbench profiles keep policy values in milliseconds.
 */
export function applySoloToolDeadline(event: { toolName: string; input: Record<string, unknown> }, timeoutMs: number | undefined): boolean {
  if (timeoutMs === undefined || (event.toolName !== "bash" && event.toolName !== "powershell")) return false;
  const maximumSeconds = timeoutMs / 1000;
  const requested = event.input.timeout;
  if (requested === undefined || (typeof requested === "number" && Number.isFinite(requested) && requested > maximumSeconds)) {
    event.input.timeout = maximumSeconds;
    return true;
  }
  return false;
}

/** Wrap a fetch implementation with a response-body idle deadline. Headers are
 * governed by the provider's fetch/dispatcher; this covers a body that stops
 * yielding chunks after headers have arrived. */
export function withResponseIdleTimeout(fetchImpl: typeof fetch, timeoutMs: number | undefined): typeof fetch {
  if (timeoutMs === undefined || timeoutMs <= 0) return fetchImpl;
  return (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const controller = new AbortController();
    const inputSignal = input instanceof Request ? input.signal : undefined;
    const callerSignal = init?.signal ?? inputSignal;
    const signal = callerSignal ? AbortSignal.any([callerSignal, controller.signal]) : controller.signal;
    const response = await fetchImpl(input, { ...init, signal });
    if (!response.body) return response;
    const reader = response.body.getReader();
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const body = new ReadableStream<Uint8Array>({
      async pull(streamController) {
        if (closed) return;
        let timedOut = false;
        const timeout = new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            timedOut = true;
            const error = new Error("provider response inactivity timeout");
            controller.abort(error);
            reject(error);
          }, timeoutMs);
        });
        try {
          const result = await Promise.race([reader.read(), timeout]);
          if (closed) return;
          if (result.done) { closed = true; streamController.close(); return; }
          streamController.enqueue(result.value);
        } catch (error) {
          closed = true;
          await reader.cancel(error).catch(() => undefined);
          streamController.error(error);
          if (!timedOut) throw error;
        } finally {
          if (timer) { clearTimeout(timer); timer = undefined; }
        }
      },
      async cancel(reason) {
        closed = true;
        if (timer) { clearTimeout(timer); timer = undefined; }
        await reader.cancel(reason).catch(() => undefined);
        reader.releaseLock();
      },
    });
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  }) as typeof fetch;
}
