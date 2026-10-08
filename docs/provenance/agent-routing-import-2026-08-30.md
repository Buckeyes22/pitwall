# Agent Routing Import Provenance

This record documents the behavior-neutral Agent Routing snapshot imported on 2026-08-30.

- Public source remote: `https://github.com/Buckeyes22/subagent-model-routing.git`
- `ROUTING_SOURCE_SHA`: `c0c5888a1c1315a8b29e4ca63aceb81e91de475c`
- `ROUTING_READINESS_SHA`: `6b45a9569fc4f9a74ad75c0b8585baaacedaaa50`
- Source allowlist SHA-256: `31520b4043d13ca376f87a0f8dd28a98bbd3d0a1d6d5c1e6dc4bbbe9e0de5068`
- Readiness builder SHA-256: `87dcae5cc12fe7d15a9ae810cd58c0b97f79977c67974595104ab11f34f2d512`
- Candidate manifest SHA-256: `fe7ac6ae89a3330e9c521c8f1c559b3b917a0406452cea0d09cd404d9b0aaf14`
- Manifest-listed source-file count: 236
- `PITWALL_PUBLIC_BASE_SHA`: `a89a1b387c91631077be46e34401725efce302a0`
- Public DCO identity: Pitwall Maintainers &lt;buck&#101;yes22&#64;users.noreply.github.com&gt;
- Component license at import: MIT, retained byte-for-byte at `docs/agents/LICENSE` (historical file); since the merge the package is Apache-2.0 as a whole and the MIT terms are reproduced in the root `NOTICE`

The source allowlist excludes private planning and agent state, caches, local environment files,
generated public-release metadata, and other paths that are not part of the reviewed product
snapshot. The readiness change added exactly `docs/attach-local-endpoint.md` and
`docs/self-hosted.md` to correct the public allowlist; no product source was changed.

The pristine snapshot and a separate disposable validation copy were validated with pinned uv
0.11.19 and managed CPython 3.14.7. The isolated environment used pip 26.2.1, Ruff 0.16.0, and
mypy 2.3.0. Ruff, strict mypy, Markdown links, schemas, plugin and host-native boundaries,
registry and generated-output checks, the hash lock, route synchronization, parser compilation,
shell syntax, source-CI contracts, privacy review, and exact manifest verification passed. Each of
the three full test-suite runs reported 491 tests with 488 passed and the three documented live-
endpoint skips; the portable module set passed 166 of 166 tests on Linux. A genuine managed macOS
3.14.7 rerun remains assigned to the later platform-validation lane and is not claimed here.

This provenance record is committed atomically with the behavior-neutral import from the exact
pristine candidate. Original local source history and both reviewed successor commits are
preserved in verified private recovery bundles; private archive locations and ref names are
intentionally omitted.
