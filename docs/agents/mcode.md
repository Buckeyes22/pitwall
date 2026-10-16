# MiniMax Code through a MiniMax account

The `mcode` harness runs MiniMax Code with the operator's MiniMax login. Pitwall sends the prompt
on stdin, selects one registered model explicitly, sets the dispatch workspace with `--cwd`, and
requests JSON output so the answer and token usage are recorded in the run. `MCODE_BIN` overrides
binary discovery.

## Installation and login

Install the CLI with npm or use MiniMax's installer, which places the binary under the user-level
`.minimax-code` directory:

```sh
npm install -g @minimax-ai/code
mcode login --region global
```

For accounts using the default region, `mcode login` is also supported. Pitwall's optional harness
setup uses the pinned npm package `@minimax-ai/code@0.6.12`; npm recipes do not have a reviewed
checksum. Setup does not log in for you. Complete login with the CLI before dispatching.

## Models and effort

The registered routes are:

| Model | Default | Effort values |
|---|---:|---|
| `minimax/MiniMax-M3.1-Flash-Preview` | Yes | default (omit `--effort`), low, medium, high, xhigh, max |
| `minimax/MiniMax-M3` | No | Not supported |
| `minimax/MiniMax-M2.7` | No | Not supported |
| `minimax/MiniMax-M2.7-highspeed` | No | Not supported |

Only M3.1-Flash-Preview accepts `--effort`. Unknown `minimax/...` model IDs are refused before
launch because MiniMax Code accepts unknown IDs without reporting an error. These routes use the
MiniMax account and do not accept custom endpoint profiles.

Add a profile and dispatch it as follows:

```sh
pitwall agents profiles add minimax --model minimax/MiniMax-M3.1-Flash-Preview --harness mcode
pitwall agents dispatch route minimax /absolute/path/to/prompt.md
```

## Permission behavior

MiniMax Code's `smart` permission mode is used by default. Set
`PITWALL_AGENTS_UNRESTRICTED=1` to select `--permission full`. In smart mode, the CLI can decline
a command that it cannot ask permission for while still exiting successfully. Pitwall detects
`HOST_CAPABILITY_UNAVAILABLE` and records exit `77` with a remediation to rerun unrestricted.

## Orchestrator channel

`pitwall agents setup mcp --harness mcode` registers `pitwall-channel` in the user-level
`~/.minimax/mcp.json` file under `mcpServers`. The entry uses `enabled: true` and a timeout in
milliseconds; the CLI inherits the dispatch environment for the stdio server. The supported
`enabled: false` switch disables the registration.

## CLI exits

- `0`: CLI success; Pitwall still checks for a smart-mode refusal in the output.
- `2`: invalid CLI arguments, model syntax, unsupported effort, or empty input.
- `3`: not signed in; run `mcode login`.
- `6`: the CLI timeout elapsed.

## Sources

- [MiniMax Code CLI quick start](https://agent.minimax.io/docs/cli/quick-start)
- [Harness registry and installer recipe](../../src/pitwall/agents/resources/config/harness-registry.json)
