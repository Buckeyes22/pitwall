# Free-tier catalog sync provenance — 2026-09-10

- Upstream: `omniroute@3.8.51` (npm), MIT, © 2026 diegosouzapw
- Superseded: in 0.2.0a1 the catalog was re-pinned to `omniroute@3.8.50` (3.8.51 was unpublished); see `config/gateway-catalog.lock.json`. This record describes the 2026-09-10 sync.
- Tarball SHA-256: `03ab03f798260cf79d9a63576a84d7d85925b1e5d1ed765f4947171f03a719c8`
- Extractor: `tools/gateway/sync_catalog.py`, SHA-256 `261d91f1c5f9bd15fa2409d02748059611ea65250b726b303596542336ae7e48`
- Rows: 444 budgets → 424 catalog rows (20 skipped from the 4 known-unreachable providers); steady monthly tokens (pool-deduped): 1288425000; pools: 72
- Registry coverage: 73 of 77 catalog providers carry a `baseUrl` in the upstream registry; the remaining 4 (`agy`, `arcee-ai`, `glm-cn`, `opencode-zen`) are listed in `KNOWN_UNREACHABLE` and dropped from the sync
- Avoid-list providers: ai21, blackbox, coze, duckduckgo-web, fireworks, friendliai, iflytek, kiro, muse-spark-web, nlpcloud, opencode, t3-web
- Diff summary: switched base-URL source from `src/shared/constants/config.ts#PROVIDER_ENDPOINTS` (12 of 77) to `open-sse/config/providers/registry/<id>/index.ts` (73 of 77) so non-direct rows can be retargeted through the fork (Task 19).