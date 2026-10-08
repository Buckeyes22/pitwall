# Model facts

This directory records what vendors, harnesses, hosts, and released model files state about the
models Agent Routing uses, with a source for every claim. The registry fields, the marked blocks in
the routing skills, the ledger cards, and the facts sheets are generated from it.

The design is `docs/superpowers/specs/2026-09-28-model-facts-design.md` at the repository root.

## Units

| Directory | One unit per | Holds |
|---|---|---|
| `families/<family>` | capability card in `plugins/claude/skills/subagent-model-routing/ledger/` | the models of that card and their routes |
| `harnesses/<provider>` | registry provider | how the tool that drives a model behaves |
| `hosts/<host>` | service that serves other vendors' models | where a hosted model differs from the vendor |

Each unit has `sources.json`. A unit with recorded facts also has `facts.json` and a generated
`FACTS.md`.

## Sources

`sources.json` lists every page or file the unit relies on. Each source has a kind (`vendor`,
`harness`, `host`, or `artifact`), a page type (`model`, `effort`, `harness`, `guidance`, or
`changes`), and two hashes:

- `observedHash` is what the tool last fetched.
- `reviewedHash` is what a person or the agent command last read.

A source is pending when they differ. A page type that the vendor does not publish is recorded in
`notPublished`, with what was searched and when, so a missing page is never mistaken for a page
that does not exist. `watch` entries find models a vendor releases; `seen` lists the names
already acknowledged.

Two source options stop false changes. `volatile` lists JSON keys removed before hashing, such as a
per-request timestamp. `unordered: true` hashes a page with its lines sorted, for sites that render
table rows in a different order on each request.

Fetched text is kept in `.cache/`, which git ignores. It is never committed.

## Facts

`facts.json` holds facts from a closed list of keys and short guidance statements. Every fact
cites a source id and a locator. When two sources disagree, one value is kept, the other is kept
as evidence with `"disagrees": true`, and a `note` explains why. The order of trust is the released
artifact, then the host for facts about that host, then the vendor, then the harness.

| Model keys | `contextWindow`, `maxInput`, `maxOutput`, `inputModalities`, `knowledgeCutoff`, `effortValues`, `effortDefault`, `effortOnOther`, `thinkingCanDisable`, `mustReturnReasoning`, `samplingDefaults`, `samplingLocked`, `license`, `released`, `retires`, `successor`, `revision`, `parameters` |
|---|---|
| Host keys | the model keys, plus `webSearch` and `batch` |
| Harness keys | `headlessFlag`, `outputFormats`, `effortFlag`, `permissionDefault`, `sandboxDefault`, `instructionFiles`, `subagents` |

`revision`, `parameters`, `license`, `contextWindow`, and `samplingDefaults` can be extracted from
Hugging Face files without judgement. Every other fact is reviewed. Effort values come from the
chat template when the model has one.

Skill cards show facts only. A guidance statement marked for the `card` or `reference` surface
appears in the reference; the card block never renders statements.

A route with `"register": true` puts the model in the registry under that provider. A route's own
`effortValues` overrides the model's when the harness accepts fewer values than the model.

## Commands

Run from the repository root.

| Command | Does |
|---|---|
| `uv run --frozen python tools/agents/model_sources.py fetch [UNIT ...]` | download sources into `.cache/` and record what was seen |
| `uv run --frozen python tools/agents/model_sources.py check [--json] [--write] [UNIT ...]` | report pending, unreachable, new, retiring, and stale items; exit 1 when there is any |
| `uv run --frozen python tools/agents/model_sources.py review UNIT ID [ID ...]` | record that these sources were read as they are now |
| `uv run --frozen python tools/agents/model_sources.py baseline UNIT [--except NAME]` | acknowledge the names the watches report now |
| `uv run --frozen python tools/agents/sync_model_facts.py [--check]` | generate the registry fields, marked blocks, and facts sheets |
| `uv run --frozen python tools/agents/validate_model_facts.py [UNIT ...]` | check facts against their schemas, sources, and the registry |

`/pitwall:model-facts <family>` runs these in order for one family and writes the facts.

A scheduled workflow, `.github/workflows/agent-routing-model-facts.yml`, runs `check --write`
weekly and opens or updates one pull request. It never marks a source reviewed.
