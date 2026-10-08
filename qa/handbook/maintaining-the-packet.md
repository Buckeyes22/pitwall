# Maintaining the packet

This folder describes a moving product. These rules keep it correct.

## Packet parity

`CONTRIBUTING.md` makes QA packet parity part of the Definition of Done
([definition of done](../../CONTRIBUTING.md#definition-of-done)): a pull request that changes a
command, route, screen, or doc heading that a lesson uses updates that lesson in the same pull
request. `uv run python tools/ci/check_markdown_links.py` (also `make docs-check`) fails when a
linked heading is renamed or removed. Review catches the rest.

## New surfaces

When a pull request adds a user-facing surface, it also adds any standing mission or concept card
the surface needs, and its `## QA notes` drive the tester's acceptance run (mission T3-03).

## Packet bugs

When a lesson is wrong, unclear, or out of date, the tester files an issue with the `qa-packet`
label, plus `documentation` and a severity. The maintainer fixes it, or asks the tester to open a
pull request for it.

## Re-verifying commands

When a lesson changes, run its commands on a fresh clone the way the original verification did
(see the `qa-packet-verification` evidence file in `docs/evidence/`), and put the results in the
pull request description.
