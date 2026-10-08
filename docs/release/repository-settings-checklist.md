# Repository settings

The GitHub repository should remain practical for a solo maintainer:

| Control | State |
| --- | --- |
| `main` | Pull requests, required `CI` check only; no force-push or deletion |
| Reviews | Optional while the project has one maintainer |
| Release tags | Tag ruleset on `refs/tags/v*` restricts creation, update, and deletion to the maintainer and blocks non-fast-forward updates. Tag signatures are verified with `git tag -v` (see `RELEASING.md`), because rulesets do not check tag-object signatures |
| Actions | Read-only default token and immutable action references |
| Releases | Environments `ghcr-staging`, `ghcr`, and `github-release`, each limited to tags matching `v*` |
| Security | Private vulnerability reporting, secret scanning, push protection, and Dependabot enabled |
| Community | Issues and Discussions enabled; `SECURITY.md` and `SUPPORT.md` send questions to Discussions |
| Packages | GHCR uses the workflow `GITHUB_TOKEN`; no personal registry token. Production packages are made Public after first publish; staging stays private |

A new repository starts with none of these. Apply every row when it is created, the `main` rule right
after the first push of `main`, and recheck them after changing repository administrators or release
automation.

## Ancestry-preserving integration exception

The one-time Agent Routing merger may require a temporary normal-merge window if the repository's
linear-history rule would otherwise force a squash or rebase that discards reviewed import
ancestry. Record the complete prestate, narrow only the necessary merge rule, perform the normal
merge, verify the resulting graph/tree, then restore every setting in an unconditional cleanup
path and read it back. Do not change force-push, deletion, review, status-check, tag, environment,
or release controls during that window. This exception is not a standing release procedure.
