# The terminal and the shell

## In one sentence

The terminal is the window, and the shell (bash) is the program inside it that reads and runs
your commands.

## Why it matters when testing Pitwall

Every lesson step happens in a shell, and the prompt shows who and where you are. The prompt is
your ground truth: if it says `~/pitwall`, you are in the repo root. Run every command from the
repository root unless a lesson says otherwise ([start here](../START-HERE.md)).

## Try it

```bash
echo "$SHELL"
```

Expected: a path ending in a shell name, such as `/bin/bash`.

## Common confusions

- The `$` shown in docs is the prompt, so do not type it. Type only what comes after.
- Ctrl-C stops the running command in that terminal. Copy is Ctrl-Shift-C, paste is
  Ctrl-Shift-V. The shortcuts from a browser do not apply.
- Commands are case-sensitive. `PWD` is not the same as `pwd`.
- Up-arrow recalls history. Ctrl-R searches history by typing a few letters.

## Check yourself

1. What does Ctrl-C do in a terminal?

<details><summary>Answer</summary>

It stops the running command. It does not copy text.

</details>

## Go deeper

- [B1 — Terminal basics](../bootcamp/B1-terminal-basics.md)
