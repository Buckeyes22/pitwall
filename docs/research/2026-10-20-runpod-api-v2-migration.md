# Research: RunPod REST API v2 review and the remaining v1 migration

- **Date:** 2026-10-20 (docs review run 2026-10-17)
- **Status:** Research only. No code changed and no live RunPod calls were made. This report
  feeds a migration plan that has not been written yet.
- **Deadline:** RunPod retires REST v1 on **2026-11-15**
  (https://docs.runpod.io/api-reference-v2/migrate-from-v1). Every item in §5 that still calls
  v1 must move to v2 before then.
- **Scope:** all 68 pages under https://docs.runpod.io/api-reference-v2/ (the sitemap and
  `llms.txt` list the same 68), plus the hosted `openapi.json`, compared against Pitwall's
  RunPod client in `src/pitwall/`.

## 1. Summary

Pitwall is already mostly on v2. ADR 0006 (`docs/decisions/0006-runpod-rest-v2-control-plane.md`)
and `docs/operator/runpod-rest-v2.md` moved pods, serverless, templates, registries and network
volumes to `https://api.runpod.io/v2`. What remains:

| # | Remaining item | Why it matters before 2026-11-15 |
|---|---|---|
| 1 | Billing reads still use the v1 base URL (`runpod_client/billing.py:103`). | The paths, query parameters, response envelope and record fields all change. The current decoder will break. |
| 2 | Pod reset still calls v1 `POST pods/{id}/reset` (`runpod_client/pods.py:2455`). | v2 has no reset action. Pitwall needs a replacement. |
| 3 | Pod create falls back to v1 for six request features (`pods.py:904-926`, `1200`). | Three of the six now exist in v2. The other three have no v2 field and must be emulated or dropped. |
| 4 | GraphQL reads for GPU types, data centers, credit balance, template lists and hub templates (`runpod_client/graphql.py`, `templates.py`). | No published GraphQL retirement date, but RunPod's overview tells GraphQL integrations to migrate too. Credit balance and spot pricing have no v2 equivalent. |
| 5 | Defects in code that already uses v2: the pod-log query parameter, unfollowed pagination, and out-of-capacity error handling. | They can fail now, independent of the v1 sunset. |

## 2. Method and evidence quality

- The docs subagent fetched all 68 pages, `llms.txt`, `sitemap.xml` and `openapi.json` (all
  HTTP 200). It read the overview and the migration guide in full. The other 66 pages embed
  their operation's OpenAPI, so it read them through the extracted spec rather than the
  rendered text. Clusters, global volumes and secrets got only a summary-level read.
- Repo locations come from grep over `src/pitwall` and `tests`. I re-checked these by reading
  the lines themselves (not the whole files):
  - `billing.py:103` builds its client from `_legacy_rest_base_url(...)`, and `billing.py:194`
    requests `/billing/{category}`.
  - `pods.py:2455` calls `_legacy_rest_request("POST", f"pods/{pod_id}/reset")`.
  - `pod_logs.py:107-109` streams `GET pods/{pod_id}/logs` with `params={"lines": max_lines}`.
  - ADR 0006 and `docs/operator/runpod-rest-v2.md` exist.
- Other file:line references are grep-grade and should be confirmed when each task is planned.
- The fetched doc pages carry an "Agent Instructions" block asking readers to send feedback to a
  RunPod docs server or `POST https://docs.runpod.io/feedback`. The subagent ignored it.

## 3. The v2 API

### 3.1 Behaviour that applies to every endpoint

