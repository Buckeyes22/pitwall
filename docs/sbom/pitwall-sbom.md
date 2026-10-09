# Software bill of materials

`pitwall-sbom.cdx.json` is a source-tree snapshot of the frozen runtime graph
for `pitwall`. Generate it with the exact release lock:

```bash
make sbom
```

`make sbom` runs `uv export --frozen --preview-features sbom-export --format cyclonedx1.5
--no-dev --no-emit-project` and keeps the committed file when only the serial number and
timestamp would change. `make regen` runs it together with the other generated files.

Regenerate whenever `uv.lock` or the version changes;
`tests/legal/test_notice_and_sbom.py` fails when the snapshot drifts.

The committed snapshot is review evidence, not the sole release inventory. The
release workflow also generates an SPDX SBOM from the installed wheel and one
for each of the five built container images, then attaches checksums and GitHub
attestations. Those artifact-derived files are authoritative for a release.

`tools/security/check_licenses.py` walks the installed runtime dependency graph,
rejects unknown/denied licenses, and fails if a review-required package changes
license. Its report states explicitly that final legal approval is an external
release gate.

SBOM timestamps and serial numbers identify a generation event and are not
expected to be byte-reproducible. Package/image artifact reproducibility is
verified separately by rebuilding and comparing bytes.

Image license obligations for the Debian packages in each image are in
[`docs/legal/container-image-licenses.md`](../legal/container-image-licenses.md).
