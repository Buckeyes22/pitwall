# The TUI (terminal dashboard)

## In one sentence

The TUI is Pitwall's full-screen terminal dashboard, started with `pitwall dashboard`, with ten
views.

## Why it matters when testing Pitwall

Operators use it every day; crashes and layout problems are findings; some actions are guarded
by typed confirmation.

## Try it

With the test stack up and the README `export` lines set:

```bash
uv run pitwall dashboard
```

Press `?`, then `q`. Expected: help lists the keys, then the app closes.

| Key | View |
| --- | --- |
| `o` | Overview |
| `p` | Providers |
| `l` | Leases |
| `m` | Models |
| `s` | Serve |
| `d` | Pods |
| `t` | Routes |
| `c` | Cost |
| `e` | Resources |
| `a` | Operations |
| `:` | command palette |
| `/` | search |
| `?` | help |
| `q` | quit |

## Common confusions

- Never start it without the README `export` lines (rule R2): without them it starts
  `pitwall setup`.
- Never finish a type-to-confirm dialog.
- The Serve view puts the cursor in its Model field; press `Escape` before a view key there.
- The Providers keys `g` and `a` contact providers, so skip them until tier 5.

## Check yourself

1. Which key opens the Cost view?

<details><summary>Answer</summary>

`c`.

</details>

## Go deeper

- [CLI](../../docs/sdlc/18-cli.md)