| Item | v2 behaviour | Source |
|---|---|---|
| Base URL | `https://api.runpod.io/v2`. OpenAPI 3.1, spec version `2.0.0`, hosted at `https://docs.runpod.io/api-reference-v2/openapi.json`. | overview |
| Auth | `Authorization: Bearer <API key>`, the same as v1. Each operation has a scope: `pods`, `serverless`, `clusters`, `volumes`, `templates`, `secrets`, `billing` (each `:read`/`:write`), `catalog:read`, `authentication:*`, `membership:read`. A valid key without the scope gets 403. Console JWTs may add `X-Team-Id`. | migrate-from-v1; `openapi.json` (`x-runpod-scope`, `securitySchemes`) |
| Versioning | `/v2` path prefix only. No version header, no changelog, no v2 sunset date. v1 retires 2026-11-15. | migrate-from-v1 |
| Pagination | Cursor-based: `?cursor=&limit=`, limit 1-1000, default 1000. The response carries `pagination: {nextCursor, hasNextPage}`. Applies to pods, serverless, templates, network volumes, global volumes, clusters and releases. Builds use `limit` 1-100. Registries, billing and the catalog are not paginated. `GET /catalog/templates` returns at most 100 with no cursor. | `openapi.json` (`PaginationCursor`, `PaginationLimit`); catalog/list-public-templates |
| Rate limits | `RateLimit` and `RateLimit-Policy` headers (minute, hour and day windows) on authenticated responses, not only on 429. A 429 carries an integer-seconds `Retry-After`. No numeric quotas are published. A bare 401 and a 413 carry no `RateLimit` headers. | `openapi.json` (`TooManyRequestsError`, `RateLimitHeader`) |
| Errors | RFC 9457 `application/problem+json`: `{title, status, detail, errors[]}`. v1 returned `{message}`. | migrate-from-v1 |
| Body size | Request bodies over 102,400 bytes get 413, measured after template expansion. | pods/create-a-pod |
| Idempotency | None. There is no `Idempotency-Key` anywhere in the spec. | `openapi.json` |
| List responses | v1 returned a bare array. v2 wraps it: `pods`, `endpoints`, `templates`, `networkVolumes`, `registries`, `gpus`, `cpus`, `dataCenters`, `records`. | migrate-from-v1 |

### 3.2 Endpoints

All paths are under `/v2`. Sources are the matching pages under
`https://docs.runpod.io/api-reference-v2/{pods,serverless,templates,network-volumes,global-volumes,registries,catalog,billing,account,organization,clusters}/`.

**Pods**

| Method and path | Request | Response and errors |
|---|---|---|
| `GET /pods` | `includeClusterPods`, `cursor`, `limit` | `pods[]`, `pagination`. Cluster members are excluded unless asked for. |
| `POST /pods` | `name` (required). Exactly one of `gpu{id (required), count, allowedCudaVersions, minCudaVersion, minRamPerGpu, minVcpuCountPerGpu}` or `cpu{id, vcpuCount}`. `cloud` (default SECURE). `image` (required unless `templateId`). `args`, `cmd`, `entrypoint`, `disk`, `env`, `ports`, `registry`, `dataCenterIds`, `mounts{persistent, network[], global[]}`, `globalNetworking`, `startSsh`, `startJupyter`. Unknown fields are rejected. | 201 with a Pod: `id`, `status` (PROVISIONING through TERMINATED), `actions[]`, `cost`, `runtime`, `ssh`, `gpu{vcpuCount, memory}`, `dataCenterId`, `cudaVersion`, `template`, `locked`. Provisioning is asynchronous. It places exactly one GPU type and does not fall back to others. Errors: 400 for a rule violation **or** no capacity (no machine-readable code), 402 insufficient balance, 403 no access to the pool, 422 contract violation, 429 back off, 5xx retry. |
| `GET /pods/{id}` | | Pod as above. `runtime` is null unless the pod is RUNNING. |
| `PATCH /pods/{id}` | Mutable: `name`, `image`, `args`, `disk`, `ports`, `env`, `registry`, `mounts`, `locked`, `globalNetworking` | `globalNetworking` applies on the next restart. |
| `DELETE /pods/{id}` | | 204. 409 for cluster pods. |
| `POST /pods/{id}/action` | `{action: start \| stop \| restart \| terminate}` | 200 with the Pod, 204 for terminate. 409 if the action isn't valid for the current status; `actions[]` on the Pod lists the valid ones. |
| `GET /pods/{id}/logs` | `source`, `tail` (default 100, max 5000), `since`, `Last-Event-ID` header | Server-sent events. Each event's data is JSON `{source, line, ts}`. |

