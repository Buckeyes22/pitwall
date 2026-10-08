# Draft acceptance matrix assembly

`tools/release_acceptance/matrix.py` creates a candidate-scoped draft matrix from the
existing discovery report, candidate identity, static test-node index, declarations,
and source-reviewed bindings. It does not execute tests or providers and it never makes
an acceptance or release claim.

The output directory is required, must resolve outside the candidate checkout, and
must not already exist. The assembler creates it with mode `0700` and refuses to
follow or replace pre-existing output files:

```bash
.venv/bin/python -m tools.release_acceptance.matrix \
  --root ~/git/pitwall-release-acceptance \
  --output-dir $PITWALL_EVIDENCE_ROOT/release-acceptance/matrix-draft-unique
```

The assembler writes the full manifest in `candidate-snapshot.json`, its matrix
projection in `candidate.json`, `acceptance-evidence.v1.json`,
`acceptance.v1.jsonl`, `acceptance-matrix.md`, and `gap-ledger.json`. Every output
carries the same candidate ID. The candidate snapshot is built before writing the
external directory, so generated files cannot enter their own identity.

Rows from `reviewed-bindings.json` become `not_run` cases only when the binding has an
exact current source hash, valid source line, unique discovered surface, valid proof
lane, required oracle/reviewer/review-state metadata, and exact static test-node ID.
Static source presence is recorded separately from runtime collection; parameterized or
dynamic templates without exact collection proof remain gaps. Stale, malformed, unknown,
duplicate, skipped, and ambiguous bindings are never converted into passes.

Every surface without a validated binding receives a required `not_run` requirement and
an open gap, so draft incompleteness cannot be mistaken for a scope disposition. A
source-reviewed case does not imply coverage of other journeys, scenarios,
lanes, side effects, or cleanup. Declaration selectors are attached as `hint_only` with
`auto_approved: false`; glob matches do not approve coverage or exceptions. Discovery
issues are retained in the matrix and gap ledger. The generated Markdown begins with the
evidence renderer's `NOT RELEASE PROOF` notice and states that runtime collection,
provider calls, cleanup, and release readiness were not verified.

An independently generated pytest collection receipt may be supplied explicitly
with `--collection-report /absolute/path/to/new-external-receipt.json`. The
receipt must be produced by `tools.release_acceptance.pytest_collection` with
`--collect-only`, must match the candidate root and current source hashes, and
must contain one exact collected node for a parameterized binding. Stale,
duplicate, path-escaping, failed, or tampered receipts remain open gaps; no
receipt is loaded implicitly from the candidate checkout.
