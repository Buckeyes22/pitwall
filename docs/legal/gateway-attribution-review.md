# Gateway attribution review — 2026-09-10

- Amendment, 0.3.0a1: the vendored upstream `LICENSE` left with `packages/gateway`, so the full MIT
  permission notice (copyright line and permission text) is now reproduced in `NOTICE`, which ships
  in the wheel (`.dist-info/licenses/NOTICE`), the sdist (`/NOTICE`), and all five service images
  through the installed wheel. `NOTICE` also records the pinned tarball SHA-256 the catalog was
  extracted from. `tests/legal/test_notice_and_sbom.py` fails if that notice or hash disappears.

- Status: Approved for the free-tier gateway (Prong 2) catalog data import.
- Scope: What ships from `diegosouzapw/OmniRoute` v3.8.51 (MIT) in this PR.
- Amendment, 0.2.0a1: the vendored `open-sse/` fork (`packages/gateway`) was removed when the gateway
  became a Python service, so only the catalog data attribution below still applies to shipped
  files; the fork sections are kept as the record of the earlier releases. The catalog is now
  pinned to omniroute 3.8.50 (3.8.51 was unpublished).

## What is shipped now (Task 3)

- **Catalog data only.** `config/gateway-catalog.json`, the lock file, and the
  generated seed YAMLs are derived from
  `open-sse/config/freeModelCatalog.data.ts` (444 hand-curated rows) and
  `src/shared/constants/config.ts::PROVIDER_ENDPOINTS`. Each row is a fact
  record (provider name, model id, monthly token cap, free-tier classification,
  ToS verdict) plus a human-readable display name. Facts are not copyrightable;
  display names are short factual labels of the model offerings.
- The MIT notice chain is preserved by reproducing the upstream permission
  notice in `NOTICE` (see the 0.3.0a1 amendment above); the data-only import
  here is not a derivative work in the copyright sense.

## What is *not* shipped in this lane

- **No `wreq-js` notice burden.** `wreq-js` is the TLS-impersonation library
  that the upstream Node gateway depends on for browser-fingerprint transport.
  Per ADR 0007 principle 6 the catalog data import deliberately stops at the
  data layer; the `wreq-js` notice obligation follows only when (and if) the
  gateway fork is taken into `packages/gateway` in Lane D (Tasks 18–19).
- **No source code from OmniRoute.** No `.ts`/`.mjs` files are vendored. The
  `tools/gateway/extract_upstream.mjs` script is original Pitwall code that
  regex-parses the upstream data files when Node is built without
  `--experimental-strip-types`; it does not import or copy any upstream source.
- **No model weights, datasets, or binaries.** None are referenced by the
  catalog.

## Provenance

- Tarball SHA-256, extractor SHA-256, row counts, and pool counts are recorded
  in `docs/provenance/gateway-catalog-2026-09-10.md` and the
  `config/gateway-catalog.lock.json` lock file; the drift gate in
  `tools/gateway/check_catalog_drift.py` fails CI on any change to the locked
  avoid-list and warns on count changes.

## Subsequent obligations

- Lane D (Tasks 18–19) will carry the full `open-sse/` subset into
  `packages/gateway/`. The MIT notice chain, `THIRD_PARTY_NOTICES.md` updates,
  and `wreq-js` attribution are part of that lane's scope, not this one.

## Fork subset

This is the Task 18 follow-on. The subset shipped as `@pitwall/gateway` is the
`open-sse/` directory of `diegosouzapw/OmniRoute` v3.8.51 (commit
`ba597b631d22d85e56db6982f24b7d1ebe238df9`) minus the strip-list in
`packages/gateway/scripts/strip-list.txt` (reproduced under ADR 0007
"Fork strip-list" section), reduced to the eight files the shim imports
(`TYPE_CHECK_FILES` in `packages/gateway/scripts/import-upstream.sh`). `packages/gateway/open-sse/` retains its original
file-level copyright headers and is governed by the same MIT terms as the
upstream package. `packages/gateway/UPSTREAM.lock` records the upstream tag, the
upstream commit SHA, the committed-at timestamp, and the SHA-256 of every kept
file so downstream consumers can verify the provenance chain against a fresh
clone of the upstream tarball.

### Dropped upstream notice chain

The fork intentionally drops the upstream Node gateway's TLS-impersonation
dependency chain and the assets it links. The omitted packages would each have
their own licence and notice requirements if shipped; by excluding them from
the import, the fork owes none of those notices and `THIRD_PARTY_NOTICES.md` for
the gateway component omits them on purpose, not by oversight. The dropped
upstream notice chain is:

- `wreq-js` (MIT, © 2024 YuKang Doh, author of `wreq`). The TLS-impersonation
  library the upstream Node gateway depends on for browser-fingerprint
  transport. Replaced in `packages/gateway/` by `globalThis.fetch`; the import
  script enforces `grep -rl "wreq" open-sse && exit 1`.
- `BoringSSL`. Bundled with `wreq-js` for the TLS client hello fingerprint.
  Not shipped because `wreq-js` is not shipped; the BoringSSL licence text and
  the `wreq-js` notice obligation are both absent from the fork.
- `ICU4X`. Unicode collation library bundled with `wreq-js`. Not shipped
  because `wreq-js` is not shipped.
- `Mozilla-CA`. Mozilla CA bundle bundled with `wreq-js`. Not shipped because
  `wreq-js` is not shipped; the fork relies on the host's default Node TLS
  trust store instead.

The drop is structural, not contractual. The strip-list excludes the upstream
`browserBackedChat*` / `browserPool*` / `claude-web` / `chatgpt-web-codex` /
`antigravity*` / `*-web.ts` executors and `imageGeneration*` / `imageUpscale*` /
`mediaGeneration/` / `musicGeneration.ts` / `audio*` / `search*` / `cursorCliProxy.ts`
handlers whose only reason to exist is to drive the `wreq-js` based transport.
ADR 0007 principle 6 binds: there is no ban-evasion, fingerprinting, or
web-session executor in the kept subset even if a future drift attempt
re-adds a matching path.

### Express survival check

The kept subset must not import or reference any of the dropped transitive
notices. The import script's closing assertion is:

```bash
grep -rl "wreq" packages/gateway/open-sse && exit 1
```

The CI job `Gateway CI / lock` runs `npm ci --ignore-scripts` against the
fork's `package-lock.json`, and `Gateway CI / build` asserts the launcher,
shim, and reduced `open-sse/` subset ship. A regression that re-adds `wreq-js`
would fail the import script first and the launcher's smoke step second.
