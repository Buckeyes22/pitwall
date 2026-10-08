## Summary

<!-- A brief description of what this PR does -->

## Linked Issue

<!-- Link to the issue this PR addresses. Example: Closes #123 -->

## What Changed

<!-- Describe the changes made in this PR -->

## Testing

<!-- Describe how the changes were tested. The standard local gates from CONTRIBUTING.md: -->

```bash
# Hermetic unit tests
make test

# Integration lane (real PostgreSQL + Redis)
make up && make test-int && make down
```

## QA notes

<!-- For user-facing changes. The tester runs these checks before merge
(qa/handbook/acceptance-testing.md). Add the `needs-qa` label when this section is ready. -->

- **What changed for users:**
- **Acceptance criteria** (or the plan and task numbers that list them):
- **How to exercise it:**
- **Risk areas worth exploring:**
- **Needs live credentials (the tester skips these):**

## Checklist

- [ ] Tests added / updated
- [ ] Gate commands pass (`make test`; `make up && make test-int && make down` for PostgreSQL/Redis paths)
- [ ] Documentation updated if needed
- [ ] User-facing change: QA notes filled in and `needs-qa` label added
- [ ] DCO signed