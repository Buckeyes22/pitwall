# Meta Muse Glimmer — Prompting Reference

| Field | Value |
|---|---|
| Vendor | Meta Superintelligence Labs |
| Models in scope | `meta-models/Muse-Glimmer-30B` and `meta-models/Muse-Glimmer-30B-GGUF` |
| Primary access | Self-hosted OpenAI-compatible endpoint |
| Official guidance | Muse Glimmer prompting and vLLM documentation; Meta model cards |
| Canonical doc host | `dev.meta.ai` and Meta's `meta-models` Hugging Face organization |
| Compiled | 2026-08-26 |

## 1. Official guidance landscape

Meta Superintelligence Labs released Muse Glimmer on August 10, 2026 as a 30B open-weight agentic model under Apache 2.0. [Meta's release announcement](https://research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model) [Meta model card](https://huggingface.co/meta-models/Muse-Glimmer-30B)

The base repository is `meta-models/Muse-Glimmer-30B`; Meta also publishes `meta-models/Muse-Glimmer-30B-GGUF` for llama.cpp-compatible quantized deployment. [Base model card](https://huggingface.co/meta-models/Muse-Glimmer-30B) [GGUF model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF)

The `meta-models/Muse-Glimmer-30B-assistant` repository is a DFlash speculative-decoding drafter, not a standalone chat model. [Assistant model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant)

The base model card documents a 131,072+ token context window and approximately 29.6B total parameters, including an approximately 1.8B-parameter perception encoder. [Meta model card](https://huggingface.co/meta-models/Muse-Glimmer-30B)

## 2. Message format and API surface

Meta documents serving the base model through `vllm/vllm-openai:muse-glimmer` with `--enable-auto-tool-choice --tool-call-parser muse_glimmer --reasoning-parser muse_glimmer`; its client uses the OpenAI-compatible `http://localhost:8000/v1` base URL. [Meta vLLM documentation](https://dev.meta.ai/docs/muse-glimmer/vllm/)

The model card likewise demonstrates an OpenAI-compatible chat-completions request against a local `/v1` endpoint. [Meta model card](https://huggingface.co/meta-models/Muse-Glimmer-30B)

## 3. Reasoning control

Meta documents `Reasoning strength: <value>` as a system-prompt line, with `low`, `medium`, `high`, and `xhigh` as supported values and `high` as the default. [Meta prompting documentation](https://dev.meta.ai/docs/muse-glimmer/prompting/)

The documented chat-template control is `reasoning_strength="medium"`; replace `medium` with the intended supported value when constructing the template. [Meta prompting documentation](https://dev.meta.ai/docs/muse-glimmer/prompting/)

## 4. Sampling defaults

Meta recommends `temperature=1.0`, `top_p=0.95`, and `top_k=64`. [Meta model card](https://huggingface.co/meta-models/Muse-Glimmer-30B)

## 5. Routing this family through Pitwall Agent Routing

**Evidence-derived operational guidance:** Meta does not list Muse Glimmer in its Model API overview, so treat it as a self-hosted OpenAI-compatible endpoint rather than a Meta-hosted model. Create an endpoint route such as:

```bash
pitwall agents profiles add glimmer --model meta-models/Muse-Glimmer-30B --base-url http://<host>:8000/v1 --api-key-env GLIMMER_API_KEY
route-shim.sh glimmer <prompt-file>
```

An endpoint route uses Qwen Code's environment-variable delivery by default; after `pitwall agents profiles sync`, `glimmer@opencode` can use opencode's synced provider configuration. Put `Reasoning strength: low|medium|high|xhigh` at the top of the prompt file when the desired level differs from the harness-owned default system prompt.

## 6. Sources

- Meta release announcement: https://research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model
- Base model card: https://huggingface.co/meta-models/Muse-Glimmer-30B
- GGUF model card: https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF
- DFlash assistant model card: https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant
- Meta prompting documentation: https://dev.meta.ai/docs/muse-glimmer/prompting/
- Meta vLLM documentation: https://dev.meta.ai/docs/muse-glimmer/vllm/
- Meta Model API overview: https://dev.meta.ai/docs/overview/
- Meta Muse Glimmer model page: https://developer.meta.com/ai/models/muse-glimmer/
- Meta Hugging Face organization guidance: https://developer.meta.com/ai/docs/getting-the-models/hugging-face/

## Asking through the orchestrator channel

Through `route-shim`, this family asks on tier 1 or 4 depending on the route's harness. Tier 1: through `opencode`, after `pitwall agents setup mcp --harness opencode`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. Tier 4: through `qwen`, the model writes `mailbox/asks/NNNN.json` and exits 75; the run pauses until the orchestrator answers and runs `pitwall agents runs resume`. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
