# Personal serve live round trip (2026-09-23)

Plan [`2026-09-23-review-remediation.md`](../superpowers/plans/2026-09-23-review-remediation.md),
Task 18: `tests/live/test_personal_serve_live.py` serves Ornith
(`ornith-ai/Ornith-1.5-35B-A3B-GGUF`, llama.cpp, Q4_K_M) on a community pod. The test then
checks the key-protected `/v1/models`, confirms a request without the key is refused, probes the
attached routing route, and waits for the pod's in-pod timer to terminate it.

**Result: passed on the final run** (`1 passed in 2143.91s`). The pod was `mfqow4qud0btag` on an
RTX 3090, created 00:21 UTC on 2026-09-24. The model answered after about 5 minutes. At the
35-minute TTL the pod terminated itself, and `pitwall runpod pods list` then showed no pods.

## Command

```bash
RUNPOD_API_KEY=<read-write key from a private file> \
PITWALL_ROUTING_CLI=<repo>/packages/agent-routing/scripts/pitwall-agent-routing \
PITWALL_LIVE_GPU_CLASS="NVIDIA GeForce RTX 3090" PITWALL_RUN_LIVE=1 \
  uv run --frozen pytest -m live -p no:randomly tests/live/test_personal_serve_live.py -s
```

`PITWALL_LIVE_TTL_MINUTES` defaults to 35, above Ornith's 30-minute startup budget.

## Every attempt

Each failure below was a defect or a market condition, and each defect is fixed with a test.

| # | GPU | Outcome | Cause | Fix | Spend |
| --- | --- | --- | --- | --- | --- |
| 1 | RTX 3090 | 401 before create | The run's environment had no RunPod key | Run setup | $0 |
| 2 | RTX 3090 | `cuda_unavailable` | The market offered no CUDA 12.8 or newer for the class then | None needed: correct refusal | $0 |
| 3 | RTX 4090 | Pod ended at 15 min, model not ready | The TTL was inside the model's 30-minute startup budget | `1570d40`: serve refuses `ttl_below_startup` | ~$0.09 |
| 4 | RTX 4090 | RunPod HTTP 500 on create, raised as a traceback | Create errors were not a named refusal | `b6f2217`: `create_failed` | $0 |
| 5 | RTX 4090 | `/v1/models` answered 401 until stopped | The readiness probe never sent the endpoint key | `c2cbb0b` | ~$0.08 |
| 6 | RTX 4090 | Model ready; route probe failed | RunPod's Cloudflare edge answers 403 to urllib's default user agent | `5dc09c4`: Agent Routing sends `pitwall-agent-routing/1` | ~$0.05 |
| 7 | RTX 4090 | `container_restarting` | The container crash-looped on its host; the logs went with the pod | `ae9c539`: failures carry the pod's last 40 log lines | ~$0.05 |
| 8 | RTX 4090 | `create_failed`: no instances available | Capacity | None needed | $0 |
| 9 | RTX 4090 | A capacity retry created pod `4tpatvftvvryn2`; stopping the retry loop with SIGTERM skipped the test's cleanup | Operator error: the test cleans up on Ctrl-C | Terminated with `pitwall runpod pods terminate` | ~$0.03 |
| 10 | RTX A6000 | `cuda_unavailable` | No CUDA 12.8 or newer offered then | None needed | $0 |
| 11 | RTX 5000 Ada | `create_failed` | Offered only CUDA 13.1, which RunPod's create enum (up to 13.0) cannot request | `b0a0bb7`: refused as `cuda_unavailable` before launch | $0 |
| 12 | RTX 3090 | Model ready; route probe failed | `PITWALL_ROUTING_CLI` was ignored, so the installed routing CLI (without the user-agent fix) ran | `5523156`: the setting is read from the environment | ~$0.03 |
| 13 | RTX 3090 | **Passed** | — | — | ~$0.13 |

Attempts 7 and 12 also set `PITWALL_ROUTING_CLI`, which was ignored until `5523156`.

Other findings from these runs, fixed in the same batch:

- A redis outage no longer fails a completed lease stop (`c079ebb`).
- The console escapes pod log text; a `[/]` in a log line crashed the serve screen (`ae9c539`).

## Spend

Billing lags, so each figure is pod uptime times the rate: about $0.34/hr for the RTX 4090
and $0.22/hr for the RTX 3090 (community).

**Total for T18: about $0.46.** No pod remains.
