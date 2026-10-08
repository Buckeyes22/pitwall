# Pi Workbench baseline

Pi Workbench (`pitwall workbench`) starts stock Pi with one exact model profile. Its
solo baseline checks tools, reasoning controls, compaction, cancellation, vision, and redacted
usage evidence against a disposable fixture. The optional native profile delegates bounded scout, writer, and reviewer tasks through a pinned backend, with exact
identity, fresh context, same-child controls, and host-shared request admission. It makes no endpoint-wide quota promise.

The profile keeps credentials as environment-variable names and writes generated provider data into
an isolated `PI_CODING_AGENT_DIR`. A generated ownership marker allows native session continuation;
edited or unowned state fails closed. A baseline report records each gate as passed, failed, or
unexecuted so a missing endpoint cannot become a simulated success.

Safe checks start with `pitwall workbench doctor`, which makes no provider request, and
`uv run pytest tests/workbench -q | tail -40`. A live RPC run requires an explicitly prepared disposable
fixture and an authorized credential environment; see the [Pi Workbench guide](../../docs/operator/pi-workbench.md).

Check question: which parts of the baseline report are evidence from the model, and which are
independent fixture checks?

Additional acceptance checks: inspect `/agent-control list`; run `pitwall workbench doctor` and
`pitwall workbench usage` without making a model request; prove dirty writer bases are preserved;
inspect a handoff patch and its independent validation result before integration.
Use `--restricted` to exercise the tested Linux filesystem boundary, remembering
that provider network access and the selected credential remain available. A
worker's completion report alone is not acceptance evidence.
