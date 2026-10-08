# B1 — Terminal basics

**Mode:** training | **Needs:** none beyond B0 | **Output:** none

## Goal

Get comfortable moving around and running commands in a terminal.

## You will learn

- The prompt; `ls`, `cd`, and `pwd`.
- Absolute versus relative paths.
- `head` and `less`.
- Tab completion and history.
- Ctrl-C.
- Copy and paste.
- Pipes and `tee`.
- Exit codes.
- What `sudo` means.

## Before you start

A terminal at the repository root. Read these cards: [terminal and shell](../concepts/terminal-and-shell.md), [files, paths, and permissions](../concepts/files-paths-and-permissions.md), [logs, tracebacks, and exit codes](../concepts/logs-tracebacks-and-exit-codes.md).

## Steps

### Step 1 — Where am I

Coach: the prompt is your ground truth. If it does not show the repository folder, run `cd` before trusting any command.
Tester does:

```bash
pwd
```

Expected: a path that ends in `/pitwall`.
If different: run `cd ~/pitwall` to land in the clone.

### Step 2 — What's here

Coach: `ls` lists names; `-a` shows dot-files too.
Tester does:

```bash
ls
ls -a
```

Expected: the first list has `README.md`, `qa`, `src`, and `tests`; the second also has `.git`.

### Step 3 — Moving around

Coach: `..` is one folder up; `~` is your home folder.
Tester does:

```bash
cd qa
pwd
ls
cd ..
pwd
```

Expected: `pwd` shows `.../pitwall/qa`; `ls` includes `START-HERE.md`, `bootcamp`, and `missions`; the second `pwd` shows `.../pitwall`.

### Step 4 — Reading files

Coach: `head -N <file>` shows the first N lines. `less <file>` opens a pager.
Tester does:

```bash
head -5 qa/README.md
less qa/START-HERE.md
```

Expected: the first line of `qa/README.md` is `# QA program`. In `less`, arrows and space scroll, `/` searches, `q` returns to the prompt.

### Step 5 — Shortcuts

Coach: Tab completes names. Up arrow recalls earlier commands. Ctrl-C stops a running command.
Tester does:

```bash
head -3 qa/STA[TAB]
[UP-ARROW]
sleep 30
[CTRL-C]
```

Expected: the first completes to `head -3 qa/START-HERE.md`; the up arrow recalls a previous command; `sleep 30` waits 30 seconds; Ctrl-C shows `^C` and returns.

### Step 6 — Copy and paste

Coach: the terminal is not a browser. Copy is Ctrl-Shift-C; paste is Ctrl-Shift-V.
Tester does:

```bash
uname -sr
```

Expected: a line starting with `Linux` and a version number. Select it, copy with Ctrl-Shift-C, paste into the chat with Ctrl-Shift-V.

### Step 7 — Pipes and tee

Coach: `|` passes one command's output into the next; `tee` saves that stream to a file; `2>&1` merges errors into normal output.
Tester does:

```bash
ls qa | wc -l
ls -la qa 2>&1 | tee qa/.work/evidence/B1-ls.txt | head -5
ls qa/.work/evidence
```

Expected: a number, then the first five lines of the listing, then a list that includes `B1-ls.txt`.

### Step 8 — Errors and exit codes

Coach: every command prints an exit code in `$?`. `0` means success. After a pipe, `${PIPESTATUS[0]}` reads the first command's exit code.
Tester does:

```bash
cat does-not-exist.txt; echo "exit=$?"
ls qa > /dev/null; echo "exit=$?"
```

Expected: `No such file or directory` and `exit=1`, then `exit=0`.

### Step 9 — sudo

Coach: `sudo` runs a command as the administrator. The coach never runs it; the tester types their own `sudo` and password. Rule R2 lets the tester use `sudo` only inside [B2](B2-install-and-clone.md); outside B2 the rule blocks it.
Tester does: none.
Expected: the tester can say in plain words what `sudo` does and when it is allowed here.

## Checkpoint

Ask: "What does `|` do? What does `tee` add? What does exit code 0 mean?"
Expected: `|` sends one command's output into the next; `tee` also saves it to a file; `0` means success.

## Done when

The tester navigated to the repository root, listed `qa/`, saved output with `tee`, and explained exit codes.

## Record in progress

Add a `Completed` row for B1. Under "Terms taught" add: shell, path, pipe, exit code.
Set `Current item: B2`. Write a short session log entry.

## Next

[B2 — Install your tools and check your clone](B2-install-and-clone.md)