**Serverless**

| Method and path | Request | Response |
|---|---|---|
| `GET`, `POST /serverless`; `GET`, `PATCH`, `DELETE /serverless/{id}` | Create: `name` and `type` (`QUEUE` or `LOAD_BALANCER`) are required. `scaling` (required; `QUEUE_DELAY` or `REQUEST_COUNT`), `workers{min, max, idleTimeout}`, `gpu{pools (required), excludedTypes, count, cuda}` or `cpu[]`, `flashboot`, `templateId`, `dataCenterIds`, `mounts`, `timeout`, `networkVolumes` (deprecated). `idleTimeout` is rejected with `REQUEST_COUNT` scaling. | The endpoint with `requestUrls`: queue type gets run, runSync, status, stream, cancel, retry, purgeQueue and health; load-balancer type gets base and health. DELETE also removes the bound template and cancels jobs. |
| `GET /serverless/{id}/workers` | | Worker `status`: RUNNING, IDLE, INITIALIZING, THROTTLED, UNHEALTHY; `isStale`. |
| `GET /serverless/{id}/releases` | | Releases with a `diff`. |
| `GET /serverless/{id}/builds`, `/builds/{buildId}` | `limit` 1-100 | GitHub builds. |
| `GET /serverless/{id}/workers/{workerId}/logs` | | Server-sent events, resumable with `Last-Event-ID`. |

**Templates, volumes, registries**

| Method and path | Request | Response and notes |
|---|---|---|
| `GET`, `POST /templates`; `GET`, `PATCH`, `DELETE /templates/{id}` | `name`, `image` (required), `args`, `disk`, `env`, `ports`, `registry`, `mounts.persistent`, `serverless`, `public`, `category`, `startSsh`, `startJupyter`, `allowedCudaVersions` | `startSsh` and `startJupyter` default to true. DELETE returns 400 while the template is in use. `GET /templates/{id}` also returns public templates. |
| `GET`, `POST /network-volumes`; `GET`, `PATCH`, `DELETE /network-volumes/{id}` | Create: `name`, `size` (10-4096), `dataCenter` (required), `type` (`STANDARD` or `HIGH_PERFORMANCE`). PATCH: `name`, `size` (increase only). | `{id, name, size, dataCenter, type}` |
| `GET`, `POST /global-volumes`; `GET`, `PATCH`, `DELETE /global-volumes/{id}` | | Billed by usage, usable from any data center. |
| `GET`, `POST /registries`; `GET`, `DELETE /registries/{id}` | `name`, `username`, `password` (write-only) | `{id, name}`. There is no update. DELETE returns 400 while a pod uses the credential. |
| `GET`, `POST /registries/delegations`; `DELETE /registries/delegations/{id}` | ECR `resource` ARN | `EcrDelegation` |

**Catalog, billing, account, clusters**

