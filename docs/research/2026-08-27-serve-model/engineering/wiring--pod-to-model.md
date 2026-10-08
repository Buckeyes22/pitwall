# Wiring a RunPod pod to model weights

**Research date:** 2026-08-27.  This is an implementation-facing recommendation, not evidence that a particular model/image/provider combination has been live-tested.  No pod was started and no weights were downloaded for this research.

## Decisive conclusion

Use a **separate RunPod network volume per datacenter as a Hugging Face cache**, set `HF_HOME=/workspace/hf` for both the pre-warm pod and serving pod, and keep a small explicitly sized container disk for the image, temporary files, and compilation artifacts.  A network volume is durable independently of a Pod, is mounted at `/workspace`, is Secure-Cloud-only, and pins the Pod to that volume's datacenter; data is not replicated to another datacenter automatically. [RunPod network volumes](https://docs.runpod.io/storage/network-volumes), [RunPod storage types](https://docs.runpod.io/pods/storage/types)

This gives a cold first fetch per `(datacenter, repo, revision)`, followed by cache hits on later leases.  Do **not** put a writable HF cache on the same volume for multiple simultaneous first downloads without a lock/job serialization: RunPod warns that concurrent writes to one network volume can corrupt data. [RunPod network volumes](https://docs.runpod.io/storage/network-volumes)

## 1. What happens when the server starts

### Shared HF cache semantics

`huggingface_hub` stores repository snapshots under `HF_HUB_CACHE`, defaulting to `$HF_HOME/hub`; `HF_HOME` itself defaults to `~/.cache/huggingface` (unless `XDG_CACHE_HOME` is set).  Thus the generic default repo cache is `~/.cache/huggingface/hub`.  It uses refs, blobs, and snapshot directories, so retain the whole cache tree rather than copying just a snapshot directory. [HF environment variables](https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables), [HF cache layout](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)

| Engine invocation | Boot download/load behavior | Default in its official image / robust cache setting |
| --- | --- | --- |
| `vllm serve <hf-id>` | A Hub model ID is downloaded then loaded; vLLM documents `--download-dir` as the directory to download *and load* weights, whose default is the HF cache. | The normal `vllm/vllm-openai` image is root by default; its source sets `ENTRYPOINT ["vllm", "serve"]`. vLLM's documented non-root setup uses `/home/vllm/.cache/huggingface`; the non-root target's `HOME` is `/home/vllm`. Do not depend on the tag/user default: set `HF_HOME=/workspace/hf` and preferably `--download-dir /workspace/hf/hub`. [vLLM serve](https://docs.vllm.ai/en/latest/cli/serve/), [vLLM Docker](https://docs.vllm.ai/en/stable/deployment/docker/), [Dockerfile](https://github.com/vllm-project/vllm/blob/main/docker/Dockerfile) |
| `python3 -m sglang.launch_server --model-path <hf-id>` | `--model-path` accepts either a local folder or HF repository ID.  SGLang has `--download-dir` specifically for Hugging Face model downloads. | SGLang's official Docker example mounts `~/.cache/huggingface` to `/root/.cache/huggingface`, establishing the image's documented root-user cache convention. Set `HF_HOME=/workspace/hf` and `--download-dir /workspace/hf/hub`. [SGLang arguments](https://docs.sglang.ai/advanced_features/server_arguments.html), [SGLang Docker install](https://github.com/sgl-project/sglang/blob/main/docs/docs/get-started/install.mdx) |
| `llama-server -hf <repo>:<quant>` | `-hf/--hf-repo` selects a GGUF model from the Hub; `<quant>` is optional, defaults to `Q4_K_M`, falls back to the first repo file if unavailable, and may also fetch a multimodal projector. | llama.cpp resolves cache in this order: `LLAMA_CACHE`, `HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, `HF_HOME/hub`, `XDG_CACHE_HOME/huggingface/hub`, then `$HOME/.cache/huggingface/hub`. The official Docker documentation does not declare a fixed `LLAMA_CACHE`/`HOME` default: treat its in-image default as **unverified** and set `LLAMA_CACHE=/workspace/llama-cache` explicitly. [llama-server flags](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [cache implementation](https://github.com/ggml-org/llama.cpp/blob/master/common/hf-cache.cpp), [CUDA image docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md) |

For llama.cpp, do not assume that an HF cache warmed for Python libraries is interchangeable with the server's model cache across all versions; giving it its own `LLAMA_CACHE` avoids that coupling.  For vLLM, persisting only HF weights does not persist its default `VLLM_CACHE_ROOT` compile cache, so a new container still recompiles. [vLLM Docker](https://docs.vllm.ai/en/stable/deployment/docker/)

### Throughput, access, and failure handling

Download elapsed time is approximately `bytes-to-fetch / achieved-throughput`, plus Hub metadata, checksum/cache work, volume writes, and server weight-loading/initialization.  It is therefore model-revision and quant-file-size dependent; no universal “minutes per model” number is supportable.  Hub's current client uses `hf-xet` when installed; `HF_XET_HIGH_PERFORMANCE=1` asks it to consume more network/CPU resources.  The previously common `HF_HUB_ENABLE_HF_TRANSFER=1` is deprecated/ignored in current `huggingface_hub` v1: `hf_transfer` was removed.  Older pinned images may still use the legacy path, so record the image's hub version rather than assuming the flag helps. [HF environment variables](https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables), [HF v1 migration](https://huggingface.co/docs/huggingface_hub/main/concepts/migration)

`HF_TOKEN` is the documented environment variable and overrides a token stored in the cache/home.  A gated repository also requires that the token's **individual user** has accepted/been granted access; a token alone cannot bypass a pending access request.  `HUGGING_FACE_HUB_TOKEN` is **not documented by current `huggingface_hub` as the canonical variable**; pass `HF_TOKEN` (and, only for compatibility with an explicitly tested image, the alias) rather than relying on it. [HF environment variables](https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables), [HF gated models](https://huggingface.co/docs/hub/models-gated)

Expected launch failures to classify and surface before readiness:

- insufficient network-volume capacity or container disk: failed writes/unpack/cache creation; calculate against the repository's actual selected files plus headroom, and preflight with `df -B1` in the warming job;
- 401/403: absent/invalid token or unaccepted gated-repo license/access; 429: Hub rate limit; retry only boundedly with jitter and preserve the real status;
- connect/read timeout: the Hub default download timeout is 10 seconds; set a deliberately tested `HF_HUB_DOWNLOAD_TIMEOUT` for slow paths;
- interrupted/partial fetch: retain the cache metadata/blobs and rerun `hf download` (do not `rm -rf` a shared cache); finally use `hf cache verify <repo> --cache-dir /workspace/hf/hub` before declaring the warm cache usable;
- unsupported architecture, CPU/GPU OOM, or engine initialization failure after a successful download: distinguish these from download failures in Pitwall's workload result.

The timeout behavior and the `hf download`/cache-verification interfaces are documented by Hugging Face. [HF CLI](https://huggingface.co/docs/huggingface_hub/en/guides/cli), [HF download guide](https://huggingface.co/docs/huggingface_hub/en/guides/download)

## 2. RunPod storage decision

| Storage | Lifetime / mount | Cost and consequence |
| --- | --- | --- |
| Container disk | Temporary OS/session storage; lost when the Pod stops/restarts. | $0.10/GB/month while running, no charge stopped. It is local/fastest but cannot carry weights across leases. |
| Volume disk | `/workspace`; retained until that Pod is deleted; not shareable. | $0.10/GB/month running, $0.20/GB/month stopped. Useful for a long-lived manually managed Pod, not a disposable lease cache. |
| Network volume | Replaces volume disk at `/workspace`; persists independently of Pods and can be attached to multiple Pods. | $0.07/GB/month below 1 TB and $0.05/GB/month above 1 TB, billed hourly. Variable network performance; Pods can use it only in Secure Cloud and only in its datacenter. |

These are RunPod's published rates and behavior as accessed on the research date. [Storage types](https://docs.runpod.io/pods/storage/types), [Pod pricing](https://docs.runpod.io/pods/pricing)

Network volumes are sized in the chosen datacenter (maximum capacity **unverified from the volume-creation doc**); RunPod's S3 API page specifies a maximum 4-TB *file* size, not a general volume-size maximum.  The S3-compatible API is available only in listed/select datacenters and maps `s3://VOLUME_ID/path` to `/workspace/path`; it needs a separate RunPod S3 API key.  It can pre-populate a volume without a compute Pod, but the documented `sync` caveat for >10,000 files is relevant to raw Hub-cache trees. [RunPod S3 API](https://docs.runpod.io/storage/s3-api)

**Recommended cache layout.** Use exactly one writable cache root per datacenter, e.g. `/workspace/hf` (which contains `hub/`) for Python engines and `/workspace/llama-cache` for llama.cpp.  Attach the volume while creating the Pod; it cannot be added/removed later.  Pin the provider/lease placement to the corresponding `data_center_id`.  Keep different datacenters' cache volumes separate and copy/pre-warm each explicitly; RunPod does not synchronize them. [RunPod network volumes](https://docs.runpod.io/storage/network-volumes)

## 3. Pre-warming

RunPod does not document a Pods-specific managed Hugging Face model-cache feature in the sources reviewed.  Its documented alternatives are a network volume and the S3-compatible API, which can populate files without launching compute.  Therefore `hf download` in a short-lived Pod is the most portable initialization path; account for one temporary Pod's image-pull/boot plus download cost, then network-volume storage continues to bill even between leases. [RunPod network volumes](https://docs.runpod.io/storage/network-volumes), [RunPod S3 API](https://docs.runpod.io/storage/s3-api), [RunPod pricing](https://docs.runpod.io/pods/pricing)

### Proposal — pre-warm Pod payload

This is a proposed, not-yet-implemented, operator payload.  It chooses a CPU-capable `python:3.11-slim` image because the operation is a Hub download, not inference; test image pull, package version, and egress in the target RunPod DC before operationalizing it.

```sh
# image: python:3.11-slim
# network volume: attach at /workspace in the target data center
# required env: MODEL_ID, MODEL_REVISION (optional), HF_TOKEN only if needed
# cache env: HF_HOME=/workspace/hf  HF_HUB_CACHE=/workspace/hf/hub
# performance: HF_XET_HIGH_PERFORMANCE=1 (current hub client)
set -eu
python -m pip install --no-cache-dir 'huggingface_hub>=1,<2'
if [ -n "${HF_TOKEN:-}" ]; then
  hf download "$MODEL_ID" --revision "${MODEL_REVISION:-main}" \
    --cache-dir /workspace/hf/hub --token "$HF_TOKEN"
else
  hf download "$MODEL_ID" --revision "${MODEL_REVISION:-main}" \
    --cache-dir /workspace/hf/hub
fi
hf cache verify "$MODEL_ID" --revision "${MODEL_REVISION:-main}" \
  --cache-dir /workspace/hf/hub --fail-on-missing-files
```

In production, render the token argument safely (not via the shown illustrative shell expansion), pin the Hub package/image digest and model commit SHA, use a per-volume lock, and write a completion manifest containing repo/revision/commit/bytes/check time.  The CLI's `--cache-dir`, `--revision`, `--token`, and include/exclude support make it possible to pre-warm a precise revision or only selected files. [HF CLI reference](https://huggingface.co/docs/huggingface_hub/main/package_reference/cli), [HF download guide](https://huggingface.co/docs/huggingface_hub/en/guides/download)

For GGUF/llama.cpp, pre-warm separately with the intended server image and `LLAMA_CACHE=/workspace/llama-cache`, using `llama-server -hf <repo>:<quant>` (or a compatible `llama download` command after image/version verification).  Do not use `hf download` blindly for a giant multi-quant repo: select the actual GGUF plus any required `mmproj` files or use llama.cpp's resolver, which knows the `:quant` convention. [llama-server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## 4. What Pitwall has today (read-only inspection)

### Existing wiring

- `pitwall warm-volume` exists and parses a capability, network `--volume-id`, optional provider/script, dry run, and a default 1,800-second timeout (`src/pitwall/cli.py:1270` (since split into `src/pitwall/cli/`), `src/pitwall/cli.py:1283` (since split into `src/pitwall/cli/`)).
- Its async path validates configuration/provider GPU, creates a one-GPU, 20-GB-container-disk workload, attaches `network_volume_id=volume_id`, pins with `RUNPOD_DATA_CENTER_ID`, runs a base64-injected Python script, polls for exit, and terminates the Pod (`src/pitwall/cli.py:1462` (since split into `src/pitwall/cli/`), `src/pitwall/cli.py:1475` (since split into `src/pitwall/cli/`), `src/pitwall/cli.py:1523` (since split into `src/pitwall/cli/`), `src/pitwall/cli.py:1552` (since split into `src/pitwall/cli/`)). It only passes `HUGGINGFACE_TOKEN` if that host variable is set—not the canonical `HF_TOKEN` (`src/pitwall/cli.py:1481` (since split into `src/pitwall/cli/`)).
- Despite the CLI description, `_build_prewarm_script` merely prints `PREWARM_START`/`PREWARM_COMPLETE`; it does not run `hf download`, verify bytes, select a model, or write to `/workspace` (`src/pitwall/cli.py:1602` (since split into `src/pitwall/cli/`)). So it is orchestration scaffolding, **not model pre-warming**.
- Pod-lease launch accepts `network_volume_id` or `volume_id`, accepts `volume_mount_path` or `volume_mount` (default `/workspace`), and defaults `container_disk_gb` to 50 ([src/pitwall/api/leases/launch.py:279](../../../../src/pitwall/api/leases/launch.py#L279), [src/pitwall/api/leases/launch.py:284](../../../../src/pitwall/api/leases/launch.py#L284), [src/pitwall/api/leases/launch.py:288](../../../../src/pitwall/api/leases/launch.py#L288)). `prepare_lease_launch` carries the volume, DC, env, and provider `docker_start_cmd` into pod creation ([src/pitwall/api/leases/launch.py:677](../../../../src/pitwall/api/leases/launch.py#L677), [src/pitwall/api/leases/launch.py:933](../../../../src/pitwall/api/leases/launch.py#L933)).
- The mount constants distinguish Pods `/workspace` from Serverless `/runpod-volume`; the network-volume client implements CRUD and an S3 endpoint `https://s3api-<dc>.runpod.io` with per-volume buckets ([src/pitwall/runpod_client/mounts.py:27](../../../../src/pitwall/runpod_client/mounts.py#L27), [src/pitwall/runpod_client/mounts.py:94](../../../../src/pitwall/runpod_client/mounts.py#L94), [src/pitwall/runpod_client/mounts.py:212](../../../../src/pitwall/runpod_client/mounts.py#L212)). The SDLC integration document describes the same client and credential inputs ([docs/sdlc/08-runpod-integration.md:386](../../../../docs/sdlc/08-runpod-integration.md), [docs/sdlc/08-runpod-integration.md:411](../../../../docs/sdlc/08-runpod-integration.md)).
- Today `_env_for_pod` starts with provider `env_vars`, forwards only Redis/Langfuse/R2 endpoint/bucket from the broker process, vends staging credentials, and accepts explicit `extra_env` ([src/pitwall/api/leases/launch.py:64](../../../../src/pitwall/api/leases/launch.py#L64), [src/pitwall/api/leases/launch.py:599](../../../../src/pitwall/api/leases/launch.py#L599)). **`HF_TOKEN` does not reach a normal lease automatically.** It can reach one only if configured in `provider.config.env_vars` or supplied through the launch caller's `extra_env`; neither is a purpose-built secret-vending policy.
- The staging-store system is independent of model storage: with appropriate configuration it vends temporary AWS-style R2 credentials to Pods; otherwise it is a no-op ([src/pitwall/staging_store.py:15](../../../../src/pitwall/staging_store.py#L15), [src/pitwall/staging_store.py:45](../../../../src/pitwall/staging_store.py#L45), [src/pitwall/staging_store.py:68](../../../../src/pitwall/staging_store.py#L68)). Do not reuse those credentials/token paths as HF authentication without an explicit design.
- Operator material already encodes the relevant control-plane constraints: network volumes force Secure Cloud and a single pinned DC ([docs/operator/16-check-audit-procedure.md:79](../../../../docs/operator/16-check-audit-procedure.md), [docs/operator/16-check-audit-procedure.md:193](../../../../docs/operator/16-check-audit-procedure.md)); it recommends explicit disk sizes of vLLM 80 GB, embed 40 GB, slim 20 GB ([docs/operator/16-check-audit-procedure.md:237](../../../../docs/operator/16-check-audit-procedure.md)); the journey catalog tests only dry-run/fake-key `warm-volume`, not a real model cache ([docs/operator/user-journey-catalog.md:45](../../../../docs/operator/user-journey-catalog.md)).

### Missing for `serve-model`

There is no inspected implementation that: maps model ID/revision/quant to provider config; produces engine-specific `docker_start_cmd`; sets `HF_HOME`/`HF_HUB_CACHE`/`LLAMA_CACHE`; securely vends a short-lived HF token; performs actual warm/download/verification/locking/manifesting; detects warm-cache hit vs miss; estimates cache/disk requirement; or ties readiness timeout and failure taxonomy to model byte size.  The generic launch, template, volume, budget, and R2-staging primitives already exist.

## 5. Recommended rollout

| Option | Operation | Trade-off / recommendation |
| --- | --- | --- |
| A. Cold fetch per lease | No volume; server receives HF ID and `HF_TOKEN` if needed. | Lowest persistent-storage/implementation complexity, but every pod bears image pull + full download + model initialization. Suitable only for small public models or rare leases. Use a container disk at least `ceil(1.25 × selected-weight-bytes / GiB) + 30 GiB`; 80 GB is the current operator baseline for vLLM. |
| B. Per-DC cache volume | Attach provider's one DC-local network volume; set engine cache env to `/workspace`. | Best default. First lease per revision is cold; later leases skip bytes transfer but still load weights and (for vLLM) compile a fresh container. Adds recurring $/GB storage, DC-capacity constraint, locking and eviction/manifest work. Never store a long-lived personal HF token in the volume. |
| C. Pre-warm before capacity lease | Run the proposed job per target `(DC, model revision, engine/quant cache format)`, verify then mark cache ready. | Most predictable customer startup and can fail before reserving expensive serving GPUs. Costs a short warm job plus persistent volume, needs queue/lock/status/UI and explicit gated-token vending. Prefer this for large/gated/high-traffic models. |

### Proposed deterministic serve configuration

Provider configuration should carry `network_volume_id`, `data_center_id`, `volume_mount: "/workspace"`, explicit `container_disk_gb`, `image_ref`, and a rendered `docker_start_cmd`; model record should carry immutable `hf_repo`, `revision`, quant/file selector, selected-weight bytes, and engine.  Use an ephemeral `HF_TOKEN` injection only for the launch/warm job if the model needs it, redact it from all persisted launch payloads/logs, and do not forward the broker process environment wholesale.

```text
vLLM:  env HF_HOME=/workspace/hf HF_HUB_CACHE=/workspace/hf/hub
       args <hf-repo> --download-dir /workspace/hf/hub

SGLang: env HF_HOME=/workspace/hf HF_HUB_CACHE=/workspace/hf/hub
       args python3 -m sglang.launch_server --model-path <hf-repo>
            --download-dir /workspace/hf/hub

llama.cpp: env LLAMA_CACHE=/workspace/llama-cache
           args -hf <repo>:<quant>
```

The arguments above are cache wiring only; host/port, GPU offload, context length, TP, model flags, and model readiness ID remain engine/model dossier decisions.

**Disk and readiness heuristic (proposal).** With Option B/C, set **80 GB container disk for vLLM**, matching existing operator guidance; set a larger value only when engine-specific temporary unpack/compile requirements demonstrably exceed it.  Size the *network volume* to at least `ceil(1.30 × selected-download-bytes / GiB) + 20 GiB` per active model set to allow cache metadata, revision coexistence, and verification.  For no-volume cold downloads, move that same weight allowance onto container disk.  Set `readiness_timeout_s = max(900, 300 + 1.5 × observed_p95_download_seconds(size, DC) + observed_p95_engine_init_seconds(engine, model, GPU, quant))`, capped by an operator policy; cache-hit timeout should omit the download term.  Until telemetry exists, use the command's current 1,800 seconds as a conservative initial cap, report phase timestamps, then replace estimates with P95 per model/DC/image.  These numbers are a proposal, not vendor-specified guarantees.

## Sources

- https://docs.runpod.io/pods/storage/types — container/volume/network volume persistence, mount paths, sharing, published rates — accessed 2026-08-27
- https://docs.runpod.io/storage/network-volumes — Secure-Cloud and datacenter attachment constraints, no automatic cross-DC sync, concurrency warning — accessed 2026-08-27
- https://docs.runpod.io/pods/pricing — published storage rates and billing cadence — accessed 2026-08-27
- https://docs.runpod.io/storage/s3-api — S3-compatible access, DC availability, path mapping, 4-TB maximum file size — accessed 2026-08-27
- https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables — cache and token defaults, Xet/high-performance and legacy transfer variable — accessed 2026-08-27
- https://huggingface.co/docs/huggingface_hub/guides/manage-cache — Hub cache layout and persistence properties — accessed 2026-08-27
- https://huggingface.co/docs/huggingface_hub/en/guides/cli — `hf download`, cache directory, token, timeout, verify commands — accessed 2026-08-27
- https://huggingface.co/docs/huggingface_hub/main/package_reference/cli — `hf download` revision/include/exclude/cache options — accessed 2026-08-27
- https://huggingface.co/docs/huggingface_hub/main/concepts/migration — `hf_transfer` removal and ignored legacy environment variable — accessed 2026-08-27
- https://huggingface.co/docs/hub/models-gated — per-user gated access and token requirement — accessed 2026-08-27
- https://docs.vllm.ai/en/latest/cli/serve/ — vLLM `--download-dir` semantics — accessed 2026-08-27
- https://docs.vllm.ai/en/stable/deployment/docker/ — vLLM HF-cache mount and remaining compile-cache behavior — accessed 2026-08-27
- https://github.com/vllm-project/vllm/blob/main/docker/Dockerfile — vLLM entrypoint and non-root HOME behavior — accessed 2026-08-27
- https://docs.sglang.ai/advanced_features/server_arguments.html — SGLang model-path and download-dir semantics — accessed 2026-08-27
- https://github.com/sgl-project/sglang/blob/main/docs/docs/get-started/install.mdx — official SGLang image and cache/token Docker example — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — llama-server `-hf` selection and Docker usage — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/common/hf-cache.cpp — llama.cpp cache environment-variable precedence — accessed 2026-08-27
- https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md — official CUDA server image scope — accessed 2026-08-27

## Open questions

1. Which image digests and runtime UID will Pitwall support?  Verify cache-path ownership on an actual RunPod Pod before hard-coding any in-image default.
2. Does the target RunPod DC offer enough Secure-Cloud capacity for the intended GPU and network-volume attachment?  A cache volume narrows placement.
3. What is the model registry schema for immutable repo commit, selected files/quant, expected bytes, and readiness model ID?
4. What secret backend will vend short-lived HF access tokens, and which tokens have already been individually approved for each gated repo?
5. Should the system warm vLLM's non-HF artifacts separately, or accept per-lease compilation?  vLLM documents that the ordinary HF cache mount alone does not retain them.
