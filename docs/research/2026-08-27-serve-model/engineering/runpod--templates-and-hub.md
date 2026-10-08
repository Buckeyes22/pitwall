# RunPod templates and Hub: inputs to `serve-model`

Research date: 2026-08-27. This is deliberately conservative: a template's actual image, command, environment, ports, update date, and ID are mutable console data. RunPod documents authenticated discovery (`runpodctl template list/search/get`), but no usable API credential was available for this read-only research unit. Values not exposed by a cited public page are **unverified**, not guessed.

## Decision

Use **(c) both**, with a Pitwall-owned generated Pod template as the launch artifact. Select a vetted upstream engine image/digest as the input; create/cache a private `isServerless: false` template containing Pitwall's explicit entrypoint/start command, port 8000, `/workspace` mount, and the model-specific environment contract. Treat an official template only as a discovery/default-image aid, never as a durable production dependency.

This keeps the operationally important contract in versioned Pitwall configuration, pins image identity, permits the exact model command and probe expected by the proxy, and still benefits from RunPod's published images/templates. Referencing an official ID directly is lower setup work, but its image/tag, command, environment, README, and availability can change without a Pitwall deploy; a Hub/community listing has the same drift risk plus a different maintainer. A private image additionally needs a RunPod registry-auth ID; public upstream images do not.

## What RunPod provides

RunPod calls a template a reusable Pod/Serverless configuration. A Pod template supports storage and is passed to `runpodctl pod create`; a Serverless template does not support volume disks and is for endpoints. The console deploy flow can choose a template and optionally attach a network volume. Stopped Pod data survives only in `/workspace`'s volume disk; the container disk is cleared. A network volume preserves `/workspace` even after termination. [Template CLI reference](https://docs.runpod.io/runpodctl/reference/runpodctl-template), [Manage Pods](https://docs.runpod.io/pods/manage-pods).

### Official / one-click LLM candidates

