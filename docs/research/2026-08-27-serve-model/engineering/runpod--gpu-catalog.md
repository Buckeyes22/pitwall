# RunPod pod GPU catalog for LLM serving

**Research date:** 2026-08-27. **Scope:** Pod GPU types, not Serverless. Prices, stock, maximum count, and regions are live-market fields and must be refreshed at launch time.

## Decisive findings (initial)

- Treat the exact canonical strings as `gpuTypeId` inputs. Pitwall deliberately rejects aliases in `src/pitwall/runpod_client/gpu.py:14-42` and documents that pod creation expects RunPod's exact `gpuTypeId` names at `gpu.py:1-6`.
- Do not bake a price, availability, region, or multi-GPU maximum into a serving policy. Pitwall's GraphQL query asks RunPod for the relevant live fields, including the cloud lanes, prices, maximum counts, `lowestPrice`, and datacenters (`src/pitwall/runpod_client/graphql.py:230-286`); the client exposes those GPU types at `graphql.py:391-394` and a per-count/per-datacenter price lookup at `graphql.py:402-449`. Its 5-minute availability cache is deliberately keyed by datacenter, GPU name, cloud type and requested GPU count (`src/pitwall/runpod_client/availability.py:1-8`).
- A serving placement needs more than checkpoint bytes: GPU memory is weights + allocated KV cache + temporary activations/workspaces/engine overhead. The completed headroom and model-fit sections below make this operational.

## How to read the catalog

