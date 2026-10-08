# Provider registry

`src/pitwall/agents/resources/config/harness-registry.json` is the machine-readable source of truth for provider binaries, native-host ownership, prompt delivery, default/model selectors, effort controls, diagnostic capabilities, known models, route families, and reference wiring. Antigravity is a model-bound provider for Gemini 3.x, Kimi is a dedicated provider backed by Kimi Code, and OpenCode remains the generic pass-through provider.

It does not generate or replace evidence-derived prompting prose. Canonical prompting guidance remains under `prompting/`, and each plugin retains a self-contained `references/model-prompting.md` bundle.

## Validation and generation

Run:

```bash
python3 tools/agents/validate_registry.py
python3 tools/agents/sync_routes.py --check
```

Semantic validation supplements `src/pitwall/agents/resources/schemas/harness-registry.schema.json`. It checks required fields, identifier shapes, repository-contained paths, reference anchors, alias uniqueness, adapter/registry parity, reciprocal native ownership, valid defaults, and the explicit absence of any Mythos-specific route or card.

`tools/agents/sync_routes.py` writes two deterministic files into each plugin package:

- `references/routes.generated.md`
- `references/harness-registry.generated.json`

Claude's generated catalog omits the native Claude provider, Codex's omits native Codex, and Copilot's includes all fourteen providers. Unknown provider-supported model IDs remain pass-through; the catalog is guidance, not a live availability claim.

Some registry fields are generated from `model-facts/` by `tools/agents/sync_model_facts.py`: a model's
`displayName`, `effortValues`, and `provenance`, and a model family's `contextWindow`,
`samplingDefaults`, `license`, `modelCardUrl`, and `provenance`. Change
those through `model-facts/<unit>/facts.json`; `tools/agents/check_generated.py` fails when they drift.
See `model-facts/README.md`.

## Route profile metadata

Each provider declares `harnessKind`: model-bound harnesses own only their registered models, while model-agnostic harnesses can receive endpoint routes. `endpointDelivery` is `none`, `env`, or `config-sync`; `endpointEnv` lists the per-process environment-variable mapping when delivery is by environment, and `configSync` declares configuration materialization support when a harness reads only its own configuration. `promptDelivery` is `stdin`, `argv`, or `file`; `file` gives the harness a private prompt path. `defaultModel.source` additionally supports `pi-config`, `hermes-config`, `cline-config`, `goose-config`, and `dsh-config` for harness-configured defaults.

These `endpointDelivery` values describe how a route reaches a harness, not
whether an endpoint is warm or healthy. See [Self-hosted
endpoints](self-hosted.md) and [Attach a locally hosted model](attach-local-endpoint.md)
for cold-start, timeout, discovery, and readiness
semantics.

Endpoint adapters may add provider-specific behavior without expanding the route schema: `endpoint_argv(name, entry)` supplies argv before entry arguments, `endpoint_environment_extras(entry, env_updates)` derives variables from mapped endpoint values, and `endpoint_sync_commands(entries, env, home)` returns documented argv commands used by command-backed sync plans. Pi writes its managed `models.json`; goose receives endpoints through environment variables; Hermes writes a managed `providers:` block into `config.yaml` and selects it at dispatch with `endpoint_argv`'s injected `--provider <route-name>`, holding several managed providers at once; Cline runs `cline auth` and therefore has one `openai-compatible` endpoint at a time; dsh writes its single managed `settings.yaml` mapping. Muse is model-bound and has no endpoint sync.

The top-level `modelFamilies` section describes route families independently of a harness. Its requirements are identical to route families: stable identifiers, model matching, ownership and reference wiring, and no Mythos-specific route, card, or reference. This lets a route resolve a model before choosing a compatible harness.

`src/pitwall/agents/resources/config/model-catalog.json` is the lightweight open-weight roster used by the route picker. `tools/agents/validate_registry.py` validates it alongside the registry, and `tools/agents/sync_routes.py` renders it into host views. Catalog entries do not require prompting references.

The lightweight roster owns harness selection, authentication, prompt delivery, effort controls,
aliases, and harness-native or subscription-only records. Pitwall's model dossiers remain the
source for deployability, variants, serving engines/images/flags, hardware fit, cost evidence, and
licensing. The catalogues are not merged. Their nineteen current byte-for-byte `modelId` overlaps
and the explicit `google/gemma-4-31B` ↔ `google/gemma-4-31B-it` human-review near match are documented
in [Pitwall's model dossier authority](../models/README.md#ownership-relative-to-agent-routing).
Suffixes such as `-it` are never stripped automatically and near matches are not a CI failure
heuristic.

## Adding a fixed model

Add the registry entry and its substantive prompting reference/capability card together. Every model entry requires:

- display name and aliases;
- allowed effort values;
- canonical prompting reference;
- package-local runtime reference anchor;
- capability-card path;
- provenance classification.

Then regenerate with `python3 tools/agents/sync_routes.py` and run the full test suite. Discovery backends live in `src/pitwall/agents/discovery.py`; keep executable discovery behavior out of the declarative registry and preserve unknown-model pass-through where configured.

## Binary candidates

Adapters implement the actual executable lookup behavior. `binaryCandidates` documents the ordered candidates, while `binaryOverrideEnv` names the additive override. Candidate strings such as `$HOME/.opencode/bin/opencode` are expanded by the relevant adapter rather than interpreted by a shell; provider commands are never assembled with `shell=True`.