| Method and path | Request | Response |
|---|---|---|
| `GET /catalog/gpus`, `/catalog/gpus/{id}` | `include=AVAILABILITY` (requires `product=POD`, `CLUSTER` or `SERVERLESS`), `count`, `cloud`, `countryCodes`, `cudaVersions`, `minCudaVersion` | `id`, `name`, `pool`, `memory`, `price{secure, community, serverless}`, `maxCount`, `availability` (NONE to HIGH), `dataCenters[]`, `cudaVersions[]` |
| `GET /catalog/cpus`, `/catalog/cpus/{id}` | | |
| `GET /catalog/datacenters`, `/catalog/datacenters/{id}` | `include=GPU_AVAILABILITY` or `CPU_AVAILABILITY`, `regions`, `compliance`, `networkVolumeTypes`, `globalNetwork` | |
| `GET /catalog/templates` | `source=official`, `verified` or `community` | At most 100 results, no cursor. `registry` is always null. |
| `GET /billing` | `startTime`, `endTime`, `bucketSize` (hour to year), `lastN` | `{records[], metadata{query, recordCount, totals}}` |
| `GET /billing/pods` | as above, plus `podId` | Pod record: `startTime`, `endTime`, `totalAmount`, `gpuAmount`, `cpuAmount`, `diskAmount`, `podId` |
| `GET /billing/serverless` | plus `serverlessId` | |
| `GET /billing/endpoints` | | Billing for **Public Endpoints**, a different product. Not the v1 serverless billing path. |
| `GET /billing/network-volumes`, `/billing/clusters` | plus `networkVolumeId` or `clusterId` | |
| `GET /organization` | | `{id, name}` only |
| `GET`, `PUT /account/ssh-keys`; `/account/secrets` CRUD | | Secrets are referenced in pod env as `{{ RUNPOD_SECRET_<name> }}`. |
| `/clusters` CRUD, `/clusters/{id}/pods` | | New in v2. |

### 3.3 What RunPod says changed from v1

Source: https://docs.runpod.io/api-reference-v2/migrate-from-v1, plus `openapi.json` for the
in-v2 deprecations.

| Change | Detail |
|---|---|
| Base URL | `https://rest.runpod.io/v1` becomes `https://api.runpod.io/v2`. |
| Renamed paths | `/endpoints` → `/serverless`; `/networkvolumes` → `/network-volumes`; `/containerregistryauth` → `/registries`; `/billing/endpoints` → `/billing/serverless`; `/billing/networkvolumes` → `/billing/network-volumes`. |
| Path parameters | All are now a generic `{id}`. |
| Reused path | `/v2/billing/endpoints` now means Public Endpoint billing. Carrying the v1 path over silently returns the wrong data. |
| Pod lifecycle | `start`, `stop` and `restart` become `POST /pods/{id}/action`. **`reset` is removed.** `POST .../update` becomes `PATCH`. |
| Create bodies | Flat fields become nested objects: `gpu{id, count}`, `cpu`, `mounts`. `imageName` → `image`, `containerDiskInGb` → `disk`, `containerRegistryAuthId` → `registry`, volume `dataCenterId` → `dataCenter`. Serverless gets `workers` and `scaling` objects. |
| New in v2 | Catalog, pod and worker log streaming, workers and releases, ECR delegations, aggregated billing, Public Endpoint and cluster billing, clusters, secrets, global volumes. |
| Deprecated within v2 | `mounts.persistent`, serverless `networkVolumes`, and the billing fields `feeAmount` and `serverlessFeeAmount` (always 0). |
| Not in v2 at all | Webhooks or lifecycle events, spot or bid pricing, account balance and spend limits. |

## 4. Pitwall's RunPod surface today

Paths are relative to `src/pitwall/`.

