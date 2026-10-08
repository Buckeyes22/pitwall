# Google Gemma 4 — Prompting Reference

| Field | Value |
|---|---|
| Vendor | Google DeepMind |
| Models in scope | `google/gemma-4-31B`, `google/gemma-4-26B-A4B`, `google/gemma-4-12B`, `google/gemma-4-E4B`, and `google/gemma-4-E2B` |
| Primary access | Self-hosted OpenAI-compatible endpoint |
| Official guidance | Gemma 4 model card and launch announcement |
| Canonical doc host | `ai.google.dev` and `blog.google` |
| Compiled | 2026-08-26 |

## 1. Official guidance landscape

Google released Gemma 4 on April 2, 2026 as a family of open-weight models under the Apache 2.0 license. [Google launch announcement](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

The family has five sizes: E2B, E4B, 12B Unified, 26B A4B MoE, and 31B Dense. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

Google documents 128K-token context windows for E2B and E4B and 256K-token windows for the 12B, 26B A4B, and 31B models. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

## 2. Message format and API surface

Gemma 4 uses standard `system`, `assistant`, and `user` roles, and introduces native support for the `system` role. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

Google notes that libraries including Transformers and llama.cpp handle the chat-template details. The model card does not prescribe an OpenAI-compatible server command or a vLLM parser flag; serving through such an endpoint is operational guidance rather than an official API claim. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

## 3. Reasoning control

Thinking is enabled by placing `<|think|>` at the start of the system prompt; remove that token to disable it. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

When thinking is enabled, the model emits internal reasoning followed by its final answer. In multi-turn conversations, retain only the final response in history except for tool-call turns, where the thinking content should be preserved. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

## 4. Sampling defaults

Google recommends `temperature=1.0`, `top_p=0.95`, and `top_k=64` across use cases. [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)

## 5. Routing this family through Pitwall Agent Routing

**Evidence-derived operational guidance:** create an endpoint route for a Gemma 4 model and dispatch it with the route shim:

```bash
pitwall agents profiles add gemma --model google/gemma-4-26B-A4B --base-url http://<host>:8000/v1
route-shim.sh gemma <prompt-file>
```

An endpoint route uses Qwen Code's environment-variable delivery by default; after `pitwall agents profiles sync`, `gemma@opencode` can use opencode's synced provider configuration. The 12B and 26B-A4B variants are the practical single-GPU candidates. Start the prompt file with `<|think|>` only when a reasoning trace is worth the added latency, because the harness owns the system prompt. Use the documented sampling defaults and keep tool schemas lean on E-series deployments.

## 6. Sources

- Gemma 4 model card: https://ai.google.dev/gemma/docs/core/model_card_4
- Google launch announcement: https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/

## Asking through the orchestrator channel

Through `route-shim`, this family asks on tier 1 or 4 depending on the route's harness. Tier 1: through `opencode`, after `pitwall agents setup mcp --harness opencode`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. Tier 4: through `qwen`, the model writes `mailbox/asks/NNNN.json` and exits 75; the run pauses until the orchestrator answers and runs `pitwall agents runs resume`. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
