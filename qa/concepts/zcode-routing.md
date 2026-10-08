# ZCode account routing

Pitwall can dispatch through an installed ZCode CLI using its saved model and
account. The profile model `zcode-default` names that saved selection; it does
not pin a GLM model. Existing OpenCode GLM routes are separate.

## Safe check

Run `pitwall agents harnesses` and check that ZCode has a binary path and version.
This does not make a model request or prove that the account works. Run the
hermetic checks with `uv run pytest tests/agents/test_zcode.py -q -p no:cov`.

A live dispatch consumes the signed-in account's quota and requires authorization
for the live test. Use a small coding prompt in a scratch directory; verify both
the response and `SHIM-DONE exit=0`. Missing login or quota errors are failures.

## Common confusion

`--model` and reasoning-effort overrides are not available on this ZCode CLI.
Choose the model in ZCode. Restricted Pitwall dispatches explicitly select build
mode, because ZCode's headless default is yolo. Build may need tool approval.

Check question: does a version check establish usable subscription access? No.

Read [the ZCode route guide](../../docs/agents/zcode.md) for setup and sources.