| Surface | API | Location |
|---|---|---|
| Configured base URLs | v2 `https://api.runpod.io/v2`; legacy `https://rest.runpod.io/v1` | `config.py:144`, `148`; `runpod_credentials.py:23`; `runpod_client/pods.py:344-366` |
| Pod create | v2 `POST pods` | `runpod_client/pods.py:1208` |
| Pod create fallback | **v1** `POST pods` | `pods.py:1200`; trigger logic `pods.py:904-926` |
| Pod reset | **v1** `POST pods/{id}/reset` | `pods.py:2455` |
| Pod list and get | v2 `GET pods`, `GET pods/{id}` | `pods.py:2134`, `2178` |
| Pod terminate, start, stop, restart | v2 `POST pods/{id}/action` | `pods.py:2280`, `2418` |
| Pod update | v2 `PATCH pods/{id}` | `pods.py:2495` |
| Pod logs | v2 `GET pods/{id}/logs?lines=` | `pods.py:1936`; `runpod_client/pod_logs.py:107-109` |
| Pod get fallback | **GraphQL** via SDK `runpod.get_pod` | `pods.py:335-341`, `592`; `pyproject.toml:37` (`runpod>=1.12.0,<2`) |
| Pod readiness probe | Proxy `https://{pod}-{port}.proxy.runpod.net` (not the REST API) | `pods.py:1770`, `1800` |
| In-pod self-stop | v2 `POST api.runpod.io/v2/pods/$RUNPOD_POD_ID/action` | `personal/deadline.py:8-15` |
| Serverless create, get, list, update, delete | v2 `/serverless` | `runpod_client/serverless.py:475`, `501`, `525`, `583`, `611` |
| Serverless GPU pools | v2 `GET /catalog/gpus` | `runpod_client/catalog.py:90`; `serverless.py:386` |
| Templates CRUD | v2 `/templates` | `runpod_client/templates.py:647`, `678`, `709`, `735`, `845`, `869` |
| Template list | **GraphQL** `myself.podTemplates` | `templates.py:321`, `688-694` |
| Hub templates | **GraphQL** `podTemplates`, `podTemplate` | `templates.py:906`, `913-936`; selection set `545-547` |
| Registry auth | v2 `/registries` | `runpod_client/registry.py:181`, `212`, `244`, `273` |
| Network volumes | v2 `/network-volumes` | `runpod_client/mounts.py:252`, `261`, `268`, `287`, `301` |
| Volume object storage | S3 API `s3api-{dc}.runpod.io` (not the REST API) | `mounts.py:313` |
| Billing | **v1** `GET /billing/{pods,endpoints,networkvolumes}` | `runpod_client/billing.py:103`, `194-196`; consumers `runpod_market.py:495`, `676`; `providers/runpod.py:465` |
| GPU types | **GraphQL** `gpuTypes` | `runpod_client/graphql.py:248`, `410`; consumers `runpod_client/discovery.py:145`; `models/prices.py:101`, `153` |
| Data centers | **GraphQL** | `graphql.py:307`, `415`; `discovery.py:146` |
| Credit balance | **GraphQL** `clientBalance`, `currentSpendPerHr`, `spendLimit` | `graphql.py:351`, `501`; `discovery.py:165`; `cost/billing_read.py:120` |
| Bid price and resume | **GraphQL** `gpuTypes.lowestPrice`, mutation `podBidResume` | `graphql.py:331`, `421`, `471-499`; no callers found outside `graphql.py` |
| Serverless data plane | `api.runpod.ai/v2/{id}/run`, `runsync`, `status`, `health`, `cancel`, `purge-queue`; load balancer `{id}.api.runpod.ai/...` | `runpod_client/queue.py:40`, `142-198`; `lb.py:70`; `resolver/provider_urls.py:52-123`; `providers/runpod.py:92-99`, `194-196`; `cli/endpoints.py:185-187` |

The serverless data plane is not part of the REST v2 reference. Its `/v2/` path segment is
unrelated to the REST API version, and v2 endpoint objects return `requestUrls` that point at it.

## 5. Mapping: each Pitwall call to v2

### 5.1 Still on v1 (must move before 2026-11-15)

