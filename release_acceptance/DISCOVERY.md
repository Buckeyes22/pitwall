# Canonical discovery composition

`tools/release_acceptance/discovery.py` composes the existing REST/MCP, CLI dispatch,
CLI argument, config, Node (Pi workbench), gateway, operations, routing, routing-contract, and TUI inventory
units into one deterministic `release-acceptance-discovery.v1` report. It does not add
a second extractor or execute product/provider code.

Run it from the candidate checkout with:

```bash
.venv/bin/python -m tools.release_acceptance.discovery \
  --root ~/git/pitwall-release-acceptance \
  --output /tmp/release-acceptance-discovery.json
```

Each source row keeps its original `surface_id`, `kind`, `operation`, `source`, and
`metadata`, and receives an `origin` object identifying the domain and source schema.
The facade adds `surface_kind` and `declared_source`, the fields needed by
`tools.release_acceptance.evidence`; it does not rewrite domain-specific contract
metadata. Rows are sorted deterministically. Duplicate semantic IDs remain in the
output and produce `duplicate-surface-id` or `surface-id-collision` issues with their
origins and sources. No suffix or implicit namespace is invented.

Domain issues, deferred scopes, malformed rows, extractor failures, and stale
`metadata.source_sha256` source contracts are retained as unresolved issues. A failed
domain does not hide unaffected rows, and report status is `unresolved` whenever any
issue exists. The report is an inventory input for later acceptance-matrix binding;
`complete` means only that these domain calls emitted no composition issues, not that
any test or release gate passed. Existing `release_acceptance/discovery-review.json`
is reported under `source_review` with `auto_approved: false` and
`status: linked_not_approved`; this facade never treats source review as execution
approval.
