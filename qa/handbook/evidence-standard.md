# Evidence standard

Evidence is what lets someone else trust a result without redoing it.

## What counts as evidence

- The exact command that was run.
- Its exit code (`echo "exit=$?"` right after, or `echo "exit=${PIPESTATUS[0]}"` after a pipe).
- The relevant excerpt of the output: enough to show the result, not the whole log.
- The commit tested: `git rev-parse --short HEAD`.
- The environment: OS, Python, and `uv run pitwall --version`.

## What does not count

- An agent's summary ("tests pass") without the output behind it.
- "It works on my machine" without the command and output.
- A screenshot without the command that produced it.
- A result from an earlier session or a different commit, unless it says so.

## Saving output

Save output while you run the command:

```bash
make test 2>&1 | tee qa/.work/evidence/T1-01-make-test.txt | tail -15
echo "exit=${PIPESTATUS[0]}"
```

`2>&1` includes error messages. `tee` writes the file and still shows the output. `tail` keeps the
screen short.

## Attaching evidence

- Put short excerpts in the issue or report inside a code block.
- For long output, include the key lines, and say which file in `qa/.work/evidence/` holds the rest.
  The maintainer can ask for it.
- Redact before posting (rule R10).