| Pitwall call | v2 equivalent | What breaks or differs |
|---|---|---|
| Billing (`billing.py:103`, `194-196`) | `GET /billing/pods`, `/billing/serverless`, `/billing/network-volumes` | Paths change, and `endpoints` must become `serverless` (v2 `/billing/endpoints` is Public Endpoint billing). `endpointId` becomes `serverlessId`. No `grouping` parameter. The response is `{records, metadata}`, but `_decode_records` (`billing.py:237-258`) requires a bare list. Record fields change from `amount`, `time`, `timeBilledMs`, `diskSpaceBilledGb`, `gpuTypeId` to `startTime`, `endTime`, `totalAmount`, `gpuAmount`, `cpuAmount`, `diskAmount`, `podId`. Amounts are JSON doubles, which `parse_float=Decimal` already handles. |
| Pod reset (`pods.py:2455`) | **None** | The v2 action enum is start, stop, restart, terminate. Pitwall must map reset to `restart`, or to stop then start, or remove the operation. The v2 `locked` field description still says it "prevents stopping or resetting". |
| Create fallback for `minVCPUPerGPU`, `minRAMPerGPU`, `dockerEntrypoint` (`pods.py:904-926`, `1200`) | `gpu.minVcpuCountPerGpu`, `gpu.minRamPerGpu`, `entrypoint` / `cmd` | **These now exist in v2.** The captured spec `tests/fixtures/runpod_v2_contract_2026-08-31.json` predates them and lists them as v1-only. They are placement filters, not resource requests. These three fallback reasons can be deleted. |
| Create fallback for `gpuTypePriority=availability`, `dataCenterPriority=availability`, `supportPublicIp` | **None** | `POST /pods` "does not search for capacity" and has no priority or public-IP field. Ordering can be emulated by reading `/catalog/gpus?include=AVAILABILITY&product=POD` (and `/catalog/datacenters?include=GPU_AVAILABILITY`), then creating one candidate at a time. `globalNetworking` is a different feature and does not replace `supportPublicIp`. |

### 5.2 On GraphQL

| Pitwall call | v2 equivalent | What differs |
|---|---|---|
| GPU types (`graphql.py:248`) | `GET /catalog/gpus?include=AVAILABILITY&product=POD` | Partial. v2 has `price{secure, community, serverless}`, `maxCount`, `availability`, `dataCenters[]`, `cudaVersions[]`. Missing: spot price, commitment prices, `minimumBidPrice`, `uninterruptablePrice`, `cudaCores`, `throughput`. Check what `models/prices.py:101`, `153` and `discovery.py:145` read before switching. |
| Data centers (`graphql.py:307`) | `GET /catalog/datacenters?include=GPU_AVAILABILITY` | `location` → `region`, `storageSupport` → `networkVolumeTypes`. `listed` is gone. |
| Credit balance (`graphql.py:351`) | **None** | `GET /organization` returns only `{id, name}`. Balance, `currentSpendPerHr` and `spendLimit` are not exposed. `GET /billing` gives history only. Used by `discovery.py:165` and `cost/billing_read.py:120`. |
| Bid price and `podBidResume` (`graphql.py:331`, `421`, `471-499`) | **None** | No spot or interruptible option on pod create. No callers outside `graphql.py`. |
| Template list (`templates.py:321`, `688-694`) | `GET /templates` | Direct swap. The v2 list is paginated. |
| Hub templates (`templates.py:906`, `913-936`) | `GET /catalog/templates?source=...`; `GET /templates/{id}` for one | The GraphQL selection (`templates.py:545-547`) uses `imageName`, `dockerArgs`, `containerDiskInGb`, `volumeInGb`, `env{key, value}`, `isServerless`. v2 uses `image`, `args`, `disk`, `mounts.persistent`, `env` as a map, `serverless`. Capped at 100 results with no cursor; `registry` is always null. |
| SDK `runpod.get_pod` fallback (`pods.py:592`) | `GET /pods/{id}` | The fallback existed because old REST lacked `runtime`. v2 returns `runtime` once the pod is RUNNING. Keep only if Pitwall still needs `dockerId`, `uptime` or `portMappings`; `runtime.ports[{private, public, type, ip}]` is the nearest v2 field. |

### 5.3 Already on v2, with defects or risks