**Live-market snapshot.** The RunPod columns below were read from the public `gpuTypes` query on 2026-08-27. `S/C` means the returned `secureCloud`/`communityCloud` booleans; `max any/S/C` is `maxGpuCount` / `maxGpuCountSecureCloud` / `maxGpuCountCommunityCloud`; prices are `securePrice` / `communityPrice` dollars per GPU-hour. The snapshot returned `secureSpotPrice`/`communitySpotPrice` equal to the corresponding prices for every canonical row below, so no lower interruptible price is separately listed. This is *not* a price guarantee: RunPod calls Pods a live market across 30+ regions and publishes the pricing page as the current source. [RunPod pricing](https://www.runpod.io/pricing) [RunPod GPU types](https://docs.runpod.io/references/gpu-types)

`gpuTypes` formally exposes `id`, `displayName`, `memoryInGb`, cloud booleans, on-demand/spot prices, three maximum-count fields, `lowestPrice`, and `nodeGroupDatacenters`; `lowestPrice` in turn exposes bid/interruptible price, country and available GPU counts. [RunPod GraphQL schema](https://graphql-spec.runpod.io/)

**Hardware notation.** `CC` is CUDA compute capability. The CUDA capability table identifies Ampere data-center A100 as 8.0, Ampere RTX/A40 as 8.6, Ada as 8.9, Hopper as 9.0, Blackwell data-center as 10.0, and Blackwell RTX as 12.0. [NVIDIA CUDA GPU table](https://developer.nvidia.com/cuda-gpus) “FP8 / FP4” means native Transformer Engine generation support, not that every serving engine/model has a compatible checkpoint: NVIDIA documents FP8 on Ada/Hopper/Blackwell and MXFP8/NVFP4 only on Blackwell. [Transformer Engine](https://docs.nvidia.com/deeplearning/transformer-engine/index.html)

**Fabric warning.** “NVLink-capable” is the GPU/platform capability, not evidence that the particular RunPod pod has an NVLink/NVSwitch topology. `max` is an inventory/control-plane ceiling, not a promise that that count is available in one host. Before creating a TP pod, use `lowestPrice(input: {gpuCount, dataCenterId, secureCloud})` and inspect the returned `availableGpuCounts`; then verify topology inside the pod (`nvidia-smi topo -m`). This is especially important for PCIe-only cards.

### Canonical names validated by Pitwall

| Pitwall canonical name → current `gpuTypes.id` / display name | GB | architecture / CC | FP8 / FP4 | bandwidth | multi-GPU fabric | live RunPod snapshot: max any/S/C; clouds; $/hr S/C; named node-group regions |
|---|---:|---|---|---:|---|---|
| `NVIDIA H100 80GB HBM3` → same / H100 SXM | 80 | Hopper / 9.0 | yes / no | 3.35 TB/s | NVLink 4 (900 GB/s); NVSwitch-capable | 8/8/1; yes/yes; 3.29/2.69; India (AP-IN-1, AP-IN-2), Europe (EUR-IS-3) |
| `NVIDIA H100 NVL` → same / H100 NVL | 94 | Hopper / 9.0 | yes / no | 3.9 TB/s | two-GPU NVLink (600 GB/s); no NVSwitch claim | 10/8/10; yes/yes; 3.19/2.59; none returned |
| `NVIDIA H200` → same / H200 SXM | 141 | Hopper / 9.0 | yes / no | 4.8 TB/s | NVLink 4 / NVSwitch-capable | 8/8/8; yes/yes; 4.59/3.59; Europe (EUR-IS-4, EUR-IS-5, EU-FR-1), US (US-CO-1), Japan (AP-JP-1), Canada (CA-MTL-4) |
| `NVIDIA H200 NVL` → same / H200 NVL | 143 | Hopper / 9.0 | yes / no | 4.8 TB/s | four-way NVLink (up to 1.8 TB/s); no NVSwitch claim | 8/8/0; yes/no; 3.79/—; none returned |
| `NVIDIA B200` → same / B200 | 180 | Blackwell / 10.0 | yes / yes | 8 TB/s | NVLink 5 / NVSwitch-capable | 8/8/0; yes/**schema says yes**; 6.79/5.98; US (US-CA-2, US-NC-2) |
| `NVIDIA A100 80GB` → **not current exact ID**; `NVIDIA A100-SXM4-80GB` / A100 SXM | 80 | Ampere / 8.0 | no / no | 2.039 TB/s | NVLink 3 (600 GB/s), HGX NVSwitch | 8/8/8; yes/yes; 1.59/1.39; US (US-MD-1, US-KS-2, US-MO-1) |
| `NVIDIA A100 80GB PCIe` → same / A100 PCIe | 80 | Ampere / 8.0 | no / no | 1.935 TB/s | NVLink bridge only up to two; no NVSwitch assumption | 8/8/8; yes/yes; 1.39/1.19; none returned |
| `NVIDIA A100 40GB` → **not current exact ID**; `NVIDIA A100-SXM4-40GB` / A100 SXM 40GB | 40 | Ampere / 8.0 | no / no | 1.6 TB/s | NVLink 3 / possible HGX NVSwitch | 2/0/2; no/yes; —/1.00; none returned |
| `NVIDIA A6000` → **not returned**; closest current type is RTX A6000 | 48 | Ampere / 8.6 | no / no | 768 GB/s | NVLink bridge, two cards; no NVSwitch | unverified: no exact live ID |
| `NVIDIA RTX A6000` → same / RTX A6000 | 48 | Ampere / 8.6 | no / no | 768 GB/s | NVLink bridge, two cards; no NVSwitch | 10/10/3; yes/yes; 0.53/0.33; none returned |
| `NVIDIA A40` → same / A40 | 48 | Ampere / 8.6 | no / no | 696 GB/s | NVLink 112.5 GB/s bidirectional; no NVSwitch claim | 10/10/1; yes/no; 0.44/**unavailable lane**; none returned |
| `NVIDIA L40` → same / L40 | 48 | Ada / 8.9 | yes / no | 864 GB/s | PCIe only; no NVLink | 10/8/10; yes/yes; 0.82/0.69; none returned |
| `NVIDIA L40S` → same / L40S | 48 | Ada / 8.9 | yes / no | 864 GB/s | PCIe only; no NVLink | 8/8/8; yes/yes; 0.99/0.79; none returned |
| `NVIDIA L4` → same / L4 | 24 | Ada / 8.9 | yes / no | 300 GB/s | PCIe only; no NVLink | 9/9/1; yes/no; 0.49/**unavailable lane**; none returned |
| `NVIDIA RTX 6000 Ada` → **not current exact ID**; `NVIDIA RTX 6000 Ada Generation` / RTX 6000 Ada | 48 | Ada / 8.9 | yes / no | 960 GB/s | PCIe only; no NVLink | 8/8/8; yes/yes; 0.84/0.74; none returned |
| `NVIDIA RTX 4090` → **not current exact ID**; `NVIDIA GeForce RTX 4090` / RTX 4090 | 24 | Ada / 8.9 | yes / no | 1.008 TB/s | PCIe only; no NVLink | 9/8/9; yes/yes; 0.74/0.34; none returned |
| `NVIDIA GeForce RTX 4090` → same / RTX 4090 | 24 | Ada / 8.9 | yes / no | 1.008 TB/s | PCIe only; no NVLink | 9/8/9; yes/yes; 0.74/0.34; none returned |
| `NVIDIA RTX A5000` → same / RTX A5000 | 24 | Ampere / 8.6 | no / no | 768 GB/s | NVLink bridge, two cards; no NVSwitch | 10/10/10; yes/yes; 0.27/0.16; none returned |
| `NVIDIA RTX A4500` → same / RTX A4500 | 20 | Ampere / 8.6 | no / no | 640 GB/s | NVLink bridge, two cards; no NVSwitch | 4/4/4; yes/yes; 0.25/0.19; none returned |
| `NVIDIA RTX A4000` → same / RTX A4000 | 16 | Ampere / 8.6 | no / no | 448 GB/s | PCIe only; no NVLink | 8/8/8; yes/yes; 0.25/0.17; none returned |
| `NVIDIA RTX 5000 Ada Generation` → same / RTX 5000 Ada | 32 | Ada / 8.9 | yes / no | 576 GB/s | PCIe only; no NVLink | 4/0/4; no/yes; —/0.49; none returned |
| `NVIDIA RTX 4000 Ada Generation` → same / RTX 4000 Ada | 20 | Ada / 8.9 | yes / no | 360 GB/s | PCIe only; no NVLink | 8/8/8; yes/yes; 0.28/0.20; none returned |

Hardware sources for the rows: NVIDIA specifies H100 Hopper, FP8 Transformer Engine and 900-GB/s NVLink; H200's 141-GB/4.8-TB/s SXM product and H200 NVL four-way interconnect; B200's 180 GB/8-TB/s/1.8-TB/s NVLink; and A100's separate SXM/PCIe bandwidth and interconnect specifications. [H100](https://www.nvidia.com/en-us/data-center/h100/) [H200](https://www.nvidia.com/en-au/data-center/h200/) [B200](https://www.runpod.io/articles/guides/nvidia-b200) [A100 80GB comparison](https://www.nvidia.com/en-sg/data-center/a100/) [A100 40GB data sheet](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet.pdf)

For the workstation/visualization rows, NVIDIA's product line card supplies the A6000/Ada memory bandwidths and documents which boards have NVLink, while its A40 data sheet supplies the 48-GB, 696-GB/s, 112.5-GB/s NVLink specifications. NVIDIA's L4 page supplies its 24-GB/300-GB/s/PCIe-only specification. [NVIDIA professional line card](https://www.nvidia.com/content/dam/en-zz/Solutions/gtcs22/design-visualization/quadro-product-literature/rtx-6000-l40-linecard-nvidia-us-2653097-r7-web.pdf) [NVIDIA A40 data sheet](https://images.nvidia.com/content/Solutions/data-center/a40/nvidia-a40-datasheet.pdf) [NVIDIA L4](https://www.nvidia.com/en-us/data-center/l4/)

### Newer RunPod offerings — not yet canonical in Pitwall

| current `gpuTypes.id` / display | GB; architecture / CC | FP8 / FP4; bandwidth; fabric | snapshot max any/S/C; clouds; $/hr S/C; regions |
|---|---|---|---|
| `NVIDIA B300 SXM6 AC` / B300 | 288; Blackwell / 10.0 | yes / yes; HBM3e (exact bandwidth unverified here); NVLink/NVSwitch-capable | 8/8/0; yes/**schema says yes**; 7.89/6.94; US-WA-2 |
| `NVIDIA GeForce RTX 5090` / RTX 5090 | 32; Blackwell RTX / 12.0 | yes / yes; 1.792 TB/s; PCIe only/no NVLink | 10/10/8; yes/yes; 0.99/0.69; none returned |
| `NVIDIA RTX PRO 6000 Blackwell Server Edition` / RTX PRO 6000 | 96; Blackwell RTX / 12.0 | yes / yes; 1.792 TB/s; PCIe, NVLink status unverified | 9/9/9; yes/yes; 2.09/1.69; none returned |
| `NVIDIA RTX PRO 6000 Blackwell Workstation Edition` / RTX PRO 6000 WK | 96; Blackwell RTX / 12.0 | yes / yes; 1.792 TB/s; PCIe, NVLink status unverified | 8/8/4; yes/yes; 1.89/1.69; none returned |
| `NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition` / RTX PRO 6000 MaxQ | 96; Blackwell RTX / 12.0 | yes / yes; exact bandwidth unverified; PCIe | 2/0/2; no/yes; —/1.64; none returned |
| `NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb` / PRO 6000 MIG 24GB | 24 (MIG slice); Blackwell RTX / 12.0 | yes / yes; not a multi-GPU TP choice | 32/0/0; yes/no; 0.59/—; none returned |
| `NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb` / PRO 6000 MIG 48GB | 48 (MIG slice); Blackwell RTX / 12.0 | yes / yes; not a multi-GPU TP choice | 16/0/0; yes/no; 1.09/—; none returned |
| `NVIDIA RTX PRO 4500 Blackwell` / RTX PRO 4500 | 32; Blackwell RTX / 12.0 | yes / yes; exact bandwidth/fabric unverified | 8/8/0; yes/no; 0.72/—; none returned |
| `AMD Instinct MI300X OAM` / MI300X | 192; CDNA 3 / CUDA CC n/a | NVIDIA FP8/FP4 categories n/a; 5.3 TB/s; Infinity Fabric, not NVLink | 8/8/0; yes/no; 2.39/—; none returned |

The current RunPod reference table independently confirms B300, RTX 5090, the RTX PRO 6000/MIG types and MI300X IDs/memory. [RunPod GPU types](https://docs.runpod.io/references/gpu-types) B300 and RTX PRO exact bandwidth or topology not cited above are deliberately `unverified`; do not infer them from a similarly named SKU.

## VRAM headroom for serving

Use a feasibility calculation, not `parameter_count × quantization_bits` alone:

`per-GPU required ≈ (P × weight_bytes / TP) + KV_cache_per_GPU + activations/workspaces + CUDA/PyTorch/CUDA-graph/engine overhead`, and require it to be no greater than the engine's usable memory (for vLLM, VRAM × `gpu_memory_utilization`). vLLM's current default is 0.92 per executor instance, and it can instead take an explicit `kv_cache_memory_bytes`, which overrides that utilization calculation. [vLLM cache configuration](https://docs.vllm.ai/en/latest/api/vllm/config/cache/)

KV is workload-dependent, not a fixed model tax. A usable conservative structural estimate is `batch × (prompt + generated tokens) × hidden_size × layers × 2 × KV_dtype_bytes` (then account for GQA/MQA's actual KV heads); the cited vLLM-Omni guide names weights, KV, activations, CUDA/PyTorch/system and non-Torch/CUDA-graph memory separately, gives that KV formula, and notes inference activation memory commonly varies with batch/sequence/model. [vLLM GPU memory guide](https://docs.vllm.ai/projects/vllm-omni/en/latest/configuration/gpu_memory_utilization/)

For SGLang, its documented `--mem-fraction-static` is the fraction reserved for model weights and KV cache; it tells operators to reduce that fraction on OOM and offers `--max-total-tokens`/`--chunked-prefill-size` controls. Exact capacity must be obtained from its startup memory report for the selected model/engine/version. [SGLang server arguments](https://docs.sglang.io/advanced_features/server_arguments.html) **Operational recommendation:** start an unshared pod around 0.90–0.92 only after a real cold-start profile; reserve additional headroom for long context, concurrent sequences, CUDA graphs, adapters and speculative decoding, then pin KV bytes/context/concurrency from the measured report.

## Model-class fit table

This is an **admission floor for weights**, not an SLA for a useful context length. It uses decimal GB: BF16 ≈ 2 bytes/parameter, FP8 ≈ 1 byte/parameter, INT4 ≈ 0.5 byte/parameter *before* scales/metadata and the headroom above. That is the documented `model_memory ≈ parameters × dtype_size`; thus 8B BF16 is ~16 GB, and the same arithmetic produces the estimates below. [vLLM memory formula](https://docs.vllm.ai/projects/vllm-omni/en/latest/configuration/gpu_memory_utilization/)

| model class | raw BF16 / FP8 / INT4 weights | practical placement after headroom (single GPU or TP) | RunPod-oriented recommendation |
|---|---:|---|---|
| ~8B dense | 16 / 8 / 4 GB | BF16: 20–24 GB class single, but 24 GB leaves limited KV; FP8/INT4: 16–24 GB single | L4/4090/A5000 for FP8/INT4; 48 GB class for comfortable BF16 context |
| ~27–32B dense | 54–64 / 27–32 / 13.5–16 GB | BF16: 80 GB single or TP2×48; FP8: 48 GB single (32 GB may be tight); INT4: 24 GB single | 80-GB A100/H100 for BF16; L40S/RTX 6000 Ada for FP8; 24 GB for INT4 |
| ~70B dense | 140 / 70 / 35 GB | BF16: 141 GB H200 is borderline after KV, prefer TP2×80/96 or B200; FP8: 80 GB single; INT4: 48 GB single | H200/B200 or TP2 H100/A100 for BF16; H100/A100 for FP8; L40S/A6000 class INT4 |
| ~120B **active** MoE | 240 / 120 / 60 GB active weights | BF16: TP2×141/180 or TP4×80; FP8: 141/180 GB single, 96 GB needs TP2; INT4: 80 GB single | B200/H200 for FP8; TP2 H200/B200 for BF16; H100 TP4 only after topology check |
| >300B active | >600 / >300 / >150 GB | BF16: TP4×180 or TP8×80/96; FP8: TP2×180 or B300+headroom / TP4×96; INT4: B200 180 GB may fit weights but leaves small KV, prefer B300 or TP2 | NVLink/NVSwitch H200/B200/B300 systems; do not choose PCIe-only consumer TP for production serving |

For MoE, replace `P` with the checkpoint's **resident total parameters**, not merely “active” parameters, unless the serving implementation really loads only active experts; most standard engines load all weights. Therefore the 120B-active and >300B-active rows are optimistic *only when that active count is demonstrably resident count*. Recalculate using the model config/files and engine profile. Tensor parallelism shards model weights across GPUs but does not abolish KV/overhead; vLLM documents that TP>1 splits the model and reduces per-GPU model memory. [vLLM TP note](https://docs.vllm.ai/projects/vllm-omni/en/latest/configuration/gpu_memory_utilization/)

## Sources

- https://docs.runpod.io/references/gpu-types — current RunPod GPU type IDs, display names, and memory table; accessed 2026-08-27.
- https://www.runpod.io/pricing — Pod pricing page, per-second/hour framing, and current public price cards; accessed 2026-08-27.
- https://graphql-spec.runpod.io/ — `gpuTypes`, `GpuType`, `LowestPrice`, maximum-count, price, cloud and datacenter schema fields; accessed 2026-08-27.
- https://api.runpod.io/graphql — live public `gpuTypes` snapshot queried with the fields stated above; prices/clouds/maxima/returned node groups in the tables; accessed 2026-08-27.
- https://developer.nvidia.com/cuda-gpus — CUDA compute-capability assignments; accessed 2026-08-27.
- https://docs.nvidia.com/deeplearning/transformer-engine/index.html — FP8 on Ada/Hopper/Blackwell and MXFP8/NVFP4 on Blackwell; accessed 2026-08-27.
- https://www.nvidia.com/en-us/data-center/h100/ — H100 Hopper/FP8/NVLink capability; accessed 2026-08-27.
- https://www.nvidia.com/en-au/data-center/h200/ — H200 141-GB HBM3e, 4.8-TB/s and NVLink context; accessed 2026-08-27.
- https://developer.nvidia.com/blog/deploying-nvidia-h200-nvl-at-scale-with-new-enterprise-reference-architecture/ — H200 NVL four-way/1.8-TB/s NVLink; accessed 2026-08-27.
- https://www.runpod.io/articles/guides/nvidia-b200 — B200 VRAM, bandwidth, NVLink and current RunPod context; accessed 2026-08-27.
- https://www.nvidia.com/en-sg/data-center/a100/ — A100 80-GB PCIe/SXM memory bandwidth and NVLink configurations; accessed 2026-08-27.
- https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet.pdf — A100 40-GB memory/bandwidth/NVLink; accessed 2026-08-27.
- https://www.nvidia.com/content/dam/en-zz/Solutions/gtcs22/design-visualization/quadro-product-literature/rtx-6000-l40-linecard-nvidia-us-2653097-r7-web.pdf — A6000 and RTX Ada memory bandwidth and NVLink capability; accessed 2026-08-27.
- https://images.nvidia.com/content/Solutions/data-center/a40/nvidia-a40-datasheet.pdf — A40 architecture/memory/bandwidth/NVLink; accessed 2026-08-27.
- https://www.nvidia.com/en-us/data-center/l4/ — L4 architecture/memory/bandwidth/PCIe; accessed 2026-08-27.
- https://docs.vllm.ai/en/latest/api/vllm/config/cache/ — vLLM `gpu_memory_utilization`, default, and explicit KV-cache bytes behavior; accessed 2026-08-27.
- https://docs.vllm.ai/projects/vllm-omni/en/latest/configuration/gpu_memory_utilization/ — memory components, model/KV arithmetic, TP and activation considerations; accessed 2026-08-27.
- https://docs.sglang.io/advanced_features/server_arguments.html — SGLang memory-fraction and capacity control arguments; accessed 2026-08-27.

## Open questions

- RunPod's `secureCloud` boolean has inconsistent-looking combinations in the live snapshot (for example B200 reports `communityCloud: true` while `maxGpuCountCommunityCloud: 0`; A40/L4 report a nonzero Community price while `communityCloud: false`). Treat the cloud booleans and count-specific `lowestPrice` result as authoritative at launch, not the price scalar alone.
- `NVIDIA A100 80GB`, `NVIDIA A100 40GB`, `NVIDIA A6000`, `NVIDIA RTX 6000 Ada`, and `NVIDIA RTX 4090` are accepted by Pitwall's current canonical validator but are not all exact IDs in current RunPod `gpuTypes`. The broker should either update its canonical set or add an explicit, tested current-ID migration map before it creates pods.
- `nodeGroupDatacenters` was empty for many public-market GPU types. It is not proof of no region availability. Query `myself.datacenters.gpuAvailability` and count-specific `lowestPrice` immediately before a lease; Pitwall already has both query surfaces (`src/pitwall/runpod_client/graphql.py:289-310`, `402-449`).
- The exact B300 and RTX PRO 6000 topology/bandwidth, and actual topology of a selected multi-GPU Pod, require hardware/host confirmation. Do not mark an NVLink requirement satisfied until in-pod topology inspection.
