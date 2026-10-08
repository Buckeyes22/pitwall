# v0.8 route profiles migration

v0.8 adds named route profiles and an open-weight roster. It is additive: the six v0.7 provider shims, their argv syntax, sentinels, environment variables, run directories, doctor defaults, worktree operations, and workflow runner behave exactly as in v0.7.

New surfaces:

- `agy-shim.sh` adds the Antigravity CLI as the Gemini 3.x vendor harness; it is additive to the existing shim contracts.
- `route-shim.sh <name>[@harness] <prompt-source>`, the `routes` CLI verbs, `setup routes`, and `PITWALL_AGENTS_PROFILES`.
- `config/model-catalog.json` and the registry's `harnessKind`, `endpointDelivery`, `endpointEnv`, `configSync`, and `modelFamilies` fields.
- Exit `78` (EX_CONFIG) for route configuration that is incomplete; like usage errors it writes no ledger row.
- Ledger rows now carry `route` (`null` for direct shim use) and `schema_version` 4; `result.json` carries an optional `route` object; `dispatch.created` events carry it in `data`.
- Seed cards for Muse Glimmer, DeepSeek V4, and Gemma 4.

Nothing is written to a harness's own configuration unless you run `pitwall-agent-routing routes sync` and confirm the diff. Existing `distill` mappings are unchanged; three new model-string mappings cover the new families.