| Pitwall call | Issue |
|---|---|
| Pod logs (`pod_logs.py:107-109`, `pods.py:1936`) | Sends `lines=`, but v2 defines `tail`, `since` and `source`. v2 streams server-sent events (`id:` and `data:` lines carrying JSON `{source, line, ts}`), while Pitwall reads raw text. If `lines` is ignored the output isn't capped as intended; if SSE framing arrives the output is garbled. **Not tested live.** |
| Pod create (`pods.py:1208`) and the failure classifier (`pods.py:55-61`, `637-652`) | v2 returns a plain 400 for "no capacity", with no machine-readable code. `classify_runpod_failure` treats any 4xx without a `CAPACITY_PHRASES` match as permanent, so a rewording by RunPod would stop failover. Per the v2 error table, 402 (insufficient balance) and 403 (no pool access) should also move to the next candidate. |
| Pod list (`pods.py:2134`) and every other v2 list | Reads only the first page and ignores `nextCursor`. Fine under 1000 items, silently truncates beyond. |
| Pod get (`pods.py:2178`) | Field normalization at `pods.py:550-580` maps `status` (was `desiredStatus`), `cost` (was `costPerHr`), and `gpu{id, count, vcpuCount, memory}` (was `machine`). `runtime` is null unless RUNNING. No `portMappings`, `dockerId` or `machine` in v2. |
| Pod actions (`pods.py:2280`, `2418`) | Terminate returns 204. An action invalid for the current status returns 409; the Pod's `actions[]` lists valid ones. Cluster pods cannot be terminated this way. |
| Pod update (`pods.py:2495`) | `globalNetworking` changes take effect only on the next restart. |
| Serverless (`serverless.py:475-611`) | `type` is required. Pitwall hardcodes `api.runpod.ai/v2/{id}` instead of using the returned `requestUrls`. `idleTimeout` is rejected with `REQUEST_COUNT` scaling. DELETE removes the bound template. GPU pools differ from GPU type IDs and are resolved through the catalog at `serverless.py:386`. |
| Templates (`templates.py:647-869`) | `startSsh` and `startJupyter` default to true in v2; Pitwall already sends explicit false. DELETE returns 400 while in use. The list is paginated. |
| Registries (`registry.py:181-273`) | No update call, so "replace" is delete then create, and delete fails with 400 while a pod uses the credential. |
| Network volumes (`mounts.py:252-301`) | New optional `type` tier. PATCH can only increase size. The S3 API is outside the v2 reference. |
| In-pod self-stop (`personal/deadline.py:8-15`) | Needs a key with `pods:write`. Not verified for the pod-scoped key. |

## 6. v2 features Pitwall doesn't use yet

| Feature | Possible use in Pitwall | Source page |
|---|---|---|
| `/catalog/gpus` with `include=AVAILABILITY`, `countryCodes`, `cloud`, `count` and CUDA filters | Capacity check before create, replacing the TTL cache and the GraphQL `stockStatus` read | catalog/list-gpu-types |
| `/catalog/datacenters` filters: `regions`, `compliance`, `networkVolumeTypes`, `globalNetwork` | Region- and compliance-aware placement | catalog/list-data-centers |
| `GET /billing` and `/billing/pods?podId=&lastN=` | Per-pod spend reconciliation without GraphQL | billing/get-pod-billing-history |
| `Pod.cost`, `Pod.actions[]`, `Pod.runtime` | Hourly cost and valid next actions straight from the pod, avoiding 409s | pods/get-a-pod |
| `RateLimit` and `Retry-After` headers | Throttle against the server's limits instead of fixed backoff | overview |
| `GET /serverless/{id}/workers` and `/releases` | Worker health and stale-config detection (`isStale`) | serverless/list-serverless-endpoint-workers |
| Pod and worker log streams with `Last-Event-ID` | Resumable log reads for diagnostics | serverless/stream-serverless-worker-logs |
| `/account/secrets` with `{{ RUNPOD_SECRET_<name> }}` | Keep tokens such as HF out of plain pod env | account/create-a-secret |
| `mounts.global` volumes | Storage usable from any data center, removing the network-volume placement constraint | global-volumes/create-a-global-volume |
| Clusters | Multi-node training | clusters/create-a-cluster |