| Candidate requested | Public evidence of RunPod offering | Image / start contract / ports / env / mount / updated / ID or URL |
|---|---|---|
| vLLM | RunPod documents vLLM as a configurable LLM deployment, and says the wizard accepts HF model name/token/CUDA and vLLM parameters; a RunPod article also calls vLLM a pre-built template. This is sufficient evidence of a RunPod-provided offering, but it is primarily Endpoint documentation, not a current Pod listing. | Exact Pod listing, image/tag, entrypoint, ports, env, update date, and ID: **unverified**. Discover: `runpodctl template search vllm --type official`, then `runpodctl template get ID`. Do not assume `MODEL_NAME`, `HF_TOKEN`, or `MAX_MODEL_LEN`: inspect the returned env. [Configurable vLLM deployment](https://www.runpod.io/blog/configurable-endpoints-large-language-models), [CLI template reference](https://docs.runpod.io/runpodctl/reference/runpodctl-template). |
| SGLang | RunPod has a current SGLang production guide, evidence that RunPod supports the engine; this is not proof of a named official Pod template. | Current official template listing and all requested fields: **unverified**. Discover as above with `sglang`. Prefer the upstream engine image and generated template until an authenticated `template get` supplies the contract. [SGLang guide](https://www.runpod.io/articles/guides/blog-sglang-production-llm-pipelines). |
| llama.cpp | RunPod's guide explains deploying llama.cpp on a cloud GPU and GGUF model use; it does not identify a current official Pod template. | Listing/image/tag/command/ports/env/mount/update/ID: **unverified**. Search `llama.cpp` and `llamacpp`, inspect exact result. [llama.cpp guide](https://www.runpod.io/articles/guides/deploy-llama-cpp-cloud-gpu-hosting-headaches). |
| Ollama | RunPod's current Pod tutorial explicitly says choose the latest PyTorch template, then add HTTP 11434 and `OLLAMA_HOST=0.0.0.0`, install Ollama, and run `ollama serve`. This is a supported Pod recipe, not evidence of a fixed official Ollama template ID. | Base image is the current **latest PyTorch template** (exact tag and ID mutable; PyTorch example below). Required HTTP port `11434`; documented env `OLLAMA_HOST=0.0.0.0`; command `ollama serve`; model launch `ollama run llama2`; mount convention `/workspace`. Tutorial last modified 2026-07-20. An alleged `runpod/ollama` official image/template is **unverified** by primary RunPod docs. [Ollama Pod tutorial](https://docs.runpod.io/tutorials/pods/run-ollama). |
| text-generation-webui / Oobabooga | RunPod calls **`valyriantech/text-generation-webui-oneclick-UI-and-API`** a community template, and separately refers to an older RunPod Text Generation UI / `Runpod/oobabooga:1.0.1`. Therefore it is not safe to call the former official. | `valyriantech/text-generation-webui-oneclick-UI-and-API`: community; observed UI port 7860 (also port 5000 may respond before UI); exact image/tag, command, full env, mount, update, and template ID **unverified**. Older documented variables: `WEBUI` and `LOAD_MODEL`; old article says default model download consumes volume. [2026 community article](https://www.runpod.io/blog/no-code-ai-run-llm), [older official blog](https://blog.runpod.io/setting_up_oobabooga_chatbot/). |
| PyTorch base | This is the dependable official foundation. All official PyTorch templates have JupyterLab preconfigured. Docs demonstrate `runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04`, port `8888/http`, and `JUPYTER_PASSWORD`; current CLI examples show `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04`. | Example ID `runpod-torch-v21` (not a promise that it remains the latest). HTTP 8888 for JupyterLab; official-template SSH/22 exposure and exact entrypoint are **unverified** from docs. `/workspace` is the default volume mount. [Connect to Pod](https://docs.runpod.io/pods/connect-to-a-pod), [CLI overview](https://docs.runpod.io/runpodctl/overview), [Pod CLI reference](https://docs.runpod.io/runpodctl/reference/runpodctl-pod). |
| “one-click” LLM templates | RunPod advertises preconfigured Quickstart templates and its own articles name OpenChat, KoboldCPP, and Oobabooga as examples; public docs distinguish official from community. | The precise set and every requested runtime field are **unverified** until authenticated listing. Search all three template types, record `isRunpod`, `isServerless`, and `runtimeInMin`/listing timestamp before adopting. [One-click overview](https://www.runpod.io/articles/guides/open-source-ai-no-code), [CLI type filters](https://docs.runpod.io/runpodctl/reference/runpodctl-template). |

The docs' own Pod examples use `/workspace` for persistent volume data. A template can specify `volumeInGb` and `volumeMountPath`; on later Pod creation, an attached `networkVolumeId` is the appropriate persistent cross-Pod storage mechanism. That is where a shared HF cache should be mounted; Pitwall must set engine cache paths deliberately rather than rely on an image default. [Manage Pods](https://docs.runpod.io/pods/manage-pods), [Templates REST reference](https://docs.runpod.io/api-reference/templates/POST/templates).

### Hub, templates, and Pods

The current CLI has three template classes: `official`, `community`, and `user`; `template list --type official|community|user`, `template search QUERY`, and `template get ID` are the supported inspection surface. `get` returns README, environment variables, and exposed ports. Pods may be created from the resulting template ID (`runpodctl pod create --template-id ID`) or directly from an image. RunPod's Pod documentation explicitly says it deploys preconfigured Pods from the Hub; therefore Hub/template IDs can be Pod deployments when the template's `isServerless` is false. They are not inherently Serverless-only. [Template CLI reference](https://docs.runpod.io/runpodctl/reference/runpodctl-template), [Pod CLI reference](https://docs.runpod.io/runpodctl/reference/runpodctl-pod), [Manage Pods](https://docs.runpod.io/pods/manage-pods).

Important terminology caveat: RunPod's separate Hub/Serverless workflow also supports `runpodctl hub search` and `runpodctl serverless create --hub-id ...`; do not substitute `hub-id` for the Pod API's `templateId`. For `serve-model`, inspect a listing and require `isServerless == false`, then use its template ID with `POST /v1/pods`. [RunPod official CLI skill examples](https://github.com/runpod/runpod-plugins-official/blob/main/plugins/runpod/skills/runpodctl/SKILL.md).

## Templates API and Pitwall status

RunPod REST exposes `POST /v1/templates` and the corresponding list/get/update/delete operations under `/v1/templates`. The create schema includes `imageName`, `name`, `containerDiskInGb`, `containerRegistryAuthId`, `dockerEntrypoint` (array; empty preserves image ENTRYPOINT), `dockerStartCmd` (array; empty preserves CMD), `env`, `isPublic`, `isServerless`, `ports` (array of `PORT/http|tcp`), `readme`, `volumeInGb`, and `volumeMountPath`. `isRunpod` on a returned object marks an official RunPod-maintained template. [Create template REST reference](https://docs.runpod.io/api-reference/templates/POST/templates).

GraphQL is also viable (Pitwall uses it): save/update/delete and queries can represent `dockerArgs` in the legacy template schema. The REST fields above are preferable for engine commands because entrypoint and command remain structured arrays; the create-template reference says the arrays override the image's ENTRYPOINT/CMD only when supplied.

`POST /v1/pods` accepts `templateId` rather than individual template configuration, while GPU selection/count remains in the request. The documented raw-image example uses `imageName`, `gpuTypeIds`, `gpuCount`, `containerDiskInGb`, and `volumeInGb`; Pitwall additionally uses documented/REST-request fields for cloud, compute, networking, commands, and registry auth. [Manage Pods](https://docs.runpod.io/pods/manage-pods).

### Already wired in Pitwall

| Capability | Evidence | Gap for `serve-model` |
|---|---|---|
| Private generated Pod template, cache/reuse by image tag/digest-derived suffix | `ensure_template` creates `is_serverless=False`, private template, `/workspace`, configurable container disk and registry auth, and caches the returned ID in DB: [`templates.py:635`](../../../../src/pitwall/runpod_client/templates.py#L635). | It supplies an empty legacy `dockerArgs`, empty ports/readme, and only Pitwall worker env; no engine-specific `dockerEntrypoint`/`dockerStartCmd`, port 8000, model env, or template fingerprint beyond image ref is persisted: [`templates.py:300`](../../../../src/pitwall/runpod_client/templates.py#L300). |
| Read/update/delete private templates via GraphQL | Full selection has image, `dockerArgs`, disk/volume/mount, ports, env, visibility, README; update and delete exist: [`templates.py:281`](../../../../src/pitwall/runpod_client/templates.py#L281), [`templates.py:436`](../../../../src/pitwall/runpod_client/templates.py#L436), [`templates.py:514`](../../../../src/pitwall/runpod_client/templates.py#L514). | Model the REST structured command fields, port-label distinction, and non-empty README/contract in the `Template` model; use a stable config hash, not only image tag, for template reuse. |
| Hub listing/get adapter | `list_hub_templates` queries `podTemplates`; `get_hub_template` queries `podTemplate(id:)`: [`templates.py:541`](../../../../src/pitwall/runpod_client/templates.py#L541), [`templates.py:572`](../../../../src/pitwall/runpod_client/templates.py#L572). | It presently cannot distinguish official/community/user or retain `isRunpod`; code comments state Hub GraphQL fields were reduced. It also has no authenticated snapshot/approval of a specific official template contract. |
| Pod REST launch and per-pod override | Payload sends image, GPU/cloud selection, disk, env, `/workspace`, template ID, network volume, ports, registry auth, `dockerEntrypoint`, and `dockerStartCmd`: [`pods.py:661`](../../../../src/pitwall/runpod_client/pods.py#L661). | This is the right launch seam. `serve-model` must pass structured engine commands, 8000 HTTP, its model/Hub token/cache env, and a network-volume ID when cache reuse is intended; verify whether REST overrides merge with or replace template env/ports in a dry-run/RunPod-supported test. |

## Concrete default

1. Engine registry selects a digest-pinned public upstream image (`vllm/vllm-openai`, `lmsysorg/sglang`, or `ghcr.io/ggml-org/llama.cpp:server-cuda*`) and records the exact tested image and command.
2. `serve-model` generates a private Pod template keyed by **engine configuration hash**: image digest, command/entrypoint, HTTP port 8000, `/workspace` mount, disk size, and non-secret env key names. Put secrets (`HF_TOKEN`) only in Pod request env, not the reusable template or README.
3. Attach a co-located network volume at `/workspace`; set the engine's model/cache destination under it. Use container disk only for image/runtime scratch.
4. Offer official template ID as an opt-in discovery/bootstrapping input. Before use, run `runpodctl template get ID`, save its returned image/command/env/ports/`isRunpod`/`isServerless` as a reviewed snapshot, then reproduce that in the generated private template. Reject serverless-only templates.

This produces predictable health and `/v1/models` behavior while leaving RunPod's official PyTorch/template inventory available for development. It avoids depending on GUI-only one-click templates whose API surface is not guaranteed OpenAI-compatible (notably Ollama and text-generation-webui).

## Sources

- https://docs.runpod.io/runpodctl/reference/runpodctl-template — template discovery types, get/create/update flags, storage distinction — accessed 2026-08-27.
- https://docs.runpod.io/runpodctl/reference/runpodctl-pod — Pod `--template-id` and raw-image creation — accessed 2026-08-27.
- https://docs.runpod.io/pods/manage-pods — console/CLI/REST Pod creation, templateId, and `/workspace` persistence semantics — accessed 2026-08-27.
- https://docs.runpod.io/api-reference/templates/POST/templates — REST template schema, structured Docker overrides, official flag, port/mount semantics — accessed 2026-08-27.
- https://docs.runpod.io/pods/connect-to-a-pod — official PyTorch/Jupyter and 8888 example — accessed 2026-08-27.
- https://docs.runpod.io/runpodctl/overview — `runpod-torch-v21` example — accessed 2026-08-27.
- https://docs.runpod.io/tutorials/pods/run-ollama — current Ollama-on-Pod recipe, 11434 and `OLLAMA_HOST` — accessed 2026-08-27.
- https://www.runpod.io/blog/configurable-endpoints-large-language-models — vLLM configurable deployment evidence — accessed 2026-08-27.
- https://www.runpod.io/articles/guides/blog-sglang-production-llm-pipelines — SGLang support evidence — accessed 2026-08-27.
- https://www.runpod.io/articles/guides/deploy-llama-cpp-cloud-gpu-hosting-headaches — llama.cpp/GGUF Pod guide — accessed 2026-08-27.
- https://www.runpod.io/blog/no-code-ai-run-llm — community text-generation-webui listing name and observed ports — accessed 2026-08-27.
- https://blog.runpod.io/setting_up_oobabooga_chatbot/ — historical Oobabooga env contract and port — accessed 2026-08-27.
- https://github.com/runpod/runpod-plugins-official/blob/main/plugins/runpod/skills/runpodctl/SKILL.md — Hub versus template CLI surface — accessed 2026-08-27.

## Open questions

- With a non-production RunPod API key, run `runpodctl template list --type official --limit 100` and targeted searches, then `template get` every candidate. Capture ID, `isRunpod`, `isServerless`, image digest/tag, entrypoint/CMD, env, ports/labels, mount, `runtimeInMin`/last update, and console URL; replace all unverified cells.
- Confirm current REST `POST /pods` precedence when `templateId` and `imageName`, env, ports, and structured Docker overrides are all present. Pitwall currently sends them together.
- Verify `podTemplates`/`podTemplate` GraphQL authentication and whether the listing omits class/maintenance metadata; code's July-2026 schema-change assertion is repository-local and was not independently verified.
- Determine whether an official current Pod vLLM/SGLang/llama.cpp/Ollama template exists versus only an official doc/Serverless/Hub deployment. Do not promote a community item to official based on its search name.

## Authenticated snapshot (read-only key, `runpodctl 2.9.0`, 2026-08-27)

Replaces the "unverified" cells above. `runpodctl template list --type official` returned **14 official
(`isRunpod: true`) pod templates**, none of them an inference engine: `runpod-torch-v21`/`v220`/`v240`/`v280`
(`runpod/pytorch:*`, 20–30 GB container disk, `/workspace`), `9yo900pjtt`/`a9dk3g7cny` (PyTorch cluster images),
`cw3nka7d08`/`2lv7ev3wfp` (ComfyUI CUDA 12.8/13), `runpod-ubuntu*`, `runpod-torch-v240-rocm61`, and two
filebrowser templates. **There is no official vLLM, SGLang, llama.cpp, or Ollama pod template.**

`runpodctl template search {vllm,sglang,llama,ollama}` returned only community templates, all on moving tags:

| id | name | image | ports | env | disk / volume / mount |
|---|---|---|---|---|---|
| `pvcdqlwm9r` | vLLM Latest | `vllm/vllm-openai:latest` | `8000/http` | `HF_HOME=/workspace/.huggingface`, `VLLM_API_KEY=sk-$RUNPOD_POD_ID` | 5 / 25 / `/workspace` |
| `sybrm5hsk7` | Qwen3 32B FP8 – vLLM by Trelis | `vllm/vllm-openai:latest` | `8000/http` | `HF_HUB_ENABLE_HF_TRANSFER=1` | 10 / 50 / `/root/.cache/huggingface` |
| `8oc6sh1sth` | Qwen3 32B FP8 – SGLang by Trelis | `lmsysorg/sglang:latest` | `8000/http` | `HF_HUB_ENABLE_HF_TRANSFER=1` | 10 / 250 / `/root/.cache/huggingface` |
| `e2wsrsjbjq` | Ollama NVIDIA CUDA | `ollama/ollama:latest` | `11434/http`, `22/tcp` | `OLLAMA_HOST=0.0.0.0:11434`, `OLLAMA_MODELS=/workspace/models`, `OLLAMA_FLASH_ATTENTION=1` | 15 / 120 / `/workspace` |

All four have `dockerEntrypoint: null` and `dockerStartCmd: null` — the operator supplies the start command at
deploy time. Observed conventions Pitwall should reproduce in its generated template: the HTTP port lives in the
template `ports` list; the HF cache is redirected onto the volume (`HF_HOME` under `/workspace`, or the volume
mounted at `/root/.cache/huggingface`); RunPod expands `$RUNPOD_POD_ID` inside template env values.
Decision-relevant conclusion: option (b) "reference official ids" is not available; (c) collapses to (a) with
community templates as pattern references only.
