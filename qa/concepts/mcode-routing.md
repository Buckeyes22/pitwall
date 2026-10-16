# MiniMax Code routing

Pitwall can send a prompt to MiniMax Code using the signed-in MiniMax account. The profile names a
registered MiniMax model, and the shim supplies the workspace and permission mode.

## Safe check

Run `pitwall agents harnesses` and check that MiniMax Code has a binary path and version. This
checks the local CLI contract; it does not prove login, quota, or that a model request will work.
Hermetic checks use a fake CLI: `uv run --frozen pytest tests/agents/test_mcode.py -q -p no:cov`.

A live dispatch uses the signed-in account. Start with a small prompt in a scratch workspace and
check the answer, token-usage record, and `SHIM-DONE exit=0` result.

## Common confusion

M3.1-Flash-Preview is the default route and the only registered MiniMax Code model that supports
`--effort`. The other registered models reject effort selection. In the default `smart` permission
mode, a declined command can look successful to the CLI; Pitwall recognizes
`HOST_CAPABILITY_UNAVAILABLE` and records exit `77`. `PITWALL_AGENTS_UNRESTRICTED=1` selects `full`
permission mode.

Check question: does `mcode --version` prove that the MiniMax account is signed in? No.

Read [the MiniMax Code route guide](../../docs/agents/mcode.md) for setup and supported routes.