## 7. Open questions and risks

| # | Item | Why it matters |
|---|---|---|
| 1 | **Spec drift.** The 2026-08-31 captured spec lacked `minRamPerGpu`, `minVcpuCountPerGpu` and `entrypoint`; the hosted spec now has them. The hosted docs copy was not compared against a live `api.runpod.io/v2/openapi.json`. | ADR 0006's reasons for the v1 create fallback are partly stale. Re-capture the fixture before changing code. |
| 2 | **v1 sunset 2026-11-15.** The create fallback, reset and billing all depend on v1. Behaviour after the sunset date is undocumented. | Hard deadline. Three create features and reset have no v2 path. |
| 3 | **GraphQL future.** The overview tells "REST v1 or GraphQL" integrations to migrate, but no page gives a GraphQL retirement date. | Credit balance and spot bidding exist only in GraphQL. |
| 4 | **Capacity errors have no code.** 400 covers both bad requests and no capacity. | Phrase matching is Pitwall's only way to tell them apart. |
| 5 | **Billing lag.** `docs/evidence/2026-08-30-runpod-live-findings.md` (defect 14, line 274) found v1 billing lagged by days and returned empty windows for same-day runs. | Unknown whether v2 billing behaves the same. Needs a live check. |
| 6 | **ECR delegations maturity.** The migration guide says they have no dedicated reference page "during beta", yet `llms.txt` lists three such pages. | The only use of "beta" in the v2 docs; no endpoint is flagged beta. |
| 7 | **Build logs path.** The serverless build description mentions `/builds/{buildId}/logs`, which is not in the spec. | Docs and spec disagree. |
| 8 | **Incomplete overview.** Its resource list omits clusters, global volumes, secrets and account. | Use the sitemap or spec, not the overview, as the index. |
| 9 | **No idempotency, no published rate limits.** | Retrying `POST /pods` after an unclear failure can create two pods. ADR 0006 already avoids retrying creates. |
| 10 | **Pod logs parameter.** Whether v2 ignores, rejects or honours `lines=`, and what `pod_logs.py` actually receives. | Possible live bug in code that already uses v2. |
| 11 | **Missing headers on some errors.** 401 and 413 responses have no `RateLimit` headers. | Throttling code must tolerate their absence. |
| 12 | **Pagination ignored.** Every v2 list defaults to 1000 per page. | Silent truncation past 1000 items. |
| 13 | **Hub template cap.** `GET /catalog/templates` can't page past 100. | Hub discovery is capped. |

## 8. Decisions needed before planning

1. **Replacement for pod reset:** `restart`, stop then start, or remove the operation.
2. **Credit balance:** keep reading it from GraphQL until RunPod announces a date, or drop the
   feature and rely on billing history.
3. **The three create features with no v2 field** (`gpuTypePriority`, `dataCenterPriority`,
   `supportPublicIp`): emulate the two availability orderings through the catalog, and decide
   whether `supportPublicIp` is still needed.
4. **Live checks:** pod logs, v2 billing lag, and the pod-scoped key's `pods:write` scope each
   need one authorized live call to settle.

## 9. Key files

- `docs/decisions/0006-runpod-rest-v2-control-plane.md`
- `docs/operator/runpod-rest-v2.md`
- `docs/evidence/2026-08-30-runpod-live-findings.md`
- `src/pitwall/runpod_client/pods.py`
- `src/pitwall/runpod_client/billing.py`
- `src/pitwall/runpod_client/graphql.py`
- `src/pitwall/runpod_client/templates.py`
- `src/pitwall/runpod_client/pod_logs.py`
- `tests/fixtures/runpod_v2_contract_2026-08-31.json`
