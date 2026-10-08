# Recorded-run provenance

`tools.release_acceptance.run_provenance.validate_run_record(path, evidence_root, expected_candidate_id)` validates one `run_recorder` receipt without executing its command or rebuilding its candidate. It returns an empty list when provenance checks pass and a list of errors otherwise. The function is read-only: the receipt and every output or candidate snapshot it reads must resolve inside `evidence_root`; `cwd`, `candidate_root`, and recorded arguments are metadata and are never opened or executed.

The validator requires the recorder schema, a completed zero-exit run, no timeout or interruption, resolved process cleanup, complete retained stdout and stderr, matching retained-byte SHA-256 hashes, timezone-aware ordered timestamps, and a stable candidate. Both candidate snapshots must be available, self-consistent, readable, and have the expected ID. Their IDs are recomputed through the existing `candidate.py` identity payload and canonical encoding. A snapshot with an unreadable or unproven manifest, an escaped artifact path, a missing log, or a changed digest fails.

An indexed tracked file deleted from the working tree is a valid candidate manifest tombstone when it carries the candidate recorder's `mode: "missing"` and a recognized Git index mode. Other missing content without that index evidence remains unproven.

For a structured artifact binding, pass `expected_result_path` and
`expected_result_sha256`. Both are required together. The validator requires
that the path identify a retained `result_paths` entry, that its recorded
digest match the expected digest, and that the retained bytes still hash to
that digest. Unresolved, escaped, symlink, non-regular, or missing result
artifacts cannot satisfy this binding.

This result establishes run and source provenance only. It does not establish that a test case passed, that a product behavior is accepted, or that a release is ready. Output hashes cover the bytes retained by the recorder after its declared redaction boundary, not undeclared secrets that may have been emitted before capture.
