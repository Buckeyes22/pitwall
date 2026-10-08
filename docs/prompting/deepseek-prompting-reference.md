# DeepSeek V4 — Prompting Reference

| Field | Value |
|---|---|
| Vendor | DeepSeek |
| Models in scope | `deepseek-ai/DeepSeek-V4-Pro-0813` and `deepseek-ai/DeepSeek-V4-Flash-0731` |
| Primary access | Self-hosted OpenAI-compatible endpoint |
| Official guidance | DeepSeek V4 model cards and local-serving instructions |
| Canonical doc host | DeepSeek's `deepseek-ai` Hugging Face organization |
| Compiled | 2026-08-26 |

## 1. Official guidance landscape

DeepSeek publishes DeepSeek-V4-Pro-0813 and DeepSeek-V4-Flash-0731 under the MIT License. [Pro model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) [Flash model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)

The DeepSeek V4 model card describes Pro as 1.6T total parameters with 49B activated and Flash as 284B total parameters with 13B activated; both have a one-million-token context length. [DeepSeek V4 model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)

## 2. Message format and API surface

DeepSeek provides encoding examples that transform OpenAI-compatible messages into the model input format and parse the completion text. [Flash model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)

The Pro and Flash cards show vLLM and SGLang servers serving OpenAI-compatible `/v1/chat/completions` endpoints. [Pro model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813) [Flash model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)

For vLLM, the official Flash example uses `--trust-remote-code --kv-cache-dtype fp8 --block-size 256 --data-parallel-size 4 --enable-expert-parallel --moe-backend deep_gemm_mega_moe`; DSpark is enabled with `--speculative-config` using `"method":"dspark"`. [Flash model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)

## 3. Reasoning control

DeepSeek documents the `reasoning_effort` request parameter with `low`, `high`, and `max` values. [Pro model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

Its encoding example enables thinking with `thinking_mode="thinking"` alongside `reasoning_effort="max"`. [Flash model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731)

## 4. Sampling defaults

For local deployment, DeepSeek recommends `temperature=1.0` and `top_p=0.95` for agentic scenarios, or `top_p=1.0` otherwise. [Pro model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813)

## 5. Routing this family through Pitwall Agent Routing

**Evidence-derived operational guidance:** create an endpoint route for the Flash model and dispatch it through the route shim:

```bash
pitwall agents profiles add deepseek --model deepseek-ai/DeepSeek-V4-Flash-0731 --base-url http://<host>:8000/v1 --api-key-env DEEPSEEK_API_KEY
route-shim.sh deepseek <prompt-file>
```

An endpoint route uses Qwen Code's environment-variable delivery by default; after `pitwall agents profiles sync`, `deepseek@opencode` can use opencode's synced provider configuration. Flash is the realistic single-box self-host target; Pro needs a multi-node deployment. Configure `reasoning_effort` and `thinking_mode` in the selected harness, and keep the prompt focused on the work and output contract.

## 6. Sources

- DeepSeek V4 Pro 0813 model card: https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813
- DeepSeek V4.1 Flash model card: https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- DeepSeek V4 Flash 0731 model card: https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731
- DeepSeek V4 Pro model card: https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro

## Asking through the orchestrator channel

Through `route-shim`, this family asks on tier 1 or 4 depending on the route's harness. Tier 1: through `opencode`, after `pitwall agents setup mcp --harness opencode`, the dispatcher gives the model the `ask_orchestrator` tool and it waits for the answer. Tier 4: through `qwen`, the model writes `mailbox/asks/NNNN.json` and exits 75; the run pauses until the orchestrator answers and runs `pitwall agents runs resume`. In the task prompt, name the decisions that are dangerous or irreversible enough to ask about; everything else proceeds on the model's own judgment.
