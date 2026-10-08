# RunPod lifecycle for the candidate (2026-09-23)

Plan [`2026-09-23-review-remediation.md`](../superpowers/plans/2026-09-23-review-remediation.md),
Task 20, with a $2.00 cap. The runs used the working tree's broker (`pitwall-api` on
`127.0.0.1:18080`) with a dedicated `pitwall_live` database on the local test Postgres. The
installed local stack runs an older snapshot, so it was not used. The API key, endpoint key,
and RunPod key came from private files and are not reproduced here.

## Registry pod lease end to end

| Step | Command | Observed |
| --- | --- | --- |
| Dry run | `pitwall serve --capability live.ornith.t20 --model ornith-ai/Ornith-1.5-35B-A3B-GGUF --gpu-class "NVIDIA GeForce RTX 4090" --ttl-minutes 35 --max-usd-per-hour 0.80 --dry-run --json` | `llama.cpp`, variant `gguf:Q4_K_M`, live price, 35-minute estimate $0.43 |
| Serve | same command without `--dry-run` | Lease `lease_prov_01M3852ACYD63QZYJ416BJNXRD_ba40111dc0e7`, pod `e5op1zb9d8en0e`, created 22:09:48 UTC; ready (`lease_ready`) 22:17:29 |
| Proxied chat | `POST /v1/openai/live.ornith.t20/v1/chat/completions`, "Reply with exactly: PITWALL_T20_OK" | HTTP 200 in 2.1 s; content exactly `PITWALL_T20_OK`; 21 prompt and 231 completion tokens |
| Stop | `pitwall leases stop <lease-id> --reason t20-live-complete --json` | Lease `stopped` at 22:21:23; `cost_accrued_usd` 0.143313 |
| Cleanup | `pitwall runpod pods list --json` | No pods |
| Audit | `config_audit` in `pitwall_live` | `lease_ready` 22:17:29, `stop` 22:21:24, `lease_closed` 22:21:24 |

The plan's parameters changed. The RTX 3090 market offered no CUDA 12.8 or newer, so the run used
an RTX 4090. The TTL was 35 minutes instead of 15, because serve refuses a TTL within Ornith's
30-minute startup budget. Registry serve priced the secure-cloud 4090 at $0.74/hr, so the cap
was $0.80/hr instead of $0.60.

The stop logged that `lease.terminated` was not published: the CLI stop passed teardown no
Redis client. That is fixed in `c079ebb`, which covers the CLI, MCP, and `serve` teardowns.

## Serverless endpoint and volume control plane

Each mutation ran through `pitwall runpod … --request-json … --idempotency-key … --confirm …`.

| Resource | Create | Read | Update | Delete | Read after delete |
| --- | --- | --- | --- | --- | --- |
| Endpoint `tk30a8yg4mqaxu` (QUEUE, image `runpod/serverless-hello-world:latest`, pool `AMPERE_16`) | workers 0–1, idle 5 s | workers 0–1 | workers 0–2, idle 10 s; read back the same | `endpoint.delete` changed | `resource_not_found` |
| Volume `ficojj1dge` (EU-RO-1) | 10 GB | 10 GB | grown to 11 GB; read back 11 GB | `volume.delete` changed | `resource_not_found` |

No endpoint worker ran; the minimum stayed 0.

## Spend

Billing lags, so spend is pod uptime times the rate:

- Pod: 11.6 minutes at $0.74/hr = **$0.14** (matching the lease's `cost_accrued_usd` 0.143313).
- Endpoint: no workers, so $0.
- Volume: minutes of 10–11 GB storage, under $0.01.

**Total about $0.15, against the $2.00 cap.**
