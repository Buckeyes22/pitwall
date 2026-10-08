# Bug reports

A good bug report lets someone who wasn't there see the problem, reproduce it, and know why it
matters, without asking you anything.

## The bug-report checklist

Check every item before posting:

1. **Title** says what is wrong and where, specifically. Not "API broken". Instead:
   "`POST /v1/inference` returns 500 when `texts` is an empty list".
2. **Repro steps** are numbered, start from a clean state, and use exact commands.
3. **Expected Behavior** says what should happen, quoting or linking the doc that says so.
4. **What Happened** says what did happen, with the relevant output.
5. **Evidence** has the command, the exit code, an output excerpt, and the commit tested
   ([evidence standard](evidence-standard.md)).
6. **Environment** is filled in.
7. **Severity** is chosen with the [severity scale](triage-and-labels.md#severity-scale), with one
   sentence explaining why.
8. **Redaction** is done: no keys, tokens, personal data, or model-server addresses (rule R10).
9. **Duplicate search** is done (below).

## Product bug or doc bug

- The product does the wrong thing: label `bug`.
- The product does the right thing but a doc says something else: label `documentation`.
- You can't tell which is right: ask first (rule R7). File a `question` issue, or add the item to
  "Questions for the maintainer".

## Duplicate search

Search open and closed issues before filing:

```bash
gh issue list --state all --search "<two or three keywords>"
```

If a match exists, add a comment with your evidence instead of filing a new issue.

## Writing the report

1. Copy `qa/templates/bug-report.md` to `qa/.work/notes/<short-name>.md`.
2. The tester writes the title and each section. The coach reviews against the checklist.
3. Delete the instruction comment at the top of the draft before posting.

## Filing with gh

After the tester approves the draft (rule R9):

```bash
gh issue create --title "<title>" --body-file qa/.work/notes/<short-name>.md \
  --label bug --label found-by-qa --label severity:3-medium
```

Use `--label documentation` instead of `--label bug` for a doc bug. Use exactly one severity label.
The command prints the new issue's address. Record it in the progress file's `Completed` table.

## After filing

- Severity 1: message the maintainer directly as well (rule R14).
- When the maintainer asks a question on the issue, answer with evidence.
- When the fix merges, verify it ([issue lifecycle](triage-and-labels.md#issue-lifecycle)).
