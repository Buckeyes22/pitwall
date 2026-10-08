# Files, paths, and permissions

## In one sentence

A path says where a file is; absolute paths start at `/`, and relative paths start where you are.

## Why it matters when testing Pitwall

Lessons run from the repository root with relative paths such as `qa/START-HERE.md`. Running from
the wrong folder is the most common beginner failure: the file is there, but your terminal is
not. Always check `pwd` before you trust a relative path.

## Try it

```bash
pwd
ls -l qa/START-HERE.md
```

Expected: the first line ends in `/pitwall`, and the second line starts with permissions such as
`-rw-r--r--`.

## Common confusions

- `~` is your home folder; `~/pitwall` means `/home/<user>/pitwall`.
- `.` is here and `..` is one level up. From `qa/`, `../README.md` is the repo-root README.
- `chmod 600` makes a file readable and writable only by you. The tier 5 key file uses this
  mode so other users on the machine cannot read it.
- `ls` lists names; `ls -l` adds permissions, owner, size, and date.

## Check yourself

1. From inside `qa/`, what does `../README.md` point to?

<details><summary>Answer</summary>

The README at the repository root.

</details>

## Go deeper

- [B1 — Terminal basics](../bootcamp/B1-terminal-basics.md)
