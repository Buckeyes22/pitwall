# Engine smoke — declared-image certification (2026-08-28)

Run: `make engine-smoke-declared` at commit `6478461`, 2026-08-28T12:30:20Z → end 2026-08-28T12:48:53Z, exit 0. Every catalogue variant was parser-checked on its declared image (no substitution); every distinct image rejected the injected `--pitwall-bogus-flag-7` negative control; the llama.cpp CPU HTTP cycle passed. Report copied verbatim from `artifacts/engine-smoke/report.md`:

# Engine launch-shape smoke

## Parse probes

| Kind | Model | Variant | Engine | Declared image | Used image | Classification | Note | Detail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| catalogue | MiniMaxAI/MiniMax-H3 | bf16 | vllm | vllm/vllm-omni:minimax-h3 | vllm/vllm-omni:minimax-h3 | OK |  | arguments accepted |
| catalogue | Qwen/Qwen3.8-27B | fp8 | vllm | vllm/vllm-openai:latest | vllm/vllm-openai:latest | OK |  | arguments accepted |
| catalogue | Qwen/Qwen3.8-27B | gguf:UD-Q4_K_XL | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda13 | ghcr.io/ggml-org/llama.cpp:server-cuda13 | OK |  | expected model-load failure after parsing |
| catalogue | Qwen/Qwen3.8-Flash-Next | fp8 | vllm | vllm/vllm-openai:qwen38-flash-next | vllm/vllm-openai:qwen38-flash-next | OK |  | arguments accepted |
| catalogue | deepseek-ai/DeepSeek-V4-Flash-0731 | fp4+fp8 | vllm | vllm/vllm-openai:v0.25.0 | vllm/vllm-openai:v0.25.0 | OK |  | arguments accepted |
| catalogue | deepseek-ai/DeepSeek-V4-Pro-0813 | fp8 | vllm | vllm/vllm-openai:v0.25.0 | vllm/vllm-openai:v0.25.0 | OK |  | arguments accepted |
| catalogue | google/gemma-4-31B-it | bf16 | vllm | vllm/vllm-openai:gemma4 | vllm/vllm-openai:gemma4 | OK |  | arguments accepted |
| catalogue | google/gemma-4-31B-it | gguf:UD-Q5_K_XL | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | ghcr.io/ggml-org/llama.cpp:server-cuda | OK |  | expected model-load failure after parsing |
| catalogue | meta-models/Muse-Glimmer-30B | bf16 | vllm | vllm/vllm-openai:muse-glimmer | vllm/vllm-openai:muse-glimmer | OK |  | arguments accepted |
| catalogue | meta-models/Muse-Glimmer-30B | gguf:UD-Q5_K_XL | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | ghcr.io/ggml-org/llama.cpp:server-cuda | OK |  | expected model-load failure after parsing |
| catalogue | moonshotai/Kimi-K3 | fp8 | vllm | vllm/vllm-openai:kimi-k3 | vllm/vllm-openai:kimi-k3 | OK |  | arguments accepted |
| catalogue | ornith-ai/Ornith-1.5-35B-A3B-GGUF | gguf:Q4_K_M | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | ghcr.io/ggml-org/llama.cpp:server-cuda | OK |  | expected model-load failure after parsing |
| catalogue | zai-org/GLM-5.2 | fp8 | vllm | vllm/vllm-openai:glm52 | vllm/vllm-openai:glm52 | OK |  | arguments accepted |
| catalogue | zai-org/GLM-5.2 | nvfp4 | vllm | vllm/vllm-openai:glm52 | vllm/vllm-openai:glm52 | OK |  | arguments accepted |
| catalogue | zai-org/GLM-5.3-Flash | fp8 | vllm | vllm/vllm-openai:glm53-flash | vllm/vllm-openai:glm53-flash | OK |  | arguments accepted |
| catalogue | smoke/sglang-parser | synthetic-parser-probe | sglang | lmsysorg/sglang:v0.5.18-runtime | lmsysorg/sglang:v0.5.18-runtime | OK |  | arguments accepted |
| negative control | negative-control/vllm-omni | pitwall-bogus-flag-7 | vllm | vllm/vllm-omni:minimax-h3 | vllm/vllm-omni:minimax-h3 | SHAPE_DEFECT | must reject injected bogus flag | vllm-omni: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:latest | vllm/vllm-openai:latest | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/llama.cpp | pitwall-bogus-flag-7 | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda13 | ghcr.io/ggml-org/llama.cpp:server-cuda13 | SHAPE_DEFECT | must reject injected bogus flag | error: invalid argument: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:qwen38-flash-next | vllm/vllm-openai:qwen38-flash-next | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:v0.25.0 | vllm/vllm-openai:v0.25.0 | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:gemma4 | vllm/vllm-openai:gemma4 | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/llama.cpp | pitwall-bogus-flag-7 | llama.cpp | ghcr.io/ggml-org/llama.cpp:server-cuda | ghcr.io/ggml-org/llama.cpp:server-cuda | SHAPE_DEFECT | must reject injected bogus flag | error: invalid argument: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:muse-glimmer | vllm/vllm-openai:muse-glimmer | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:kimi-k3 | vllm/vllm-openai:kimi-k3 | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:glm52 | vllm/vllm-openai:glm52 | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/vllm | pitwall-bogus-flag-7 | vllm | vllm/vllm-openai:glm53-flash | vllm/vllm-openai:glm53-flash | SHAPE_DEFECT | must reject injected bogus flag | vllm: error: unrecognized arguments: --pitwall-bogus-flag-7 |
| negative control | negative-control/sglang | pitwall-bogus-flag-7 | sglang | lmsysorg/sglang:v0.5.18-runtime | lmsysorg/sglang:v0.5.18-runtime | SHAPE_DEFECT | must reject injected bogus flag | sglang.launch_server: error: unrecognized arguments: --pitwall-bogus-flag-7 |

0 rows parser-checked on a substitute image; declared image not executed.

## llama.cpp CPU full cycle

GGUF: `afrideva/TinyMistral-248M-SFT-v4-GGUF/tinymistral-248m-sft-v4.q2_k.gguf` (116198464 bytes).

CPU image: `ghcr.io/ggml-org/llama.cpp@sha256:9f84380be42d6285a827629c809387349c3541aa8986f7536547ca33cc8dd47a` (309682934 bytes).

`/health`, `/v1/models` alias, and `/v1/chat/completions` passed.
