# Example agent profiles

`pitwall.toml` holds example `[agents.profiles]` tables. Merge them into your `pitwall.toml` (found through
`PITWALL_CONFIG_FILE`, `./pitwall.toml`, or `${XDG_CONFIG_HOME:-~/.config}/pitwall/pitwall.toml`), or point
`PITWALL_AGENTS_PROFILES` at this file, then:

```bash
pitwall agents profiles list
pitwall agents profiles resolve glimmer
route-shim.sh glm prompt.md
```

`glimmer` is an endpoint entry (a self-hosted OpenAI-compatible server) that resolves to the default endpoint
harness; `glm` pins opencode; `sol` is a plain registry model that receives the `harnesses.codex.effort` default.
Effort can live on an individual model entry as `effort` or under `harnesses.<id>.effort` as the default for every
profile resolved to that harness; a profile value wins. Secrets never live here: `apiKeyEnv` names the environment
variable that holds the key. See `docs/agents/routes.md`.
