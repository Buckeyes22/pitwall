# Agent-Guided QA Onboarding Program — Implementation Plan

**Status:** complete; merged as `4cb8be0` (PR #44). Checkboxes were not maintained during execution; the commit history and tests are the record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. On this repository, Codex implements lanes through
> `~/.claude/scripts/codex-shim.sh` and Claude reviews against the repository, not the report.

**Goal:** Ship the `qa/` program that lets an AI agent train a beginner into Pitwall's QA
function, plus the nine documentation fixes, pull-request template, CONTRIBUTING, and GitHub label
changes the design requires.

**Architecture:** About 100 small Markdown files under `qa/`, written for any agent, entered with
one sentence. A skeleton commit creates every file and every heading that other files link to, so
file-disjoint lanes can write content in parallel while the repository link checker stays green.
Changes outside `qa/` are a single lane. Verification runs the lesson commands in a clean room and
runs a comprehension pass with the weakest target model.

**Tech Stack:** GitHub-flavored Markdown; existing gates `tools/ci/check_markdown_links.py`,
`tools/ci/check_dco.py`, `tools/guards/repo_text_policy.py`, and `tools/security/check_secrets.py`;
`gh`; `uv`; the Docker Compose test stack (`docker-compose.testinfra.yml`); route profile
`q38-pi` through `~/.claude/scripts/route-shim.sh` for the comprehension pass.

**Spec:** `docs/superpowers/specs/2026-09-11-qa-onboarding-design.md`. Read it before any task.
Section numbers below (for example "spec §6.2") refer to it.

## Global Constraints

Every task's requirements include this section.

- **GC-1 Names.** Committed files say "the tester" and "the maintainer". They never contain a
  person's name. The tester's and maintainer's names live only in the ignored
  `qa/.work/progress.md`.
- **GC-2 Entry sentence,** exactly: `Read qa/START-HERE.md and follow it.`
- **GC-3 No root agent files.** Never create a repository-root `AGENTS.md` or `CLAUDE.md`.
- **GC-4 Link notation in this plan.** The repository link checker scans every `.md` file,
  including fenced code, so this plan never uses real Markdown link syntax. `[text]{target}` in
  this plan means the Markdown link with `text` in square brackets followed by `target` in
  parentheses. Write real Markdown links in the files you create or edit. Targets are relative to
  the file being written. `{#anchor}` is a same-file anchor.
- **GC-5 No external Markdown links in `qa/`** (spec D8). A URL that must appear goes in a code
  span or code block.
- **GC-6 No secrets or lookalikes.**
  - No `NAME=value` where `NAME` contains `KEY`, `TOKEN`, `SECRET`, or `PASSWORD` and the value is
    a literal. Values taken from a variable or command substitution (`"$VAR"`, `"$(...)"`) are
    fine.
  - No URL with an embedded `user:password@`. detect-secrets already baselines the README's
    PostgreSQL URL (`.secrets.baseline`, "Basic Auth Credentials", `README.md` line 98), and any
    new copy fails `check_secrets.py`. Lessons send the tester to the README Quick Start export
    lines instead.
  - No literal secret-shaped strings. Describe their shape in words.
  - No IP addresses other than `127.0.0.1`.
  - No hostnames or URLs of the maintainer's infrastructure.
  - detect-secrets' keyword plugin treats words such as pwd, passwd, password, secret, and token
    as secret keywords. When such a word is a code span followed by a comma and another code span,
    the line is flagged (this is why the B1 contract lists the shell commands in the order `ls`,
    `cd`, then the working-directory command). Put the word last in a list or start a new clause
    ("`cd qa`, then `pwd`" passes), and run VC-secrets on every file you write.
- **GC-7 Line limits.** `qa/START-HERE.md` ≤ 100 lines. Lessons and missions ≤ 150. Concept cards
  ≤ 60. Every other `qa/` file ≤ 200.
- **GC-8 Formats.** Lessons, missions, and cards use the exact skeletons in spec §5.3, §5.4, and
  §5.5, headings in that order. Lesson metadata line:
  `**Mode:** <mode> | **Needs:** <ids or none> | **Output:** <output>`. Mission metadata line:
  `**Tier:** <n> | **Mode:** <mode> | **Repeatable:** <yes/no> | **Needs:** <ids> | **Output:** <output>`.
- **GC-9 Skeleton headings are link targets.** Never rename or delete a heading that Task 1
  created. Adding headings is fine. Link only to anchors listed in the Anchor Registry below, or to
  anchors that already exist in files outside `qa/`.
- **GC-10 Commands.** Every command in a lesson or mission sits in a fenced `bash` block followed by
  a line starting `Expected:`. Save meaningful output with
  `2>&1 | tee qa/.work/evidence/<id>-<step>.txt`. Long test output is piped through `tail`.
- **GC-11 Mission safety line.** Each mission's `## Safety` section starts with exactly:
  `Coach rules R1–R3, R8, and R13 apply ([coach rules]{../../coach/rules.md#safety}).` Mission-specific
  cautions follow.
- **GC-12 Plain language.** Define each term on first use or link its concept card. Short
  sentences. One idea per step.
- **GC-13 Commits.** Every commit uses `git commit -s`. Message prefix: `docs(qa):` for `qa/`
  files, `docs:` or `fix(docs):` for the fixes, `docs(plans):` for this plan and the spec. One
  commit per task.
- **GC-14 Exact vocabulary.**
  - Modes: `training`, `work`.
  - Labels: `found-by-qa`, `severity:1-critical`, `severity:2-high`, `severity:3-medium`,
    `severity:4-low`, `needs-qa`, `qa-passed`, `qa-failed`, `qa-verified`, `qa-packet`, plus the
    existing `bug`, `documentation`, and `question`.
  - Rule ids: R1–R14. Mission ids as in the Mission List (Task 10).
- **GC-15 Evidence.** Executors report validation output verbatim. A task is not done with an
  unrun validation command.
- **GC-16 The console trap** (spec §6.2 R2, `src/pitwall/cli.py:850`–`862`). No lesson may run
  `pitwall` with no arguments or `pitwall dashboard` unless the README Quick Start variables,
  including `DATABASE_URL`, are exported in that terminal. Otherwise, in an interactive terminal
  with no credential, it starts `pitwall setup`.
- **GC-17 The docs-page trap** (`src/pitwall/api/app.py:90`, `_PUBLIC_HEALTH_PATHS`). With
  `PITWALL_API_TOKEN` exported (the README does this), every route except the health paths needs a
  bearer token, including `/docs` and `/openapi.json`. Browser lessons start the API with
  `env -u PITWALL_API_TOKEN uv run pitwall-api`.

## Validation Commands

Referenced by id in every task. Run from the worktree root.

| Id | Command | Expected |
| --- | --- | --- |
| VC-links | `uv run --frozen python tools/ci/check_markdown_links.py` | Prints `markdown links passed: <N> files (internal)`; exit 0 |
| VC-policy | `git ls-files -z --cached --others --exclude-standard -- <paths> \| xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py` | No output; exit 0 |
| VC-secrets | `uv run --frozen python tools/security/check_secrets.py` | Prints `secret scan passed: 304 reviewed findings`; exit 0 |
| VC-shape | `uv run --frozen python /tmp/qa_shape_check.py <files>` | Last line `shape check: <n> ok, 0 with problems`; exit 0 |
| VC-count | `find qa -name '*.md' -not -path 'qa/.work/*' \| wc -l` | `99` |

`/tmp/qa_shape_check.py` is a plan-execution aid, never committed. Any task that needs it and does
not find it writes it from this block first:

```python
"""Plan-execution shape check for qa/ packet files. Not committed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

LESSON_H2 = [
    "Goal",
    "You will learn",
    "Before you start",
    "Steps",
    "Checkpoint",
    "Done when",
    "Record in progress",
    "Next",
]
MISSION_H2 = [
    "Why this matters",
    "Sources",
    "Safety",
    "Setup",
    "Steps",
    "What counts as a finding",
    "Done when",
    "Record in progress",
]
CARD_H2 = [
    "In one sentence",
    "Why it matters when testing Pitwall",
    "Try it",
    "Common confusions",
    "Check yourself",
    "Go deeper",
]
SAFETY_LINE = "Coach rules R1–R3, R8, and R13 apply"

MD_TARGET = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s]+)")
PLAN_LINK = re.compile(r"\]\{")
CREDENTIAL = re.compile(r"\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=(?!\"?\$)")
DSN_WITH_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
# 169.254.169.254 is allowed: T2-04 sends it as the SSRF probe path that Pitwall must refuse.
IPV4 = re.compile(r"\b(?!127\.0\.0\.1\b)(?!169\.254\.169\.254\b)\d{1,3}(?:\.\d{1,3}){3}\b")
BANNED = re.compile(r"\b(?:TBD|TODO|FIXME)\b")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def limit_for(path: str) -> int:
    if path == "qa/START-HERE.md":
        return 100
    if path.startswith(("qa/bootcamp/", "qa/missions/tier")):
        return 150
    if path.startswith("qa/concepts/") and not path.endswith("README.md"):
        return 60
    return 200


def kind_of(path: str) -> str:
    if path.startswith("qa/bootcamp/"):
        return "lesson"
    if path.startswith("qa/missions/tier"):
        return "mission"
    if path.startswith("qa/concepts/") and not path.endswith("README.md"):
        return "card"
    if path.startswith("qa/templates/"):
        return "template"
    return "doc"


def headings(lines: list[str]) -> list[tuple[int, int, str]]:
    found, fenced = [], False
    for number, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
            continue
        match = HEADING.match(line)
        if match and not fenced:
            found.append((number, len(match.group(1)), match.group(2)))
    return found


def check(path: Path) -> list[str]:
    rel = path.as_posix()
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    kind = kind_of(rel)
    problems: list[str] = []
    if len(lines) > limit_for(rel):
        problems.append(f"{len(lines)} lines > limit {limit_for(rel)}")
    for number, line in enumerate(lines, start=1):
        for match in MD_TARGET.finditer(line):
            if match.group("target").startswith(("http:", "https:", "//")):
                problems.append(f"line {number}: external Markdown link")
        for pattern, message in (
            (PLAN_LINK, "unconverted plan link notation"),
            (CREDENTIAL, "credential-style assignment"),
            (DSN_WITH_PASSWORD, "URL with embedded password"),
            (IPV4, "IP address other than 127.0.0.1 or the SSRF probe"),
            (BANNED, "placeholder word or personal name"),
        ):
            if pattern.search(line):
                problems.append(f"line {number}: {message}")
    found = headings(lines)
    if kind != "template":
        if not lines or not lines[0].startswith("# "):
            problems.append("first line is not an H1")
        for number, level, title in found:
            following = next((item for item in lines[number + 1 :] if item.strip()), None)
            nxt = HEADING.match(following) if following else None
            if following is None or (nxt and len(nxt.group(1)) <= level):
                problems.append(f"empty section: {title}")
    h2 = [title for _, level, title in found if level == 2]
    expected = {"lesson": LESSON_H2, "mission": MISSION_H2, "card": CARD_H2}.get(kind)
    if expected and h2[: len(expected)] != expected:
        problems.append(f"H2 headings {h2} do not start with {expected}")
    head = text.split("\n## ", 1)[0]
    if kind == "lesson":
        if not re.match(r"^# B\d — ", lines[0]):
            problems.append("lesson H1 must be '# B<n> — <title>'")
        if "**Mode:**" not in head:
            problems.append("lesson metadata line missing")
    if kind == "mission":
        if not re.match(r"^# T\d-\d\d — ", lines[0]):
            problems.append("mission H1 must be '# T<tier>-<nn> — <title>'")
        if "**Tier:**" not in head:
            problems.append("mission metadata line missing")
        safety = text.split("\n## Safety\n", 1)[-1].lstrip().splitlines()
        if not safety or SAFETY_LINE not in safety[0]:
            problems.append("## Safety must start with the standard rules line")
    return problems


def main(argv: list[str]) -> int:
    failed = 0
    for name in argv:
        problems = check(Path(name))
        for problem in problems:
            print(f"{name}: {problem}")
        failed += bool(problems)
    print(f"shape check: {len(argv) - failed} ok, {failed} with problems")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

## File Map

| Path | Responsibility | Task |
| --- | --- | --- |
| `qa/.gitignore` | Ignores `.work/` | 1 |
| `qa/START-HERE.md` | Agent entry: role, read order, bootstrap, routing | 2 |
| `qa/coach/rules.md` | Rules R1–R14 | 2 |
| `qa/coach/session-routine.md` | Session start, work queue, end | 2 |
| `qa/coach/teaching-method.md` | How to teach | 2 |
| `qa/coach/agent-guide.md` | Agents, switching, verifying claims, approvals | 2 |
| `qa/templates/*.md` (6) | Progress, bug report, QA report, exploratory notes, weekly check-in, tier check | 3 |
| `qa/handbook/README.md`, `qa-role.md`, `bug-reports.md`, `triage-and-labels.md`, `acceptance-testing.md`, `evidence-standard.md` | QA operating manual, part A | 4 |
| `qa/handbook/exploratory-testing.md`, `regression-and-release.md`, `test-automation.md`, `live-testing-safety.md`, `maintaining-the-packet.md` | QA operating manual, part B | 5 |
| `qa/concepts/README.md` plus 11 computer-basics cards | Concept index and basics | 6 |
| 10 Pitwall concept cards | Pitwall concepts | 7 |
| 8 QA concept cards | QA concepts | 8 |
| `qa/bootcamp/B0`–`B6` (7) | Bootcamp lessons | 9 |
| `qa/missions/README.md` plus 7 tier 1 missions | Tier ladder and docs QA | 10 |
| T2-01 to T2-06 | Manual testing, part A | 11 |
| T2-07 to T2-12 | Manual testing, part B | 12 |
| T3-01 to T3-05 | Acceptance | 13 |
| T4-01 to T4-07 | Automation | 14 |
| T5-00 to T5-05 | Live (locked) | 15 |
| `qa/README.md`, `qa/maintainer-setup.md` | Human entry and maintainer setup | 16 |
| `README.md`, `CONTRIBUTING.md`, `.github/pull_request_template.md`, `docs/operator/user-journey-catalog.md`, `docs/operator/troubleshooting.md`, `docs/sdlc/18-cli.md`, `packages/agent-routing/README.md`, `packages/agent-routing/docs/doctor.md` | Fixes F1–F9 and the pointers into `qa/` | 17 |
| `docs/evidence/<run date>-qa-packet-verification.md` | V4 and V5 evidence | 21, 22 |

## Anchor Registry

Task 1 creates these headings. Other files may link to them (GC-9). Slugs follow
`tools/ci/check_markdown_links.py` `_slug`: lowercase, drop characters other than letters, digits,
`_`, `-`, and spaces, then turn each space into `-`.

| File | Anchors |
| --- | --- |
| `qa/START-HERE.md` | `1-read-these-files-first-in-order`, `2-if-the-progress-file-does-not-exist`, `3-what-to-open-when-the-tester-says`, `4-where-things-are`, `5-facts-you-need-every-session` |
| `qa/README.md` | `what-this-is`, `for-the-tester-how-to-start`, `for-the-maintainer`, `how-it-is-organized`, `keeping-it-current` |
| `qa/maintainer-setup.md` | `1-github-access`, `2-labels`, `3-agent-access`, `4-the-kickoff-message`, `5-weekly-check-in`, `6-requesting-qa-on-a-pull-request`, `7-unlocking-tier-5` |
| `qa/coach/rules.md` | `safety`, `modes`, `honesty-and-evidence`, `what-you-may-change`, `github-and-privacy`, `staying-on-track`, `urgent-findings` |
| `qa/coach/session-routine.md` | `starting-a-session`, `the-work-queue`, `during-a-session`, `ending-a-session` |
| `qa/coach/teaching-method.md` | `the-teaching-loop`, `one-step-at-a-time`, `new-words`, `when-the-tester-is-stuck`, `checking-understanding`, `using-the-testers-experience`, `letting-the-tester-decide`, `celebrating-real-results` |
| `qa/coach/agent-guide.md` | `which-agent-for-which-job`, `switching-agents`, `checking-what-an-agent-tells-you`, `approval-settings`, `harness-tips` |
| `qa/missions/README.md` | `the-tiers`, `unlocking-a-tier`, `how-the-coach-picks-the-next-item`, `mission-list` |
| `qa/concepts/README.md` | `computer-basics`, `pitwall`, `qa` |
| `qa/handbook/README.md` | `documents`, `who-reads-what` |
| `qa/handbook/qa-role.md` | `what-qa-owns`, `what-the-maintainer-owns`, `weekly-check-in` |
| `qa/handbook/bug-reports.md` | `the-bug-report-checklist`, `product-bug-or-doc-bug`, `duplicate-search`, `writing-the-report`, `filing-with-gh`, `after-filing` |
| `qa/handbook/triage-and-labels.md` | `severity-scale`, `labels`, `issue-lifecycle`, `queries-the-coach-uses` |
| `qa/handbook/acceptance-testing.md` | `maintainer-side-requesting-qa`, `tester-side-the-acceptance-run`, `the-qa-report`, `outcomes-and-labels`, `agent-routing-pull-requests` |
| `qa/handbook/exploratory-testing.md` | `what-a-charter-is`, `running-a-session`, `heuristics`, `pitwall-charter-list` |
| `qa/handbook/regression-and-release.md` | `qa-smoke-set`, `full-journey-harness`, `release-candidate-pass`, `sign-off-report` |
| `qa/handbook/test-automation.md` | `where-tests-go`, `find-then-fix`, `pre-pr-routine`, `opening-the-pull-request`, `reading-ci-failures` |
| `qa/handbook/evidence-standard.md` | `what-counts-as-evidence`, `what-does-not-count`, `saving-output`, `attaching-evidence` |
| `qa/handbook/live-testing-safety.md` | `key-handling`, `spend-ceilings`, `before-every-live-step`, `cleanup-check`, `surprise-charges` |
| `qa/handbook/maintaining-the-packet.md` | `packet-parity`, `new-surfaces`, `packet-bugs`, `re-verifying-commands` |
| Every lesson | `goal`, `you-will-learn`, `before-you-start`, `steps`, `checkpoint`, `done-when`, `record-in-progress`, `next` |
| Every mission | `why-this-matters`, `sources`, `safety`, `setup`, `steps`, `what-counts-as-a-finding`, `done-when`, `record-in-progress` |
| Every card | `in-one-sentence`, `why-it-matters-when-testing-pitwall`, `try-it`, `common-confusions`, `check-yourself`, `go-deeper` |

Anchors outside `qa/` that lessons use (all exist on `71885b9`):

- `README.md`: `#quick-start`, `#architecture`, `#configuration`, `#security-and-trust-model`,
  `#testing-and-quality`
- `CONTRIBUTING.md`: `#dev-environment`, `#running-tests`, `#quality-gates`, `#definition-of-done`,
  `#pr-process`
- `docs/sdlc/18-cli.md`: `#3-command-inventory`, `#exit-codes`, `#6-failure-modes--error-types`,
  `#guardrails-status--guardrails-preview`
- `docs/sdlc/02-api-rest.md`: `#3-route-inventory`, `#6-failure-modes--error-types`
- `docs/sdlc/17-testing-strategy.md`: `#1-markers-and-isolation`, `#2-verification-tracks`,
  `#3-local-infrastructure`
- `docs/operator/install-acceptance-checklist.md`: `#prerequisites`, `#step-1--fresh-clone`
  through `#step-11--teardown`, `#launch-gate`
- `docs/operator/troubleshooting.md`: `#orphaned-pods-a-pod-with-no-working-owner`
- `docs/operator/personal-serving.md`: `#what-pitwall-setup-changes-on-your-machine`

## Execution Order and Lanes

1. **Task 1** runs alone on `docs/qa-onboarding` and commits the spec, this plan, and the skeleton.
2. **Tasks 2–17** are parallel lanes. Each lane:
   - gets a worktree `~/git/pitwall-qa-lane-<ID>` on branch `qa-lane/<ID>`, created from
     the Task 1 commit;
   - runs `uv sync --frozen --extra dev` in its worktree;
   - is dispatched with `~/.claude/templates/lane-prompt.md`, using the task's **Lane** block for
     the ID, owned files, and validation;
   - writes its report to `~/git/pitwall-qa-lanes/<ID>/report.md`.

   Lanes are sized to fit one codex-shim dispatch (1140 s ceiling).
3. **Task 18** integrates the lanes in task order (rebase, then fast-forward).
4. **Tasks 19–23** run in order on `docs/qa-onboarding`.

The lane template's default "Do not touch `docs/`" line is replaced by each lane's explicit
ownership list. Lane 17 owns files under `docs/`.

---

### Task 1: Commit the spec and plan, then the packet skeleton

**Files:**
- Commit: `docs/superpowers/specs/2026-09-11-qa-onboarding-design.md`,
  `docs/superpowers/plans/2026-09-11-qa-onboarding.md`
- Create: `qa/.gitignore` and all 99 `qa/**/*.md` files (headings only; templates get one comment
  line)

**Interfaces:**
- Consumes: nothing.
- Produces: every path in the File Map and every anchor in the Anchor Registry. Tasks 2–16
  overwrite these files with content.

- [ ] **Step 1: Confirm the base and a clean tree**

Run: `cd ~/git/pitwall-qa-onboarding && git status --short && git log --oneline -1`
Expected: only the untracked spec and plan are listed; HEAD is `71885b9` or a newer `origin/main`
commit. If `origin/main` moved, rebase first:
`git fetch origin && git rebase origin/main`.

- [ ] **Step 2: Commit the spec and plan**

```bash
git add docs/superpowers/specs/2026-09-11-qa-onboarding-design.md docs/superpowers/plans/2026-09-11-qa-onboarding.md
git commit -s -m "docs(plans): design and implementation plan for the agent-guided QA program"
```

Expected: one commit with a `Signed-off-by:` trailer (`git log -1 --format='%(trailers:key=Signed-off-by)'`).

- [ ] **Step 3: Write the skeleton generator to `/tmp/qa_skeleton.py`**

```python
"""Create the qa/ packet skeleton: every file, its H1, and its link-target headings."""

from __future__ import annotations

from pathlib import Path

ROOT = Path("qa")
LESSON = [
    "Goal",
    "You will learn",
    "Before you start",
    "Steps",
    "Checkpoint",
    "Done when",
    "Record in progress",
    "Next",
]
MISSION = [
    "Why this matters",
    "Sources",
    "Safety",
    "Setup",
    "Steps",
    "What counts as a finding",
    "Done when",
    "Record in progress",
]
CARD = [
    "In one sentence",
    "Why it matters when testing Pitwall",
    "Try it",
    "Common confusions",
    "Check yourself",
    "Go deeper",
]

DOCS = {
    "START-HERE.md": (
        "Start here — QA coach instructions",
        [
            "1. Read these files first, in order",
            "2. If the progress file does not exist",
            "3. What to open when the tester says",
            "4. Where things are",
            "5. Facts you need every session",
        ],
    ),
    "README.md": (
        "QA program",
        [
            "What this is",
            "For the tester: how to start",
            "For the maintainer",
            "How it is organized",
            "Keeping it current",
        ],
    ),
    "maintainer-setup.md": (
        "Maintainer setup",
        [
            "1. GitHub access",
            "2. Labels",
            "3. Agent access",
            "4. The kickoff message",
            "5. Weekly check-in",
            "6. Requesting QA on a pull request",
            "7. Unlocking tier 5",
        ],
    ),
    "coach/rules.md": (
        "Coach rules",
        [
            "Safety",
            "Modes",
            "Honesty and evidence",
            "What you may change",
            "GitHub and privacy",
            "Staying on track",
            "Urgent findings",
        ],
    ),
    "coach/session-routine.md": (
        "Session routine",
        ["Starting a session", "The work queue", "During a session", "Ending a session"],
    ),
    "coach/teaching-method.md": (
        "Teaching method",
        [
            "The teaching loop",
            "One step at a time",
            "New words",
            "When the tester is stuck",
            "Checking understanding",
            "Using the tester's experience",
            "Letting the tester decide",
            "Celebrating real results",
        ],
    ),
    "coach/agent-guide.md": (
        "Agent guide",
        [
            "Which agent for which job",
            "Switching agents",
            "Checking what an agent tells you",
            "Approval settings",
            "Harness tips",
        ],
    ),
    "missions/README.md": (
        "Missions",
        ["The tiers", "Unlocking a tier", "How the coach picks the next item", "Mission list"],
    ),
    "concepts/README.md": ("Concept cards", ["Computer basics", "Pitwall", "QA"]),
    "handbook/README.md": ("QA handbook", ["Documents", "Who reads what"]),
    "handbook/qa-role.md": (
        "The QA role",
        ["What QA owns", "What the maintainer owns", "Weekly check-in"],
    ),
    "handbook/bug-reports.md": (
        "Bug reports",
        [
            "The bug-report checklist",
            "Product bug or doc bug",
            "Duplicate search",
            "Writing the report",
            "Filing with gh",
            "After filing",
        ],
    ),
    "handbook/triage-and-labels.md": (
        "Triage and labels",
        ["Severity scale", "Labels", "Issue lifecycle", "Queries the coach uses"],
    ),
    "handbook/acceptance-testing.md": (
        "Acceptance testing",
        [
            "Maintainer side: requesting QA",
            "Tester side: the acceptance run",
            "The QA report",
            "Outcomes and labels",
            "Agent Routing pull requests",
        ],
    ),
    "handbook/exploratory-testing.md": (
        "Exploratory testing",
        ["What a charter is", "Running a session", "Heuristics", "Pitwall charter list"],
    ),
    "handbook/regression-and-release.md": (
        "Regression and release",
        ["QA smoke set", "Full journey harness", "Release candidate pass", "Sign-off report"],
    ),
    "handbook/test-automation.md": (
        "Test automation",
        [
            "Where tests go",
            "Find then fix",
            "Pre-PR routine",
            "Opening the pull request",
            "Reading CI failures",
        ],
    ),
    "handbook/evidence-standard.md": (
        "Evidence standard",
        ["What counts as evidence", "What does not count", "Saving output", "Attaching evidence"],
    ),
    "handbook/live-testing-safety.md": (
        "Live testing safety",
        [
            "Key handling",
            "Spend ceilings",
            "Before every live step",
            "Cleanup check",
            "Surprise charges",
        ],
    ),
    "handbook/maintaining-the-packet.md": (
        "Maintaining the packet",
        ["Packet parity", "New surfaces", "Packet bugs", "Re-verifying commands"],
    ),
}

BOOTCAMP = [
    ("B0-welcome", "B0 — Welcome"),
    ("B1-terminal-basics", "B1 — Terminal basics"),
    ("B2-install-and-clone", "B2 — Install your tools and check your clone"),
    ("B3-fresh-eyes-readme", "B3 — Fresh-eyes README test"),
    ("B4-git-and-github", "B4 — Git and GitHub"),
    ("B5-pitwall-tour", "B5 — Pitwall tour"),
    ("B6-qa-fundamentals", "B6 — QA fundamentals and your first issues"),
]

MISSIONS = {
    "tier1-docs": [
        ("T1-01-contributing-walkthrough", "T1-01 — CONTRIBUTING walkthrough"),
        ("T1-02-install-checklist-part-1", "T1-02 — Install checklist, part 1"),
        ("T1-03-install-checklist-part-2", "T1-03 — Install checklist, part 2"),
        ("T1-04-cli-help-vs-reference", "T1-04 — CLI help vs CLI reference"),
        ("T1-05-journey-catalog-vs-harness", "T1-05 — Journey catalog vs harness"),
        ("T1-06-configuration-docs", "T1-06 — Configuration docs cross-check"),
        ("T1-07-troubleshooting-messages", "T1-07 — Troubleshooting guide messages"),
    ],
    "tier2-manual": [
        ("T2-01-api-in-the-browser", "T2-01 — The REST API in a browser"),
        ("T2-02-api-with-curl", "T2-02 — The REST API with curl"),
        ("T2-03-api-locks", "T2-03 — The API's locks: auth, admin, rate limit"),
        ("T2-04-money-safety", "T2-04 — Money safety: budget gate, proxy, kill switch, guardrails"),
        ("T2-05-cli-off-the-happy-path", "T2-05 — The CLI off the happy path"),
        ("T2-06-database-guards", "T2-06 — Database lifecycle guards"),
        ("T2-07-tui-tour", "T2-07 — TUI tour and exploratory pass"),
        ("T2-08-mcp-agent-client", "T2-08 — Pitwall as an agent tool (MCP)"),
        ("T2-09-background-services", "T2-09 — Background services"),
        ("T2-10-journey-harness", "T2-10 — Journey harness vs your manual results"),
        ("T2-11-agent-routing-checks", "T2-11 — Agent Routing component checks"),
        ("T2-12-exploratory-session", "T2-12 — Exploratory session"),
    ],
    "tier3-acceptance": [
        ("T3-01-practice-acceptance", "T3-01 — Practice acceptance on a merged pull request"),
        ("T3-02-dependency-update-pr", "T3-02 — QA a dependency update pull request"),
        ("T3-03-needs-qa-pr", "T3-03 — Acceptance-test a needs-qa pull request"),
        ("T3-04-verify-fixes", "T3-04 — Verify fixes for issues you filed"),
        ("T3-05-release-candidate-pass", "T3-05 — Release candidate regression pass"),
    ],
    "tier4-automation": [
        ("T4-01-reading-the-test-suite", "T4-01 — Reading and running the test suite"),
        ("T4-02-pre-pr-routine", "T4-02 — The pre-PR routine and reading CI"),
        ("T4-03-first-regression-test", "T4-03 — Your first regression test"),
        ("T4-04-cli-contract-tests", "T4-04 — CLI contract tests"),
        ("T4-05-api-contract-tests", "T4-05 — API contract tests"),
        ("T4-06-journey-harness-assertions", "T4-06 — Journey harness assertions"),
        ("T4-07-property-tests", "T4-07 — Property tests with Hypothesis"),
    ],
    "tier5-live": [
        ("T5-00-live-safety-briefing", "T5-00 — Live safety briefing and key handling"),
        ("T5-01-read-only-live-checks", "T5-01 — Read-only live checks"),
        ("T5-02-personal-serving-smoke", "T5-02 — Personal serving smoke"),
        ("T5-03-pod-lease-lifecycle", "T5-03 — Pod lease lifecycle (L3)"),
        ("T5-04-live-audit", "T5-04 — Live audit (L4)"),
        ("T5-05-serverless-endpoints", "T5-05 — Serverless endpoints (L1, L2)"),
    ],
}

CARDS = [
    ("terminal-and-shell", "The terminal and the shell"),
    ("files-paths-and-permissions", "Files, paths, and permissions"),
    ("environment-variables", "Environment variables"),
    ("processes-ports-and-localhost", "Processes, ports, and localhost"),
    ("docker-and-compose", "Docker and Docker Compose"),
    ("git-basics", "Git basics"),
    ("github-issues-and-prs", "GitHub issues and pull requests"),
    ("http-and-status-codes", "HTTP and status codes"),
    ("json-and-apis", "JSON and APIs"),
    ("logs-tracebacks-and-exit-codes", "Logs, tracebacks, and exit codes"),
    ("python-uv-and-venvs", "Python, uv, and virtual environments"),
    ("pitwall-in-one-page", "Pitwall in one page"),
    ("capabilities-and-providers", "Capabilities and providers"),
    ("dry-run-and-hermetic", "Dry runs and hermetic testing"),
    ("routing-and-plans", "Routing and route plans"),
    ("cost-budget-and-guardrails", "Cost, budget, and guardrails"),
    ("pods-leases-and-serving", "Pods, leases, and serving"),
    ("kill-switch", "The kill switch"),
    ("background-services", "Background services"),
    ("mcp-and-agent-clients", "MCP and agent clients"),
    ("the-tui", "The TUI (terminal dashboard)"),
    ("expected-vs-actual", "Expected vs actual"),
    ("severity-and-priority", "Severity and priority"),
    ("reproducing-a-bug", "Reproducing a bug"),
    ("exploratory-testing", "Exploratory testing"),
    ("regression-and-smoke-tests", "Regression and smoke tests"),
    ("test-lanes-and-markers", "Test lanes and markers"),
    ("pytest-fixtures-and-fakes", "pytest, fixtures, and fakes"),
    ("flaky-tests", "Flaky tests"),
]

TEMPLATES = [
    "progress",
    "bug-report",
    "qa-report",
    "exploratory-session",
    "weekly-checkin",
    "tier-check",
]


def write(path: Path, text: str) -> None:
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def skeleton(title: str, sections: list[str]) -> str:
    lines = [f"# {title}", ""]
    for section in sections:
        lines += [f"## {section}", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    write(ROOT / ".gitignore", ".work/\n")
    for rel, (title, sections) in DOCS.items():
        write(ROOT / rel, skeleton(title, sections))
    for slug, title in BOOTCAMP:
        write(ROOT / "bootcamp" / f"{slug}.md", skeleton(title, LESSON))
    for folder, items in MISSIONS.items():
        for slug, title in items:
            write(ROOT / "missions" / folder / f"{slug}.md", skeleton(title, MISSION))
    for slug, title in CARDS:
        write(ROOT / "concepts" / f"{slug}.md", skeleton(title, CARD))
    for name in TEMPLATES:
        write(ROOT / "templates" / f"{name}.md", "<!-- Template content arrives in Task 3. -->\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run it and check the inventory**

Run: `uv run --frozen python /tmp/qa_skeleton.py && find qa -name '*.md' | wc -l && cat qa/.gitignore`
Expected: `99`, then `.work/`.

- [ ] **Step 5: Confirm `.work/` is ignored**

Run: `mkdir -p qa/.work && touch qa/.work/probe && git check-ignore qa/.work/probe && rm -r qa/.work`
Expected: prints `qa/.work/probe`.

- [ ] **Step 6: Run the gates**

Run VC-links, VC-policy with paths `qa`, and VC-secrets.
Expected: all three pass as the Validation Commands table describes. The skeleton has no links, so
VC-links counts more files and passes.

- [ ] **Step 7: Commit**

```bash
git add qa
git commit -s -m "docs(qa): skeleton for the agent-guided QA program"
```

Expected: `git show --stat HEAD | tail -1` reports `100 files changed`.

- [ ] **Step 8: Create the lane worktrees**

```bash
mkdir -p ~/git/pitwall-qa-lanes
for id in 02-coach 03-templates 04-handbook-a 05-handbook-b 06-cards-a 07-cards-b 08-cards-c \
          09-bootcamp 10-tier1 11-tier2a 12-tier2b 13-tier3 14-tier4 15-tier5 16-front 17-repo; do
  git worktree add -b "qa-lane/$id" "~/git/pitwall-qa-lane-$id" docs/qa-onboarding
  mkdir -p "~/git/pitwall-qa-lanes/$id"
done
git worktree list | grep -c pitwall-qa-lane-
```

Expected: `16`. Run `uv sync --frozen --extra dev` inside each lane worktree before dispatching it.

---

### Task 2: Coach layer (lane `02-coach`)

**Lane:** ID `02-coach`. You own: `qa/START-HERE.md`, `qa/coach/rules.md`,
`qa/coach/session-routine.md`, `qa/coach/teaching-method.md`, `qa/coach/agent-guide.md`. Do not
touch any other file.

**Files:** Overwrite the five owned files with the exact content below, converting `[text]{target}`
to Markdown links (GC-4).

**Interfaces:**
- Consumes: skeleton paths and anchors for `templates/progress.md`, `bootcamp/B0-welcome.md`,
  `bootcamp/B2-install-and-clone.md`, `missions/README.md#how-the-coach-picks-the-next-item`,
  `handbook/bug-reports.md`, `handbook/acceptance-testing.md`,
  `handbook/evidence-standard.md`, `handbook/triage-and-labels.md#severity-scale`,
  `concepts/README.md`.
- Produces: rules R1–R14 under the anchors in the registry. Every mission's Safety line links
  `../../coach/rules.md#safety`.

- [ ] **Step 1: Write `qa/START-HERE.md`**

```markdown
# Start here — QA coach instructions

You are the **QA coach** for a beginner tester on the Pitwall project. Your job is to train the
tester and help them do real QA work. You are not here to do the work for them.

The tester opened you at the root of their Pitwall clone and typed:
`Read qa/START-HERE.md and follow it.`

## 1. Read these files first, in order

1. [Coach rules]{coach/rules.md}: the rules you must follow. Never break them.
2. [Session routine]{coach/session-routine.md}: how every session starts and ends.
3. [Teaching method]{coach/teaching-method.md}: how to teach.
4. [Agent guide]{coach/agent-guide.md}: notes about the AI agents the tester uses.
5. The tester's progress file, `qa/.work/progress.md`.

## 2. If the progress file does not exist

This is the tester's first session.

1. Create the folders `qa/.work/`, `qa/.work/evidence/`, and `qa/.work/notes/`.
2. Copy [the progress template]{templates/progress.md} to `qa/.work/progress.md`.
3. Open [B0 — Welcome]{bootcamp/B0-welcome.md} and follow it.

Git ignores `qa/.work/`. It is the tester's private space. Never delete it.

## 3. What to open when the tester says

| The tester says | Open |
| --- | --- |
| "continue", "let's go", "what's next" | `Current item` in the progress file. If it is empty, [how the coach picks the next item]{missions/README.md#how-the-coach-picks-the-next-item} |
| "work mode" or "training mode" | [Modes]{coach/rules.md#modes}, then change `Mode default` in the progress file |
| "I found a bug" or "is this a bug?" | [Bug reports]{handbook/bug-reports.md} |
| "the maintainer asked me to test pull request N" | [Acceptance testing]{handbook/acceptance-testing.md}. If tier 3 is locked, do it together in training mode and explain each step |
| "I'm stuck" | [When the tester is stuck]{coach/teaching-method.md#when-the-tester-is-stuck} |
| "what does X mean?" | [Concept cards]{concepts/README.md}. If no card fits, explain in plain words |
| "let's stop" or "end session" | [Ending a session]{coach/session-routine.md#ending-a-session} |

## 4. Where things are

- `bootcamp/`: seven foundation lessons, B0 to B6, done in order.
- `missions/`: real QA work in five tiers. Start with [the mission ladder]{missions/README.md}.
- `concepts/`: short cards that explain one idea each.
- `handbook/`: how QA works on this project.
- `templates/`: fill-in forms for progress, bug reports, QA reports, and notes.
- `qa/.work/`: the tester's progress, evidence, and drafts. Not in git.

## 5. Facts you need every session

- Run every command from the repository root unless a lesson says otherwise.
- Run Python through `uv run`. Never run bare `python`.
- The placeholder settings for local testing are the `export` lines in the
  [README Quick Start]{../README.md#quick-start}. A new terminal needs them again. Never use real
  credentials.
- Never start `pitwall` with no arguments, or `pitwall dashboard`, in a terminal that does not
  have those `export` lines. It would start `pitwall setup` ([rule R2]{coach/rules.md#safety}).
- The local test stack starts with
  `docker compose -f docker-compose.testinfra.yml up -d --wait` and stops with `make down`. It uses
  ports 5444 (PostgreSQL) and 6380 (Redis). The API uses port 8080.
- The canonical docs are the [README]{../README.md}, [CONTRIBUTING]{../CONTRIBUTING.md},
  `docs/sdlc/`, and `docs/operator/`. When a doc and real behavior disagree, that is a finding.
```

- [ ] **Step 2: Write `qa/coach/rules.md`**

```markdown
# Coach rules

These rules apply in every session, in every mode, with every agent. If a lesson or a request
conflicts with a rule, the rule wins. Stop and tell the tester why.

## Safety

**R1 — No real credentials in tiers 0–4.** Never use, ask for, create, print, or store a real
provider key, token, or endpoint secret. Use only the placeholder values in the
[README Quick Start]{../../README.md#quick-start}.

**R2 — The never-run list.** Outside tier 5, never run these and never help the tester run them:

- `pitwall setup`
- `pitwall` with no arguments, or `pitwall dashboard`, unless the README Quick Start `export`
  lines (including `DATABASE_URL`) are set in that terminal. Without them it starts
  `pitwall setup`.
- `pitwall serve` without `--dry-run`
- `pitwall status` or `pitwall stop`, except the exact `pitwall status` command in mission T1-07,
  which has no credential and reads input from `/dev/null`
- `pitwall terminate-pod`
- `pitwall warm-volume` or `pitwall register-template` without `--dry-run`
- `pitwall runpod` anything except `--help`
- `pitwall runpod-onboard apply` or `pitwall runpod-onboard resume`
- `pitwall volume-files`, `pitwall provider-ops`, and `pitwall retention run`
- any command that sets `RUNPOD_LIVE`, `PITWALL_RUN_LIVE`, or `PITWALL_RUNPOD_LIVE`, or passes
  `--run-live` or `-m live`
- `pitwall db reset` or `scripts/release/run-user-journeys.sh` against anything except the local
  test stack
- `git push` to `main`, `git push --force`, or `git clean` with `-x`
- `sudo`, except in [B2]{../bootcamp/B2-install-and-clone.md}
- `rm -rf` outside `qa/.work/` and the tester's scratch clones

`--help` on any of these is always fine. If a terminal ever asks whether to run `pitwall setup`,
answer no.

**R3 — When unsure, stop.** Before any command that could spend money, contact a cloud provider,
delete something outside `qa/.work/` or the local test stack, or post to GitHub, stop and ask the
tester. These read-only commands are always fine: `git status`, `git log`, `git diff`, `ls`, `cat`
on repository files, `docker ps`, `uv run pitwall --help`, and `gh` commands that `list` or `view`.

## Modes

**R4 — Training mode is the default.** The tester types every command that changes something. You
explain first, say what you expect, then check the result. You may run read-only commands yourself
to confirm.

**R5 — Work mode only when the tester asks.** In work mode you may run allowed commands yourself.
Before each one, say what you will run and why. Never switch off the agent's approval prompts.
Bootcamp and tier 5 are always training mode.

## Honesty and evidence

**R6 — Evidence or it didn't happen.** Every "pass", "fail", "works", or "broken" names the command
that ran in this session and quotes the output that proves it. Never report a result you did not
see. See the [evidence standard]{../handbook/evidence-standard.md}.

**R7 — Bug or intended? Ask, don't guess.** If you are not sure whether something is a defect, add
it to "Questions for the maintainer" in the progress file. If it is about how the product behaves,
file a `question` issue with the tester.

## What you may change

**R8 — QA does not fix product code or docs.** In tiers 0–3, change nothing outside `qa/.work/`.
In tier 4, change test files only, on a feature branch, in a pull request. A wrong doc is filed as
an issue. Fixing it is the maintainer's call.

## GitHub and privacy

**R9 — The tester approves everything posted.** Write drafts of issues, comments, and reviews in
`qa/.work/notes/`. Show the draft. Post only after the tester says yes, using `--body-file`.

**R10 — Redact.** Never paste keys, tokens, the address of the tester's model server, personal
data, or full environment dumps into GitHub. Use made-up ids.

## Staying on track

**R11 — Stay on the page.** Follow the current lesson's steps in order. When a linked doc and the
real behavior disagree, record it as a finding. Do not quietly work around it.

**R12 — Protect the progress file.** Update `qa/.work/progress.md` at every checkpoint and at the
end of every session. Never delete `qa/.work/`.

**R13 — One test stack at a time.** Run `docker ps` before starting the test stack. The stack uses
ports 5444 and 6380, and the API uses port 8080. Stop what you started.

## Urgent findings

**R14 — Severity 1 is urgent.** If a finding involves money, secrets, or safety (see the
[severity scale]{../handbook/triage-and-labels.md#severity-scale}), stop the lesson. File it with
the tester right away and tell the tester to message the maintainer directly.
```

- [ ] **Step 3: Write `qa/coach/session-routine.md`**

```markdown
# Session routine

## Starting a session

1. Read the files listed in [Start here]{../START-HERE.md#1-read-these-files-first-in-order}.
2. Greet the tester by name from the progress file. Recap the last "Session log" entry in two
   sentences.
3. If tier 3 or higher is unlocked, check [the work queue]{#the-work-queue} first.
4. Propose one item for today: queue work first, otherwise `Current item`, otherwise the next item
   from [the mission ladder]{../missions/README.md#how-the-coach-picks-the-next-item}. Say which
   mode you will use.
5. Check only the environment this item needs. Usually that is `git status`,
   `git branch --show-current`, and `docker ps`.
6. Start a new "Session log" entry with today's date and your agent name.

## The work queue

From tier 3 on, run these read-only checks in order:

1. Severity 1 issues: `gh issue list --state open --label severity:1-critical`
2. Pull requests waiting for QA: `gh pr list --label needs-qa`
3. Fixed issues waiting for verification:
   `gh issue list --state closed --search "label:found-by-qa -label:qa-verified"`

If any list has items, suggest that work before curriculum work. The tester decides.

## During a session

- Update `Stopped at` in the progress file at each checkpoint (R12).
- Save important command output to `qa/.work/evidence/` with `tee`.
- Add raw findings to "Findings not yet filed" as soon as you see them.

## Ending a session

1. Stop what you started: the API (Ctrl-C in its terminal), and `make down` if the test stack is
   no longer needed.
2. Update the progress file: `Completed` rows, `Current item`, `Stopped at` (the exact step),
   "Findings not yet filed", "Questions for the maintainer", the session log entry, and "Coach
   notes".
3. Tell the tester in plain words what we did, what they learned, and what comes next.
4. If "Questions for the maintainer" has open items, remind the tester to bring them to the weekly
   check-in.
```

- [ ] **Step 4: Write `qa/coach/teaching-method.md`**

```markdown
# Teaching method

The tester is new to software. They are good at technical support: they troubleshoot customer
problems, follow procedures, and write ticket notes. Build on that.

## The teaching loop

For every new step:

1. **Explain** what we are about to do and why, in two to four plain sentences.
2. **Show** the command and say what you expect to happen.
3. **The tester does it** (training mode) and shares the output.
4. **Compare** the real output with what you expected. A difference is either a mistake to fix or a
   finding to record.
5. **Recap** in one sentence what the tester just learned.

## One step at a time

Send one step per message. Never send a list of ten commands. Wait for the tester's result before
the next step.

## New words

Define every technical word the first time you use it, or link its card in
[concepts]{../concepts/README.md}. Add each word you taught to "Coach notes" in the progress file
so you don't teach it twice.

## When the tester is stuck

Use the hint ladder, one rung at a time:

1. Ask a question that points the right way ("What does the error say is missing?").
2. Give a specific hint ("Compare your variable names with the README Quick Start").
3. Show the answer, then have the tester do it themselves.

Never make the tester feel slow. Getting stuck is normal. It is where the learning happens.

## Checking understanding

End every lesson section with its checkpoint question. If the answer is wrong, explain again in a
different way and note it under "Needs more practice" in the progress file. Come back to it next
session.

## Using the tester's experience

| New idea | What it is like in technical support |
| --- | --- |
| Reproducing a bug | Getting the customer's problem to happen again on the bench |
| A bug report | A ticket note someone else can act on without calling you |
| Regression | "It worked before the update. What changed?" |
| Checking the environment first | Checking the cable and the power before the motherboard |
| Severity | A dead drive with no backup versus a loose case screw |
| Evidence | The error code and a photo in the ticket, not "customer says it's broken" |

## Letting the tester decide

Ask for the tester's answer first: the severity, the title, the expected result, whether it is a
bug. Then coach. The goal is their judgment, not yours.

## Celebrating real results

Point out real milestones when they happen: the first issue filed, the first QA report posted, the
first test merged. These are real contributions to the project.
```

- [ ] **Step 5: Write `qa/coach/agent-guide.md`**

```markdown
# Agent guide

The tester uses three AI agents: Claude Code, Codex, and opencode with a self-hosted Qwen model.
This packet works with all three.

## Which agent for which job

| Job | Best agent |
| --- | --- |
| Bootcamp lessons, concept cards, drills | Any. Qwen through opencode is fine |
| Running checklists and journeys (tiers 1 and 2) | Any |
| Reading large pull-request diffs (tier 3) | Claude Code or Codex |
| Writing tests (tier 4) | Claude Code or Codex |
| A step where answers keep changing or look wrong | Switch to Claude Code or Codex |

If you are the Qwen model and a step asks you to write or change Python code, tell the tester this
job is better done in Claude Code or Codex.

## Switching agents

Everything important is in `qa/.work/progress.md`, so any agent can pick up where another stopped.
To switch, the tester starts the other agent at the repository root and types the kickoff
sentence again: `Read qa/START-HERE.md and follow it.`

## Checking what an agent tells you

Agents can be confidently wrong, including you. Teach the tester to check:

- Re-run the command and read the output.
- Run `git status` and `git diff` to see what really changed.
- Be suspicious of a command that is not in a lesson, a linked doc, or `--help` output.
- A claim without output is not evidence (R6).

## Approval settings

Keep command approval prompts on in every agent. Never use modes that skip approvals, such as
Claude Code's bypass-permissions mode or Codex's full-auto and dangerous-bypass options.

## Harness tips

- **Claude Code:** the tester can type `!` followed by a command to run it inside the session, so
  you see the output directly.
- **Every agent:** in training mode the tester can run commands in a second terminal window and
  paste the output into the chat.
- **Start every session at the repository root,** where the `qa/` folder is.
```

- [ ] **Step 6: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/START-HERE.md qa/coach/*.md`
Expected: `shape check: 5 ok, 0 with problems`.
Run VC-links, VC-policy (paths `qa/START-HERE.md qa/coach`), and VC-secrets. Expected: pass.
Run: `wc -l qa/START-HERE.md`. Expected: 100 or fewer.

- [ ] **Step 7: Commit**

```bash
git add qa/START-HERE.md qa/coach
git commit -s -m "docs(qa): coach entry point, rules, session routine, teaching method, agent guide"
```

---

### Task 3: Templates (lane `03-templates`)

**Lane:** ID `03-templates`. You own: `qa/templates/progress.md`, `qa/templates/bug-report.md`,
`qa/templates/qa-report.md`, `qa/templates/exploratory-session.md`,
`qa/templates/weekly-checkin.md`, `qa/templates/tier-check.md`. Do not touch any other file.

**Files:** Overwrite the six owned files with the exact content below. Templates are copied into
`qa/.work/`, so they contain no links, except `tier-check.md`, which is used in place.

**Interfaces:**
- Consumes: concept-card paths (Task 1) for `tier-check.md` answer links.
- Produces: the progress-file structure every coach file refers to (`Current item`, `Stopped at`,
  `Mode default`, `Unlocked tiers`, "Findings not yet filed", "Questions for the maintainer",
  "Session log", "Coach notes"), and the body formats used by `gh issue create` and
  `gh pr review`.

- [ ] **Step 1: Write `qa/templates/progress.md`**

```markdown
# QA progress — (tester's name)

## About me

- Name:
- Maintainer (who I report to, and how to reach them):
- Background:
- Agents I use:
- Machine (Linux distribution, Docker version, uv version):
- How I learn best (the coach fills this in):

## Status

- Current tier: 0 (bootcamp)
- Unlocked tiers: 0
- Mode default: training
- Current item: B0
- Stopped at:

## Completed

| Item | Date | Output (issue, pull request, or report link) | Notes |
| --- | --- | --- | --- |

## Findings not yet filed

## Questions for the maintainer

## Session log

## Coach notes

- Terms taught:
- Easy for the tester:
- Needs more practice:
```

- [ ] **Step 2: Write `qa/templates/bug-report.md`**

The headings match `.github/ISSUE_TEMPLATE/bug_report.md`, plus Severity, Found During, and
Evidence. (This block uses a four-backtick fence because the template contains a code fence.)

````markdown
<!--
Bug report draft. Copy this file to qa/.work/notes/<short-name>.md and fill it in.
Show the draft to the tester. After they approve, delete this comment and post:

  gh issue create --title "<specific title>" --body-file qa/.work/notes/<short-name>.md \
    --label bug --label found-by-qa --label severity:3-medium

Use --label documentation instead of --label bug when the problem is in a doc.
Pick exactly one severity label.
-->

## What Happened

## Expected Behavior

## Repro Steps

1.

## `pitwall` Command / Route / MCP Tool Used

## Provider / Capability Context

Synthetic ids only. Never paste a credential.

## Logs

```text
```

## Environment

- OS:
- Python:
- pitwall version (`uv run pitwall --version`):
- Deployment mode (local / FastAPI server / MCP / CLI):
- Commit tested (`git rev-parse --short HEAD`):

## Severity

(1-critical, 2-high, 3-medium, or 4-low) — one sentence explaining why, using the severity scale.

## Found During

(lesson or mission id, and the journey id if there is one)

## Evidence

- Command:
- Exit code:
- Output excerpt:
````

- [ ] **Step 3: Write `qa/templates/qa-report.md`**

```markdown
<!--
QA report for a pull request. Copy to qa/.work/notes/pr-<N>-qa.md and fill it in.
After the tester approves, delete this comment and post with one of:

  gh pr review <N> --approve --body-file qa/.work/notes/pr-<N>-qa.md          (pass)
  gh pr review <N> --request-changes --body-file qa/.work/notes/pr-<N>-qa.md  (fail)
  gh pr review <N> --comment --body-file qa/.work/notes/pr-<N>-qa.md          (pass with issues)

Then swap the label:
  gh pr edit <N> --remove-label needs-qa --add-label qa-passed   (or qa-failed)
-->

## QA report — pull request #<N>

**Verdict:** pass | fail | pass with issues
**Commit tested:** (from `git rev-parse --short HEAD`)
**Environment:** (OS), Python (version), pitwall (from `uv run pitwall --version`)

### Acceptance criteria

| # | Criterion | Command | Result | Evidence |
| --- | --- | --- | --- | --- |
| 1 | | | | |

### Exploratory notes

### Regression smoke

| Check | Result |
| --- | --- |
| `make test` | |
| Integration lane (`make test-int` with the test stack up) | |
| README Quick Start dry-run inference | |
| `/docs` loads | |
| TUI opens and every view is reachable | |
| MCP stdio lists tools | |

### Issues filed

### Questions for the maintainer

### Skipped, and why
```

- [ ] **Step 4: Write `qa/templates/exploratory-session.md`**

```markdown
<!-- Exploratory session notes. Copy to qa/.work/notes/explore-<date>-<topic>.md. -->

## Charter

Explore (area) with (resources or technique) to discover (kind of problem).

## Setup

- Commit tested:
- Test stack running: yes / no
- Starting state:

## Notes log

| When | What I did | What happened | Worth a finding? |
| --- | --- | --- | --- |

## Findings

## Questions for the maintainer

## Areas not reached

## New charter ideas
```

- [ ] **Step 5: Write `qa/templates/weekly-checkin.md`**

```markdown
<!-- Weekly check-in. Copy to qa/.work/notes/checkin-<date>.md and bring it to the maintainer. -->

## Check-in — (date)

### Issues filed

| Issue | Title | Severity | Status |
| --- | --- | --- | --- |

### QA reports posted

| Pull request | Verdict |
| --- | --- |

### Tests merged

### Tier progress

- Current tier:
- Completed since last check-in:
- Ready to unlock the next tier? (yes or no, and why)

### Questions for the maintainer

### Blockers

### What I learned
```

- [ ] **Step 6: Write `qa/templates/tier-check.md`**

```markdown
# Tier check

The coach asks three to five questions from the tier being unlocked. The tester passes when every
answer is right after at most one hint each. Record the result and date in the progress file. The
answer key is for the coach.

## Tier 1 (after the bootcamp)

1. What is the difference between training mode and work mode?
   Answer: in training mode the tester types every command that changes something; in work mode
   the coach may run allowed commands after saying what and why.
2. Name three commands on the never-run list and why each is risky.
   Answer: any three from rule R2 with a sensible reason (spends money, changes the machine,
   destroys data, publishes).
3. What goes under "Expected Behavior" in a bug report?
   Answer: what should have happened, ideally quoting the doc that says so.
4. What does a dry run mean in Pitwall?
   Answer: routing and cost are worked out, but nothing is sent to a paid provider
   ([card]{../concepts/dry-run-and-hermetic.md}).
5. What is severity 1, and what do you do when you find one?
   Answer: money, secrets, or safety; stop, file it, and message the maintainer directly.

## Tier 2

1. What makes repro steps good?
   Answer: numbered, start from a clean state, exact commands, one action per step
   ([card]{../concepts/reproducing-a-bug.md}).
2. Doc bug or product bug: how do you decide, and which label goes with each?
   Answer: if the product does the right thing and the doc is wrong, `documentation`; otherwise
   `bug`.
3. What do 401, 403, 404, and 422 mean?
   Answer: no or bad credential; valid credential without permission; not found; request body
   invalid ([card]{../concepts/http-and-status-codes.md}).
4. How do you start and stop the local test stack?
   Answer: `docker compose -f docker-compose.testinfra.yml up -d --wait`; `make down`.
5. Why search for duplicates before filing?
   Answer: so one problem has one issue and the maintainer's time goes to fixing, not sorting.

## Tier 3

1. What does "hermetic" mean, and why must the fast tests be hermetic?
   Answer: no real outside services; so they are safe, free, and repeatable anywhere.
2. What does an exploratory charter contain?
   Answer: an area, the resources or technique, and the kind of problem to look for.
3. Why are the README Quick Start values fake?
   Answer: local testing must never use or spend with real credentials.
4. What is the budget gate, and which status code shows it working?
   Answer: it refuses spend over the budget before any provider call; 402.
5. Why must the journey harness only run against the local test stack?
   Answer: it resets the database it is pointed at.

## Tier 4

1. Walk through an acceptance run for a `needs-qa` pull request.
   Answer: list, view the QA notes, check out, sync, baseline tests, criteria with evidence,
   explore, smoke, draft, approve, post, swap labels, file issues.
2. Which review action and label go with pass, fail, and pass with issues?
   Answer: approve and `qa-passed`; request changes and `qa-failed`; comment and `qa-passed`
   with issues linked.
3. What is a regression test, and how do you prove it catches the bug?
   Answer: a test that fails if the bug comes back; undo the fix locally and watch it fail.
4. What does `git commit -s` add, and why does this project need it?
   Answer: a `Signed-off-by:` line certifying you may contribute the change; CI requires it.
5. How do you read a failing CI job with `gh`?
   Answer: `gh pr checks <N>`, then `gh run view <run-id> --log-failed`.

## Tier 5

1. Where does the live key live, and what must never happen to it?
   Answer: in a `chmod 600` file outside the repository; it is never printed, pasted, or
   committed.
2. What are the three spend ceilings?
   Answer: the dedicated account's prepaid balance, `PITWALL_MONTHLY_BUDGET_USD`, and each
   launch's TTL and maximum price.
3. What do you check before and after every live step?
   Answer: what is already running and the balance before; nothing left running after.
4. What is an orphaned pod, and how do you find one?
   Answer: a pod with no working owner; see the troubleshooting guide's orphaned-pods section.
5. What do you do about a charge you did not expect?
   Answer: stop, treat it as severity 1, and message the maintainer.
```

- [ ] **Step 7: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/templates/*.md`
Expected: `shape check: 6 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/templates`), and VC-secrets. Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add qa/templates
git commit -s -m "docs(qa): progress, bug report, QA report, session, check-in, and tier-check templates"
```

---

### Task 4: Handbook, part A (lane `04-handbook-a`)

**Lane:** ID `04-handbook-a`. You own: `qa/handbook/README.md`, `qa/handbook/qa-role.md`,
`qa/handbook/bug-reports.md`, `qa/handbook/triage-and-labels.md`,
`qa/handbook/acceptance-testing.md`, `qa/handbook/evidence-standard.md`. Do not touch any other
file.

**Interfaces:**
- Consumes: skeleton paths for templates, concept cards, and Task 5's handbook files.
- Produces: the severity scale, label set, lifecycle, coach queries, and acceptance loop that
  missions and coach files link to (registry anchors).

- [ ] **Step 1: Write `qa/handbook/README.md`**

```markdown
# QA handbook

How QA works on Pitwall: the shared process between the tester and the maintainer.

## Documents

| Document | What it covers |
| --- | --- |
| [The QA role]{qa-role.md} | What QA owns, what the maintainer owns, the weekly check-in |
| [Bug reports]{bug-reports.md} | The checklist, doc bug or product bug, duplicates, filing with `gh` |
| [Triage and labels]{triage-and-labels.md} | Severity scale, labels, issue lifecycle, queue queries |
| [Acceptance testing]{acceptance-testing.md} | How the maintainer asks for QA and how the tester runs it |
| [Evidence standard]{evidence-standard.md} | What counts as proof |
| [Exploratory testing]{exploratory-testing.md} | Charters, heuristics, and the Pitwall charter list |
| [Regression and release]{regression-and-release.md} | The QA smoke set, the journey harness, release candidates |
| [Test automation]{test-automation.md} | Writing tests and opening test pull requests |
| [Live testing safety]{live-testing-safety.md} | Tier 5 rules for real credentials and spend |
| [Maintaining the packet]{maintaining-the-packet.md} | Keeping this folder correct |

## Who reads what

- The coach reads a document when a lesson or mission links to it.
- The maintainer reads [acceptance testing]{acceptance-testing.md#maintainer-side-requesting-qa},
  [triage and labels]{triage-and-labels.md}, and [maintaining the packet]{maintaining-the-packet.md}.
- The tester can read any of it at any time.
```

- [ ] **Step 2: Write `qa/handbook/qa-role.md`**

```markdown
# The QA role

The tester is Pitwall's QA function. QA finds problems before users do and proves that things
work, with evidence.

## What QA owns

- Finding defects and reporting them with evidence ([bug reports]{bug-reports.md}).
- Choosing each finding's severity ([severity scale]{triage-and-labels.md#severity-scale}).
- Acceptance verdicts on pull requests labeled `needs-qa` ([acceptance testing]{acceptance-testing.md}).
- Verifying fixes for issues QA filed, then adding `qa-verified` or reopening.
- Regression passes and release-candidate passes ([regression and release]{regression-and-release.md}).
- Test contributions in tier 4 ([test automation]{test-automation.md}).
- Reporting defects in this packet with the `qa-packet` label.

## What the maintainer owns

- Priority: which issues get fixed first.
- Fixes, merges, and releases.
- Answers to `question` issues and to "Questions for the maintainer".
- Tier unlocks, and the live key for tier 5.

## Weekly check-in

The tester fills in the weekly check-in template (`qa/templates/weekly-checkin.md`) and brings it.
The agenda:

1. New `found-by-qa` issues: the maintainer triages each one (keep, or close with a reason).
2. QA reports since the last check-in.
3. Questions for the maintainer.
4. Tier progress: does the tester meet the next unlock ([unlocking a tier]{../missions/README.md#unlocking-a-tier})?
5. Blockers.
```

- [ ] **Step 3: Write `qa/handbook/bug-reports.md`**

````markdown
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
   ([evidence standard]{evidence-standard.md}).
6. **Environment** is filled in.
7. **Severity** is chosen with the [severity scale]{triage-and-labels.md#severity-scale}, with one
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
- When the fix merges, verify it ([issue lifecycle]{triage-and-labels.md#issue-lifecycle}).
````

- [ ] **Step 4: Write `qa/handbook/triage-and-labels.md`**

````markdown
# Triage and labels

## Severity scale

| Severity | Meaning | Pitwall examples |
| --- | --- | --- |
| 1 — critical | Money, secrets, or safety | Spend without passing the budget gate; a key or token in output, logs, or an error; a dry run that calls a real provider; the kill switch fails; an auth bypass |
| 2 — high | A documented core journey fails with no workaround | README Quick Start breaks; the API returns 500; the TUI crashes on start; a migration fails on a clean database |
| 3 — medium | Wrong behavior or wrong docs, with a workaround | A documented command fails but a nearby variant works; a wrong exit code; a misleading error message; a doc that disagrees with the code |
| 4 — low | Cosmetic | Typos, formatting, confusing wording, TUI alignment |

Severity is the tester's call, made with these definitions. Priority is the maintainer's call
during triage.

## Labels

| Label | Use |
| --- | --- |
| `bug` | The product does the wrong thing |
| `documentation` | A doc is wrong or unclear |
| `question` | Not sure whether it is a defect; asking the maintainer |
| `found-by-qa` | Every issue the tester files |
| `severity:1-critical`, `severity:2-high`, `severity:3-medium`, `severity:4-low` | Exactly one on every `found-by-qa` bug or documentation issue |
| `needs-qa` | A pull request is ready for acceptance testing |
| `qa-passed`, `qa-failed` | The acceptance outcome; replaces `needs-qa` |
| `qa-verified` | A closed `found-by-qa` issue whose fix the tester confirmed on `main` |
| `qa-packet` | A defect in this `qa/` folder |
| `duplicate`, `invalid`, `wontfix` | The maintainer closes an issue with one of these and a reason |

## Issue lifecycle

1. The tester files the issue with `bug` or `documentation`, plus `found-by-qa` and one severity.
2. The maintainer triages it:
   - keeps it, or closes it as `duplicate`, `invalid`, or `wontfix` with a reason
   - answers `question` issues
3. A fix pull request closes it with `Closes #N`.
4. The tester verifies the fix on `main`
   (mission T3-04, `qa/missions/tier3-acceptance/T3-04-verify-fixes.md`):
   - fixed: add `qa-verified`
   - not fixed: reopen with evidence

## Queries the coach uses

```bash
gh issue list --state open --label severity:1-critical
gh pr list --label needs-qa
gh issue list --state closed --search "label:found-by-qa -label:qa-verified"
```
````

- [ ] **Step 5: Write `qa/handbook/acceptance-testing.md`**

````markdown
# Acceptance testing

Acceptance testing checks a pull request before it merges: does it do what it says, and did it
break anything nearby? The tester's review informs the maintainer. It never blocks or allows a
merge by itself.

## Maintainer side: requesting QA

1. Fill in the pull request's `## QA notes` section:
   - what changed for users
   - the acceptance criteria, or the plan and task numbers that list them
   - how to exercise it
   - risk areas worth exploring
   - anything that needs live credentials (the tester skips these)
2. Add the `needs-qa` label: `gh pr edit <N> --add-label needs-qa`.

## Tester side: the acceptance run

1. Find work: `gh pr list --label needs-qa`.
2. Read it: `gh pr view <N>`. Read the QA notes and the linked plan tasks.
3. Get the code: `git switch main && git pull`, then `gh pr checkout <N>`.
4. Sync: `uv sync --frozen --extra dev`.
5. Baseline: `make test 2>&1 | tail -15`. If it fails, stop and report the failure first.
6. For each acceptance criterion, run the check and save evidence to `qa/.work/evidence/`.
7. Explore around the changed area for a while ([exploratory testing]{exploratory-testing.md}).
8. Run the smoke checks relevant to the change ([QA smoke set]{regression-and-release.md#qa-smoke-set}).
9. Draft the report from `qa/templates/qa-report.md` in `qa/.work/notes/pr-<N>-qa.md`.
10. The tester approves the draft (rule R9), then posts it and swaps the label ([outcomes and labels]{#outcomes-and-labels}).
11. File an issue for each defect ([bug reports]{bug-reports.md}) and list the issues in the report.
12. Go back: `git switch main && uv sync --frozen --extra dev`.

## The QA report

The report uses `qa/templates/qa-report.md`:

- the verdict
- the commit tested
- the environment
- one row per acceptance criterion, each with a command, a result, and evidence
- exploratory notes
- the regression smoke results
- the issues filed
- questions for the maintainer
- anything skipped, and why

## Outcomes and labels

| Verdict | Review command | Label change |
| --- | --- | --- |
| Pass | `gh pr review <N> --approve --body-file <report>` | `gh pr edit <N> --remove-label needs-qa --add-label qa-passed` |
| Pass with issues | `gh pr review <N> --comment --body-file <report>` | `gh pr edit <N> --remove-label needs-qa --add-label qa-passed` |
| Fail | `gh pr review <N> --request-changes --body-file <report>` | `gh pr edit <N> --remove-label needs-qa --add-label qa-failed` |

A pull request without `needs-qa` (for example a Dependabot update) gets only the outcome label.

## Agent Routing pull requests

`packages/agent-routing` is its own project with its own environment. For a pull request that
changes it, set it up the way its contributing guide says (`packages/agent-routing/CONTRIBUTING.md`):

```bash
cd packages/agent-routing
pitwall_uv() { uvx --isolated --from 'uv==0.11.19' uv "$@"; }
pitwall_uv sync --frozen --group dev --python 3.14.7
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -4
```

Expected: the last lines show `OK` (skipped tests are fine). Run `doctor` only with a temporary
home, as in mission T2-11.
````

- [ ] **Step 6: Write `qa/handbook/evidence-standard.md`**

````markdown
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
````

- [ ] **Step 7: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/handbook/README.md qa/handbook/qa-role.md qa/handbook/bug-reports.md qa/handbook/triage-and-labels.md qa/handbook/acceptance-testing.md qa/handbook/evidence-standard.md`
Expected: `shape check: 6 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/handbook`), and VC-secrets. Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add qa/handbook/README.md qa/handbook/qa-role.md qa/handbook/bug-reports.md \
  qa/handbook/triage-and-labels.md qa/handbook/acceptance-testing.md qa/handbook/evidence-standard.md
git commit -s -m "docs(qa): handbook role, bug reports, triage and labels, acceptance, evidence"
```

---

### Task 5: Handbook, part B (lane `05-handbook-b`)

**Lane:** ID `05-handbook-b`. You own: `qa/handbook/exploratory-testing.md`,
`qa/handbook/regression-and-release.md`, `qa/handbook/test-automation.md`,
`qa/handbook/live-testing-safety.md`, `qa/handbook/maintaining-the-packet.md`. Do not touch any
other file.

**Interfaces:**
- Consumes: skeleton paths; anchors `CONTRIBUTING.md#definition-of-done`,
  `docs/sdlc/17-testing-strategy.md#1-markers-and-isolation`,
  `docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner`,
  `docs/operator/personal-serving.md#what-pitwall-setup-changes-on-your-machine`.
- Produces: the charter list (T2-12), the smoke set and release pass (T3-03, T3-05), test
  conventions (tier 4), live rules (tier 5), and the upkeep process.

- [ ] **Step 1: Write `qa/handbook/exploratory-testing.md`**

```markdown
# Exploratory testing

Exploratory testing is learning, designing, and testing at the same time. You don't follow a
script. You follow a charter and your curiosity, and you write down what you see.

## What a charter is

A charter is one sentence: **Explore** (area) **with** (resources or technique) **to discover**
(kind of problem). It keeps a session focused without scripting it. Example: "Explore the CLI
with missing environment variables to discover unclear error messages."

## Running a session

1. Pick a charter from the [Pitwall charter list]{#pitwall-charter-list} or write your own.
2. Copy `qa/templates/exploratory-session.md` to `qa/.work/notes/explore-<date>-<topic>.md`.
3. Set up: the commit you are testing, the test stack if needed, the starting state.
4. Explore. Write every notable thing in the notes log as you go, including what worked.
5. Stop at the end of the session, even if you are not done. Write down the areas not reached.
6. Turn findings into issues ([bug reports]{bug-reports.md}) and questions into "Questions for the
   maintainer".

All coach rules apply. Exploring never means running something on the never-run list.

## Heuristics

Ideas to try when you run out:

- **Boundaries:** empty, one, many, huge, zero, negative, very long names.
- **Wrong order:** run step 3 before step 1; call "cancel" before "start".
- **Interruption:** Ctrl-C in the middle; stop the test stack while the API runs.
- **Missing configuration:** unset one environment variable at a time.
- **Bad input types:** a number where text goes, a list where an object goes, broken JSON.
- **Unicode:** accents, emoji, right-to-left text in names and payloads.
- **Repetition:** run the same command twice. Is it idempotent (same result, no duplicates)?
- **Two terminals:** the same action from two places at once.
- **Restart:** restart a service mid-flow and check what it remembers.
- **Compare surfaces:** does the CLI, the API, and the TUI say the same thing about the same data?

## Pitwall charter list

1. Explore capability creation (`pitwall create-capability` and the admin API) with unusual names,
   versions, and classes to discover validation gaps.
2. Explore `POST /v1/inference` bodies with odd sizes, types, and empty values to discover server
   errors (500s).
3. Explore CLI commands with missing or wrong environment variables to discover unclear errors.
4. Explore the TUI at small terminal sizes and with the test stack stopped to discover layout
   breakage and unclear messages.
5. Explore the admin API with partial or wrong credentials to discover authorization gaps.
6. Explore repeated `pitwall seed` and `pitwall init` runs to discover idempotency problems.
7. Explore `--json` output across commands to discover invalid or inconsistent JSON.
8. Explore the inbound rate limit with bursts from two terminals to discover counting errors.
9. Explore `pitwall guardrails preview` with nested JSON, large strings, and Unicode to discover
   missed detections or crashes. Synthetic data only.
10. Explore stopping and restarting the test stack while the API runs to discover poor recovery or
    unclear errors.
11. Explore the webhook receiver with malformed, oversized, and replayed deliveries to discover
    crashes or double processing.
12. Explore `/docs` against the route inventory in `docs/sdlc/02-api-rest.md` to discover
    undocumented or missing routes.
13. Explore the model catalogue (`pitwall models list`, `pitwall models show`) to discover missing
    fields or inconsistent names.
14. Explore Agent Routing `doctor` and its help output, with a temporary home, to discover confusing
    guidance.
```

- [ ] **Step 2: Write `qa/handbook/regression-and-release.md`**

````markdown
# Regression and release

A regression is something that used to work and now doesn't. These checks catch regressions after
changes and before releases.

## QA smoke set

Run the items that fit the change, in this order. Item 1 always runs.

1. Hermetic tests: `make test 2>&1 | tail -15`. Expected: a summary with `passed` and no `failed`.
2. Integration lane:

   ```bash
   docker compose -f docker-compose.testinfra.yml up -d --wait
   make test-int 2>&1 | tail -15
   make down
   ```

   Expected: `passed` and no `failed`.
3. README Quick Start dry-run inference, with the stack up and the README `export` lines set:
   `uv run pitwall db migrate`, `uv run pitwall init --non-interactive`, the API in a second
   terminal, then the README `curl`. Expected: the response contains `"dry_run": true`.
4. The docs page: `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/docs`.
   Expected: `200`.
5. The TUI: `uv run pitwall dashboard` in a terminal with the README `export` lines. Press each
   view key from the footer, then `q`. Expected: every view opens, and no traceback appears.
6. MCP: if the `pitwall-local` server from mission T2-08 is still registered, ask your agent to
   list its tools. Otherwise rely on J10 in the full journey harness.

## Full journey harness

`scripts/release/run-user-journeys.sh` runs every hermetic journey (J01–J27) from
`docs/operator/user-journey-catalog.md`. It resets the database it is given, so run it only against
the local test stack, with the README `export` lines set:

```bash
docker compose -f docker-compose.testinfra.yml up -d --wait
bash scripts/release/run-user-journeys.sh 2>&1 | tee qa/.work/evidence/journeys-$(date +%F).txt | tail -40
echo "exit=${PIPESTATUS[0]}"
make down
```

Expected: a `journey summary` listing each journey, a final `<n> passed, 0 failed`, and `exit=0`.
Ports 18080–18090 must be free first. The script stops if one is in use.

## Release candidate pass

Work through `docs/operator/release-testing-checklist.md` sections 1–7 in order, running each
command exactly as written there. Keep these in mind:

- Section 3 needs the exact marker expression `-m release`. Any other expression skips the gate
  silently.
- Section 6's `make mutation-gate` runs for a long time. Run it last.
- Live tiers are never part of this pass.

Then run the [full journey harness]{#full-journey-harness}.

## Sign-off report

Post where the maintainer asks (the release pull request or an issue):

- **Release candidate:** the tag or commit (`git rev-parse --short HEAD`)
- **Sections 1–7:** one row each, with the command, the result, and an evidence excerpt
- **Journey harness:** the summary line
- **Open `found-by-qa` issues** by severity, with links
- **Verdict:** ready, or not ready with the blocking issues listed
````

- [ ] **Step 3: Write `qa/handbook/test-automation.md`**

````markdown
# Test automation

Tier 4 work: turning findings into automated tests and opening test pull requests. Rule R8 still
applies. Change test files only, on a feature branch.

## Where tests go

- Root project tests live in `tests/`, grouped by area to match `src/pitwall/`: for example
  `tests/cli/`, `tests/api/`, `tests/property/`, `tests/security/`.
- Put a new test next to the existing tests for the same surface and copy their style and helpers
  (for example `tests/api/_contract_helpers.py`).
- Tests are hermetic by default. `tests/conftest.py` and `tests/_hermetic_env.py` set placeholder
  values and block real provider hosts. Fakes live in `tests/fakes/`.
- Markers choose the lane (see `docs/sdlc/17-testing-strategy.md`,
  [markers and isolation]{../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation}). A test that
  needs PostgreSQL or Redis is marked `integration`.
- `packages/agent-routing/tests/` is a separate project that uses `unittest`. Run it from inside
  `packages/agent-routing/`.

## Find then fix

Every production bug fix ships with a regression test (`CONTRIBUTING.md`, Definition of Done). A
regression test must fail when the bug is present. To prove it, temporarily undo the fix in the
source file, run the test, and watch it fail:

```bash
git show <fix-commit> -- <source-file> | git apply -R
uv run pytest -q <test-file>::<test-name>
git checkout -- <source-file>
uv run pytest -q <test-file>::<test-name>
```

Expected: the first run fails, and the second run passes. The source file is never committed.

## Pre-PR routine

```bash
git switch main && git pull
git switch -c test/<short-name>
uv run ruff check .
uv run ruff format --check .
make test 2>&1 | tail -15
git add <test-files>
git commit -s -m "test: <what the test protects>"
git log -1 --format='%(trailers:key=Signed-off-by)'
```

Expected: ruff reports no errors, `make test` shows no failures, and the last command prints a
`Signed-off-by:` line. If you touched an integration test, run the integration lane too.

## Opening the pull request

1. Draft the description in `qa/.work/notes/<short-name>-pr.md` using the sections of
   `.github/pull_request_template.md`. Link the issue with `Refs #N`.
2. After the tester approves the draft:

   ```bash
   git push -u origin test/<short-name>
   gh pr create --title "test: <what the test protects>" --body-file qa/.work/notes/<short-name>-pr.md
   ```

3. Watch CI with `gh pr checks <N>` and answer review comments.

## Reading CI failures

```bash
gh pr checks <N>
gh run view <run-id> --log-failed | tail -60
```

Find the first real error, reproduce it locally with the same command, fix it, commit with `-s`,
and push again.
````

- [ ] **Step 4: Write `qa/handbook/live-testing-safety.md`**

````markdown
# Live testing safety

Tier 5 only. Live testing uses a real provider key and real money. Everything here is mandatory,
and tier 5 is always training mode: the tester types every command.

## Key handling

- The maintainer gives the tester a spend-capped key privately. It never goes into the chat with
  an agent, an issue, a pull request, or the repository.
- The tester stores it in a file outside the repository, created with a text editor:

  ```bash
  mkdir -p ~/.config/pitwall-qa && chmod 700 ~/.config/pitwall-qa
  nano ~/.config/pitwall-qa/runpod.env
  chmod 600 ~/.config/pitwall-qa/runpod.env
  ```

  The file holds the one `export` line the maintainer provides.
- To load it into one terminal: `set -a; . ~/.config/pitwall-qa/runpod.env; set +a`. Never `cat`,
  `echo`, or print it.
- `pitwall setup` stores its own credential. Read
  [what `pitwall setup` changes on your machine]{../../docs/operator/personal-serving.md#what-pitwall-setup-changes-on-your-machine}
  before running it.

## Spend ceilings

Three layers, all agreed with the maintainer before the first live step:

1. The dedicated provider account's small prepaid balance: the hard ceiling.
2. `PITWALL_MONTHLY_BUDGET_USD`: Pitwall's own budget gate.
3. Each launch's `--ttl-minutes` and `--max-usd-per-hour`: the smallest values that work.

## Before every live step

1. Check what is already running: `uv run pitwall status`.
2. Check the balance: `uv run pitwall runpod catalogue` (balance is part of its output).
3. Write the exact command you plan to run into your notes and say what it should cost at most.
4. The tester runs it.

## Cleanup check

After every live mission, and before ending the session:

1. `uv run pitwall status` shows nothing running.
2. The provider's web console shows no pods, endpoints, or volumes left from the mission.
3. If anything is left, follow
   [orphaned pods]{../../docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner}.

## Surprise charges

A charge you did not expect is severity 1. Stop. Do not delete things blindly. Write down what is
running and what the console shows, file the issue with the tester, and message the maintainer
directly.
````

- [ ] **Step 5: Write `qa/handbook/maintaining-the-packet.md`**

```markdown
# Maintaining the packet

This folder describes a moving product. These rules keep it correct.

## Packet parity

`CONTRIBUTING.md` makes QA packet parity part of the Definition of Done
([definition of done]{../../CONTRIBUTING.md#definition-of-done}): a pull request that changes a
command, route, screen, or doc heading that a lesson uses updates that lesson in the same pull
request. `uv run python tools/ci/check_markdown_links.py` (also `make docs-check`) fails when a
linked heading is renamed or removed. Review catches the rest.

## New surfaces

When a pull request adds a user-facing surface, it also adds any standing mission or concept card
the surface needs, and its `## QA notes` drive the tester's acceptance run (mission T3-03).

## Packet bugs

When a lesson is wrong, unclear, or out of date, the tester files an issue with the `qa-packet`
label, plus `documentation` and a severity. The maintainer fixes it, or asks the tester to open a
pull request for it.

## Re-verifying commands

When a lesson changes, run its commands on a fresh clone the way the original verification did
(see the `qa-packet-verification` evidence file in `docs/evidence/`), and put the results in the
pull request description.
```

- [ ] **Step 6: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/handbook/exploratory-testing.md qa/handbook/regression-and-release.md qa/handbook/test-automation.md qa/handbook/live-testing-safety.md qa/handbook/maintaining-the-packet.md`
Expected: `shape check: 5 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/handbook`), and VC-secrets. Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add qa/handbook/exploratory-testing.md qa/handbook/regression-and-release.md \
  qa/handbook/test-automation.md qa/handbook/live-testing-safety.md qa/handbook/maintaining-the-packet.md
git commit -s -m "docs(qa): handbook exploratory testing, regression and release, automation, live safety, upkeep"
```

---

### Concept card contract (applies to Tasks 6, 7, 8)

Each card below is given as a contract: the exact facts for every section, the **Try it** command
with its expected result, and the check question with its answer. Write each card in the spec §5.5
format, with the H1 exactly as in the skeleton, 60 lines or fewer, and the answer inside
`<details><summary>Answer</summary> … </details>`. Links go from `qa/concepts/`, so repository-root
files are `../../<path>` and other packet files are `../<folder>/<file>`. This is the reference
card. Write `qa/concepts/environment-variables.md` exactly like this:

````markdown
# Environment variables

## In one sentence

An environment variable is a named setting, like `DATABASE_URL`, that a terminal passes to every
program it starts.

## Why it matters when testing Pitwall

Pitwall reads its configuration from environment variables and refuses to start when a required
one is missing (journey J05). The README Quick Start `export` lines set the placeholder values for
local testing. A new terminal starts without them, which is the most common reason a command works
in one terminal and fails in another.

## Try it

```bash
export GREETING=hello
echo "$GREETING"
```

Expected: `hello`. Open a new terminal and run `echo "$GREETING"` again. Expected: an empty line.

## Common confusions

- `export` sets a variable for this terminal and the programs it starts, not for other terminals.
- `NAME=value command` sets it for that one command only.
- `env -u NAME command` runs one command with `NAME` removed. Lessons use this to test missing
  settings.
- Never put a real key in a variable you might print or paste.

## Check yourself

1. You set the README variables in terminal 1. Why does `uv run pitwall-api` fail in terminal 2?

<details><summary>Answer</summary>

Terminal 2 has its own environment. Run the `export` lines there too.

</details>

## Go deeper

- [README configuration table]{../../README.md#configuration}
- [Core models and config]{../../docs/sdlc/16-core-config.md}
````

### Task 6: Concept index and computer-basics cards (lane `06-cards-a`)

**Lane:** ID `06-cards-a`. You own: `qa/concepts/README.md` and the 11 computer-basics cards listed
below. Do not touch any other file.

**Interfaces:**
- Consumes: skeleton card paths (all 29), bootcamp and handbook paths.
- Produces: the card index that coach files link to (`concepts/README.md`).

- [ ] **Step 1: Write `qa/concepts/README.md`**

```markdown
# Concept cards

Each card explains one idea in a few minutes: what it is, why it matters for testing Pitwall, one
safe thing to try, common confusions, a check question, and where to read more.

## Computer basics

- [The terminal and the shell]{terminal-and-shell.md}
- [Files, paths, and permissions]{files-paths-and-permissions.md}
- [Environment variables]{environment-variables.md}
- [Processes, ports, and localhost]{processes-ports-and-localhost.md}
- [Docker and Docker Compose]{docker-and-compose.md}
- [Git basics]{git-basics.md}
- [GitHub issues and pull requests]{github-issues-and-prs.md}
- [HTTP and status codes]{http-and-status-codes.md}
- [JSON and APIs]{json-and-apis.md}
- [Logs, tracebacks, and exit codes]{logs-tracebacks-and-exit-codes.md}
- [Python, uv, and virtual environments]{python-uv-and-venvs.md}

## Pitwall

- [Pitwall in one page]{pitwall-in-one-page.md}
- [Capabilities and providers]{capabilities-and-providers.md}
- [Dry runs and hermetic testing]{dry-run-and-hermetic.md}
- [Routing and route plans]{routing-and-plans.md}
- [Cost, budget, and guardrails]{cost-budget-and-guardrails.md}
- [Pods, leases, and serving]{pods-leases-and-serving.md}
- [The kill switch]{kill-switch.md}
- [Background services]{background-services.md}
- [MCP and agent clients]{mcp-and-agent-clients.md}
- [The TUI (terminal dashboard)]{the-tui.md}

## QA

- [Expected vs actual]{expected-vs-actual.md}
- [Severity and priority]{severity-and-priority.md}
- [Reproducing a bug]{reproducing-a-bug.md}
- [Exploratory testing]{exploratory-testing.md}
- [Regression and smoke tests]{regression-and-smoke-tests.md}
- [Test lanes and markers]{test-lanes-and-markers.md}
- [pytest, fixtures, and fakes]{pytest-fixtures-and-fakes.md}
- [Flaky tests]{flaky-tests.md}
```

- [ ] **Step 2: Write `qa/concepts/environment-variables.md`** exactly as the reference card above.

- [ ] **Step 3: Write the other ten computer-basics cards from these contracts**

- **`terminal-and-shell.md`**
  - Sentence: the terminal is the window, and the shell (bash) is the program inside it that reads
    and runs your commands.
  - Why: every lesson step happens in a shell, and the prompt shows who and where you are.
  - Try: `echo "$SHELL"`. Expected: a path ending in a shell name, such as `/bin/bash`.
  - Confusions: the `$` shown in docs is the prompt, so don't type it; Ctrl-C stops the running
    command (copy is Ctrl-Shift-C); commands are case-sensitive.
  - Check: "What does Ctrl-C do in a terminal?" Answer: it stops the running command.
  - Deeper: `../bootcamp/B1-terminal-basics.md`.
- **`files-paths-and-permissions.md`**
  - Sentence: a path says where a file is; absolute paths start at `/`, and relative paths start
    where you are.
  - Why: lessons run from the repository root with relative paths such as `qa/START-HERE.md`, and
    running from the wrong folder is the most common beginner failure.
  - Try: `pwd` then `ls -l qa/START-HERE.md`. Expected: the path ends in `/pitwall`, and the `ls`
    line starts with permissions such as `-rw-r--r--`.
  - Confusions: `~` is your home folder; `.` is here and `..` is one level up; `chmod 600` makes a
    file readable only by you (used for the tier 5 key file).
  - Check: "From inside `qa/`, what does `../README.md` point to?" Answer: the README at the
    repository root.
  - Deeper: `../bootcamp/B1-terminal-basics.md`.
- **`processes-ports-and-localhost.md`**
  - Sentence: a running program is a process; a server process listens on a port; and `127.0.0.1`
    (localhost) means this machine only.
  - Why: the API listens on port 8080, the test database on 5444, and Redis on 6380. A busy port
    stops a server from starting, and the journey harness refuses to run when its ports are busy.
  - Try: `ss -tln | grep -E ':(5444|6380|8080) '`. Expected: one line per port that is in use now,
    and nothing otherwise.
  - Confusions: Ctrl-C stops the process in that terminal; closing a terminal can leave background
    processes running; loopback cannot be reached from other machines, by design.
  - Check: "The API says 'address already in use'. What do you check?" Answer: whether another API
    is still on 8080 (`ss -tln`), then stop it.
  - Deeper: `../../README.md#security-and-trust-model`.
- **`docker-and-compose.md`**
  - Sentence: Docker runs programs in containers, and Docker Compose starts a set of containers
    from one file.
  - Why: the local test database and Redis run in containers from `docker-compose.testinfra.yml`.
    Start them with `docker compose -f docker-compose.testinfra.yml up -d --wait` and stop them
    with `make down`.
  - Try: `docker ps --format '{{.Names}}  {{.Ports}}'`. Expected: the running containers with
    their ports, including `5444` and `6380` when the test stack is up.
  - Confusions: an image is the recipe and a container is a running copy; `make down` removes the
    test containers but keeps their data in named volumes (`down -v` deletes those too), which is
    fine because it is test data; only one test stack runs at a time (R13).
  - Check: "Why does starting the stack in a second clone fail while the first clone's stack
    runs?" Answer: both need ports 5444 and 6380.
  - Deeper: `../../docs/sdlc/17-testing-strategy.md#3-local-infrastructure`.
- **`git-basics.md`**
  - Sentence: git records the project's history as commits, and a branch is a named line of
    commits.
  - Why: every test result names the commit tested, and acceptance testing checks out a pull
    request's branch.
  - Try: `git log --oneline -3` then `git rev-parse --short HEAD`. Expected: three lines, each a
    short commit id and a message, then one short id.
  - Confusions: a commit is saved locally and a push sends it to GitHub; `git status` shows what
    changed; switching branches changes the files on disk.
  - Check: "How do you find the commit you are testing?" Answer: `git rev-parse --short HEAD`.
  - Deeper: `../bootcamp/B4-git-and-github.md`, `../../CONTRIBUTING.md#pr-process`.
- **`github-issues-and-prs.md`**
  - Sentence: an issue reports a problem or asks a question; a pull request proposes a change and
    runs CI checks before it merges.
  - Why: all QA output lands on GitHub: issues, QA reviews on pull requests, and test pull
    requests.
  - Try: `gh issue list --limit 5` then `gh pr list --limit 5`. Expected: two lists, which may be
    empty.
  - Confusions: `Closes #N` in a pull request closes the issue when it merges; a review can
    approve, request changes, or comment; labels sort work.
  - Check: "Where does a QA verdict go?" Answer: a review on the pull request, plus a `qa-passed`
    or `qa-failed` label.
  - Deeper: `../handbook/triage-and-labels.md`, `../handbook/acceptance-testing.md`.
- **`http-and-status-codes.md`**
  - Sentence: programs talk to the API over HTTP, and every response carries a status code that
    says how it went.
  - Why: API testing is mostly checking that the right code comes back. Include this table:

    | Code | Meaning |
    | --- | --- |
    | 200 | OK |
    | 201 | Created |
    | 401 | Missing or wrong credential |
    | 402 | Refused by the budget gate |
    | 403 | Valid credential without permission |
    | 404 | Not found |
    | 422 | Request body invalid |
    | 429 | Too many requests (see `Retry-After`) |
    | 500 | Server error, always a bug |

  - Try (API running): `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz`.
    Expected: `200`.
  - Confusions: 401 versus 403; 4xx means a request problem and 5xx means a server problem; a 200
    with an error inside the body is still worth reporting.
  - Check: "Which code proves the budget gate worked?" Answer: 402.
  - Deeper: `../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types`; journeys J07, J08, and
    J13–J15 in `../../docs/operator/user-journey-catalog.md`.
- **`json-and-apis.md`**
  - Sentence: JSON is the text format the API uses for requests and responses: objects in `{}`,
    lists in `[]`, text in double quotes.
  - Why: every request body and response is JSON, and malformed JSON should get a 422, never a 500.
  - Try (API running): `curl -s http://127.0.0.1:8080/healthz`. Expected:
    `{"ok":true,"backend":"runpod"}`. Then
    `curl -s http://127.0.0.1:8080/healthz | uv run python -m json.tool`. Expected: the same
    object, pretty-printed.
  - Confusions: single quotes and trailing commas are not valid JSON; JSON writes `true`, not
    `True`.
  - Check: "Is `{'a': 1}` valid JSON?" Answer: no; JSON needs double quotes.
  - Deeper: `../../docs/sdlc/02-api-rest.md#3-route-inventory`.
- **`logs-tracebacks-and-exit-codes.md`**
  - Sentence: programs report problems in three ways: log lines, tracebacks (Python's crash
    report), and exit codes (a number where 0 means success).
  - Why: a traceback during normal use is a bug, and exit codes are part of Pitwall's documented
    contract.
  - Try: `cat does-not-exist.txt; echo "exit=$?"`. Expected: `No such file or directory`, then
    `exit=1`. Then `env -u DATABASE_URL -u REDIS_URL uv run pitwall config check; echo "exit=$?"`.
    Expected: a `missing-runtime-config` message and `exit=78`.
  - Confusions: read a traceback from the bottom (the last line is the error); `$?` holds only the
    last command's code; after a pipe, use `${PIPESTATUS[0]}`.
  - Check: "A command printed an error but `exit=0`. Is that worth noting?" Answer: yes. Compare it
    with the exit-code table.
  - Deeper: `../../docs/sdlc/18-cli.md#exit-codes`.
- **`python-uv-and-venvs.md`**
  - Sentence: Pitwall is written in Python; `uv` installs the exact versions it needs into a
    private folder, `.venv`, and `uv run` runs commands inside it.
  - Why: `uv sync --frozen --extra dev` sets everything up, and `uv run` guarantees the project's
    Python 3.14.7 rather than the system one.
  - Try: `uv run python --version`. Expected: `Python 3.14.7`.
  - Confusions: never run bare `python`; after switching branches, run
    `uv sync --frozen --extra dev` again; `packages/agent-routing` has its own separate `.venv`.
  - Check: "You checked out a pull request and imports fail. What do you run?" Answer:
    `uv sync --frozen --extra dev`.
  - Deeper: `../../CONTRIBUTING.md#dev-environment`.

- [ ] **Step 4: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/concepts/README.md qa/concepts/terminal-and-shell.md qa/concepts/files-paths-and-permissions.md qa/concepts/environment-variables.md qa/concepts/processes-ports-and-localhost.md qa/concepts/docker-and-compose.md qa/concepts/git-basics.md qa/concepts/github-issues-and-prs.md qa/concepts/http-and-status-codes.md qa/concepts/json-and-apis.md qa/concepts/logs-tracebacks-and-exit-codes.md qa/concepts/python-uv-and-venvs.md`
Expected: `shape check: 12 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/concepts`), and VC-secrets. Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add qa/concepts/README.md qa/concepts/terminal-and-shell.md qa/concepts/files-paths-and-permissions.md \
  qa/concepts/environment-variables.md qa/concepts/processes-ports-and-localhost.md qa/concepts/docker-and-compose.md \
  qa/concepts/git-basics.md qa/concepts/github-issues-and-prs.md qa/concepts/http-and-status-codes.md \
  qa/concepts/json-and-apis.md qa/concepts/logs-tracebacks-and-exit-codes.md qa/concepts/python-uv-and-venvs.md
git commit -s -m "docs(qa): concept index and computer-basics cards"
```

### Task 7: Pitwall concept cards (lane `07-cards-b`)

**Lane:** ID `07-cards-b`. You own the ten Pitwall cards listed below in `qa/concepts/`. Do not
touch any other file.

**Interfaces:**
- Consumes: the card contract and reference card (above), skeleton paths.
- Produces: the Pitwall vocabulary that bootcamp B5 and the missions link to.

- [ ] **Step 1: Write the ten cards from these contracts**

For every **Try it** that needs the test stack, say so in the card ("with the test stack up and the
README `export` lines set").

- **`pitwall-in-one-page.md`**
  - Sentence: Pitwall is a control plane that takes requests for AI work, picks a provider, checks
    safety and cost before anything is spent, runs the work on rented GPUs, and records what
    happened.
  - Why: knowing the flow shows where a bug can hide. Reproduce the one-line flow from
    `README.md#architecture`: clients, surfaces, pre-spend inspection, routing, cost admission,
    provider adapter, audit and database.
  - Try: `uv run pitwall --help 2>&1 | head -20`. Expected: a usage line and the start of the
    command list.
  - Confusions: Pitwall rents GPUs from providers such as RunPod and owns none; it is built for one
    operator, not many tenants; most local testing never touches a provider.
  - Check: "Put these in order: routing, cost admission, pre-spend inspection." Answer:
    pre-spend inspection, routing, cost admission.
  - Deeper: `../../README.md#architecture`, `../../docs/sdlc/00-overview.md`.
- **`capabilities-and-providers.md`**
  - Sentence: a capability is a kind of work, such as `embedding.demo`; a provider is a registered
    place that can do it, such as `prov_demo_runpod_lb`.
  - Why: routing picks a healthy provider for the requested capability, and journey J01's expected
    result names both.
  - Try (stack up, `pitwall init --non-interactive` done, exports set):
    `curl -s -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/v1/capabilities`.
    Expected: JSON that includes `embedding.demo`.
  - Confusions: the seed files in `seed/` hold fake demo values on purpose; provider health
    (`healthy`, `unhealthy`, `hibernated`) changes routing; an unknown capability returns 404.
  - Check: "What does `pitwall init --non-interactive` create?" Answer: the demo capability and
    provider, with the provider marked healthy.
  - Deeper: `../../README.md#quick-start`, `../../docs/sdlc/04-routing.md`.
- **`dry-run-and-hermetic.md`**
  - Sentence: a dry run does the planning (routing and cost) but sends nothing to a paid provider;
    hermetic means a test uses no real outside services at all.
  - Why: tiers 0–4 are hermetic. The placeholder key makes every paid path fail at
    authentication. A dry run that contacts a real provider is severity 1.
  - Try (stack, init, exports, API running): the README Quick Start `curl`. Expected: the response
    contains `"dry_run": true`.
  - Confusions: dry run is an option on one request, while hermetic describes a whole test or
    environment; `-m live` tests are the opposite of hermetic and are on the never-run list.
  - Check: "Why is tier 5 locked?" Answer: it needs a real key and spends real money.
  - Deeper: `../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation`.
- **`routing-and-plans.md`**
  - Sentence: routing filters providers by hard requirements and health, scores the rest, and
    produces a plan with a selected provider and fallbacks.
  - Why: J01 expects `selected_provider_id` to be `prov_demo_runpod_lb`, and wrong routing sends
    work, and money, to the wrong place.
  - Try: the README Quick Start `curl`. Expected: the route plan in the response includes
    `selected_provider_id`.
  - Confusions: an unhealthy provider is skipped; a plan is made per request; the fallback chain is
    the list of next choices.
  - Check: "Which field names the chosen provider?" Answer: `selected_provider_id`.
  - Deeper: `../../docs/sdlc/04-routing.md`.
- **`cost-budget-and-guardrails.md`**
  - Sentence: before any spend, Pitwall inspects the payload for secrets and personal data
    (guardrails), estimates the cost, and refuses requests over the budget.
  - Why: these are the money and safety gates, and a failure here is severity 1.
  - Try (exports set): `uv run pitwall guardrails status`. Expected: the guardrail mode and a
    `Guardrail rules` table; exit 0.
  - Confusions: `guardrails preview` exits 0 whether it allows, redacts, or blocks, and exits 2
    only for bad or oversized JSON; 402 is the budget refusal; prices are exact decimals written as
    strings in JSON.
  - Check: "What exit code does `guardrails preview` return when it blocks a payload?" Answer: 0.
    Block is a successful preview.
  - Deeper: `../../docs/sdlc/05-cost-budget.md`,
    `../../docs/sdlc/18-cli.md#guardrails-status--guardrails-preview`.
- **`pods-leases-and-serving.md`**
  - Sentence: a pod is a rented GPU machine; a lease is Pitwall's record of a pod it launched, with
    an expiry; serving runs a model on a pod behind an OpenAI-compatible address.
  - Why: these are the paid parts (tier 5), but their read-only views (`pitwall leases list`, the
    TUI Leases view) are testable now.
  - Try (stack up, exports set): `uv run pitwall leases list`. Expected: an empty result, because
    there are no pods locally.
  - Confusions: `pitwall serve`, `status`, and `stop` are on the never-run list in tiers 0–4; a TTL
    is the pod's self-stop deadline; an orphaned pod has no working owner.
  - Check: "Why does every launch get a TTL?" Answer: so a forgotten pod stops itself and stops
    costing money.
  - Deeper: `../../docs/sdlc/06-leases.md`, `../../docs/operator/personal-serving.md`.
- **`kill-switch.md`**
  - Sentence: the kill switch is an emergency stop that blocks new work and can terminate running
    compute.
  - Why: it must finish quickly and in order (block access, remove devices, terminate compute);
    `docs/operator/release-testing-checklist.md` section 4 covers it. Journey J21 drills it safely
    with `"terminate_compute": false`.
  - Try: `grep -n '^| J21' docs/operator/user-journey-catalog.md`. Expected: the J21 row.
  - Confusions: it needs the admin secret header; locally there are no pods, so the drill is safe;
    a kill switch that fails to stop things is severity 1.
  - Check: "Which header carries the admin secret?" Answer: `X-Pitwall-Secret`, plus the bearer
    token when API auth is on.
  - Deeper: `../../docs/sdlc/02-api-rest.md#3-route-inventory`,
    `../../docs/operator/release-testing-checklist.md`.
- **`background-services.md`**
  - Sentence: besides the API, Pitwall runs a reconciler (keeps state in sync), a webhook receiver
    (accepts provider callbacks), and a cost exporter (publishes metrics).
  - Why: they fail quietly; mission T2-09 checks each one (J18–J20).
  - Try (stack up, exports set): `uv run python -m pitwall.reconciler check; echo "exit=$?"`.
    Expected: `exit=0`.
  - Confusions: with `PITWALL_WEBHOOK_SECRET` set, the receiver requires a signature; a replayed
    delivery is flagged as a duplicate, not processed twice; metric names start with `pitwall_`.
  - Check: "What should a replayed webhook get back?" Answer: 200 with `"duplicate": true`.
  - Deeper: `../../docs/sdlc/09-webhooks.md`, `../../docs/sdlc/10-reconciler-lifecycle.md`,
    `../../docs/sdlc/13-observability.md`.
- **`mcp-and-agent-clients.md`**
  - Sentence: MCP (Model Context Protocol) lets an AI agent call Pitwall's tools directly, and
    Pitwall's MCP server runs only over local stdio.
  - Why: AI agents are a real kind of Pitwall user (J10), and a network MCP transport must be
    refused (J11).
  - Try: `uv run pitwall mcp serve --help`. Expected: a usage message; nothing starts.
  - Confusions: stdio means the agent starts the server as a child process; MCP tools go through
    the same gates as the API; network MCP is unsupported on purpose.
  - Check: "Why is network MCP refused?" Answer: it would expose the tools without
    authentication.
  - Deeper: `../../docs/sdlc/03-mcp-server.md`.
- **`the-tui.md`**
  - Sentence: the TUI is Pitwall's full-screen terminal dashboard, started with
    `pitwall dashboard`, with ten views.
  - Why: operators use it every day; crashes and layout problems are findings; some actions are
    guarded by typed confirmation.
  - Try (stack up, exports set): `uv run pitwall dashboard`, press `?`, then `q`. Expected: help
    lists the keys, then the app closes.
  - Include this key table: `o` Overview, `p` Providers, `l` Leases, `m` Models, `s` Serve, `d`
    Pods, `t` Routes, `c` Cost, `e` Resources, `a` Operations; `:` command palette, `/` search, `?`
    help, `q` quit.
  - Confusions: never start it without the README `export` lines (GC-16, rule R2); never finish a
    type-to-confirm dialog; the Providers keys `g` and `a` contact providers, so skip them until
    tier 5.
  - Check: "Which key opens the Cost view?" Answer: `c`.
  - Deeper: `../../docs/sdlc/18-cli.md`.

- [ ] **Step 2: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/concepts/pitwall-in-one-page.md qa/concepts/capabilities-and-providers.md qa/concepts/dry-run-and-hermetic.md qa/concepts/routing-and-plans.md qa/concepts/cost-budget-and-guardrails.md qa/concepts/pods-leases-and-serving.md qa/concepts/kill-switch.md qa/concepts/background-services.md qa/concepts/mcp-and-agent-clients.md qa/concepts/the-tui.md`
Expected: `shape check: 10 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/concepts`), and VC-secrets. Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add qa/concepts/pitwall-in-one-page.md qa/concepts/capabilities-and-providers.md qa/concepts/dry-run-and-hermetic.md \
  qa/concepts/routing-and-plans.md qa/concepts/cost-budget-and-guardrails.md qa/concepts/pods-leases-and-serving.md \
  qa/concepts/kill-switch.md qa/concepts/background-services.md qa/concepts/mcp-and-agent-clients.md qa/concepts/the-tui.md
git commit -s -m "docs(qa): Pitwall concept cards"
```

### Task 8: QA concept cards (lane `08-cards-c`)

**Lane:** ID `08-cards-c`. You own the eight QA cards listed below in `qa/concepts/`. Do not touch
any other file.

**Interfaces:**
- Consumes: the card contract and reference card, and handbook anchors `severity-scale`,
  `the-bug-report-checklist`, `qa-smoke-set`, and `where-tests-go`.
- Produces: the QA vocabulary that B6 and the missions link to.

- [ ] **Step 1: Write the eight cards from these contracts**

- **`expected-vs-actual.md`**
  - Sentence: every test compares what should happen (expected) with what did happen (actual), and
    a difference is a finding.
  - Why: a report without a clear expected result is an opinion. The expected result comes from a
    doc, a spec, `--help`, or a journey in the catalog.
  - Try: `uv run pitwall --version` then `grep -m1 '^version' pyproject.toml`. Expected: the same
    version in both.
  - Confusions: "expected" does not mean "what I would like"; when no doc says what should happen,
    ask (R7).
  - Check: "Where do expected results come from?" Answer: docs, specs, `--help`, and the journey
    catalog.
  - Deeper: `../handbook/bug-reports.md`.
- **`severity-and-priority.md`**
  - Sentence: severity is how bad a problem is, and priority is how soon it gets fixed.
  - Why: QA sets severity with the scale, and the maintainer sets priority.
  - Try: `gh label list --search severity`. Expected: the four severity labels.
  - Confusions: a typo on the front page can be high priority but low severity; severity 1 is about
    money, secrets, or safety.
  - Check: "A dry run called a real provider. Which severity?" Answer: 1.
  - Deeper: `../handbook/triage-and-labels.md#severity-scale`.
- **`reproducing-a-bug.md`**
  - Sentence: reproducing means making a bug happen again on purpose, from a clean start, with
    steps someone else can follow.
  - Why: a bug nobody can reproduce rarely gets fixed.
  - Try: run `cat does-not-exist.txt; echo "exit=$?"` twice. Expected: the same message and
    `exit=1` both times.
  - Confusions: start from a known state (fresh terminal, known commit, stack restarted); change one
    thing at a time; for a problem that happens only sometimes, report how often (for example
    "3 of 10 runs").
  - Check: "What comes first in repro steps?" Answer: the starting state: commit, test stack,
    environment.
  - Deeper: `../handbook/bug-reports.md#the-bug-report-checklist`.
- **`exploratory-testing.md`**
  - Sentence: exploratory testing is testing without a script, guided by a charter, taking notes as
    you go.
  - Why: scripted journeys cover the known paths, and exploration finds the unknown ones.
  - Try: `grep -c '^[0-9]*\. Explore' qa/handbook/exploratory-testing.md`. Expected: `14`.
  - Confusions: exploring is not random clicking, because it has a charter and notes; the
    never-run list still applies.
  - Check: "What are the three parts of a charter?" Answer: the area, the resources or technique,
    and the kind of problem to discover.
  - Deeper: `../handbook/exploratory-testing.md`.
- **`regression-and-smoke-tests.md`**
  - Sentence: a regression is something that used to work and broke; a smoke test is a quick check
    that the main things still work.
  - Why: QA runs the smoke set after changes, and regressions are why tests exist.
  - Try: `make test 2>&1 | tail -3`. Expected: a summary line containing `passed`. It can take
    several minutes.
  - Confusions: a smoke test is not the full suite; a regression test is written to catch one
    specific bug if it returns.
  - Check: "Which smoke item always runs?" Answer: `make test`.
  - Deeper: `../handbook/regression-and-release.md#qa-smoke-set`.
- **`test-lanes-and-markers.md`**
  - Sentence: Pitwall's tests are grouped into lanes by pytest markers: fast hermetic tests,
    integration tests with a real database, and special lanes such as security, property, and
    release.
  - Why: the lane tells you what a test needs and how to run it.
  - Try: `grep -n -A14 'markers = \[' pyproject.toml`. Expected: the registered markers, including
    `integration`, `property`, `security`, `release`, and `live`.
  - Confusions: `make test` runs `-m "not integration and not slow"`; `-m release` must be written
    exactly that way; `live` tests are on the never-run list until tier 5.
  - Check: "Which make target runs the integration lane?" Answer: `make test-int`, with the test
    stack up.
  - Deeper: `../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation`.
- **`pytest-fixtures-and-fakes.md`**
  - Sentence: pytest runs test functions; fixtures hand tests ready-made setup; fakes stand in for
    real services so tests stay hermetic.
  - Why: tier 4 tests reuse the existing fixtures (`tests/conftest.py`) and fakes (`tests/fakes/`)
    instead of calling real providers.
  - Try: `ls tests/fakes`. Expected: files including `runpod.py` and `mcp.py`.
  - Confusions: a test requests a fixture by naming it as an argument; a fake behaves like the real
    service in a controlled way, while a mock only records calls.
  - Check: "Why does a hermetic test use the RunPod fake?" Answer: no network, no credentials, no
    spend, and the same result every time.
  - Deeper: `../handbook/test-automation.md#where-tests-go`.
- **`flaky-tests.md`**
  - Sentence: a flaky test sometimes passes and sometimes fails with no code change.
  - Why: flaky tests hide real bugs and waste time. Pitwall's suite runs in random order
    (pytest-randomly), which can expose tests that depend on each other.
  - Try: run `uv run pytest -q tests/cli/test_cli_output.py 2>&1 | tail -1` twice. Expected: both
    runs report `passed`.
  - Confusions: a flaky test is still a finding; report runs attempted and failed, the commit, and
    the random seed pytest prints near the top of its output; re-running until green is not a fix.
  - Check: "What does a flaky-test report include?" Answer: runs attempted and failed, the seed,
    and the commit.
  - Deeper: `../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation`,
    `../handbook/test-automation.md`.

- [ ] **Step 2: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/concepts/expected-vs-actual.md qa/concepts/severity-and-priority.md qa/concepts/reproducing-a-bug.md qa/concepts/exploratory-testing.md qa/concepts/regression-and-smoke-tests.md qa/concepts/test-lanes-and-markers.md qa/concepts/pytest-fixtures-and-fakes.md qa/concepts/flaky-tests.md`
Expected: `shape check: 8 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/concepts`), and VC-secrets. Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add qa/concepts/expected-vs-actual.md qa/concepts/severity-and-priority.md qa/concepts/reproducing-a-bug.md \
  qa/concepts/exploratory-testing.md qa/concepts/regression-and-smoke-tests.md qa/concepts/test-lanes-and-markers.md \
  qa/concepts/pytest-fixtures-and-fakes.md qa/concepts/flaky-tests.md
git commit -s -m "docs(qa): QA concept cards"
```

---

### Lesson and mission contract (applies to Tasks 9–15)

Lessons and missions are given as contracts: every step's coach action, tester command, expected
result, and what to do if different. Write each file in the spec §5.3 or §5.4 format (GC-8), with
the H1 exactly as in the skeleton, and at most 150 lines. Each step is a `### Step N — <name>`
block with `Coach:`, `Tester does:` (a fenced `bash` block when there is a command), `Expected:`,
and `If different:` lines. Links go from `qa/bootcamp/` or `qa/missions/<tier>/`, so root files are
`../../<path>` or `../../../<path>` respectively. Lessons never give credential values. They say
"the five `export` lines from the README Quick Start" (GC-6). Missions end `## Record in progress`
with: a `Completed` row with output links, `Current item` set to the next item, and the session
log.

### Task 9: Bootcamp (lane `09-bootcamp`)

**Lane:** ID `09-bootcamp`. You own `qa/bootcamp/B0-welcome.md` through
`qa/bootcamp/B6-qa-fundamentals.md` (seven files). Do not touch any other file.

**Interfaces:**
- Consumes: `../coach/rules.md#modes`, `../missions/README.md#the-tiers`, concept-card paths,
  `../handbook/bug-reports.md#the-bug-report-checklist`, `../templates/tier-check.md`,
  `../../README.md#quick-start`, `../../README.md#architecture`.
- Produces: the lesson sequence that `START-HERE.md` and `missions/README.md` point into.

- [ ] **Step 1: Write `qa/bootcamp/B0-welcome.md` exactly as follows (the reference lesson)**

````markdown
# B0 — Welcome

**Mode:** training | **Needs:** none | **Output:** `qa/.work/progress.md`

## Goal

Get to know the tester, explain how this program works, and fill in the progress file.

## You will learn

- What QA means on this project.
- How the program is organized: a bootcamp, then five tiers of missions.
- Training mode and work mode.
- The rules that matter most.
- How a session starts and ends.

## Before you start

The coach has just created `qa/.work/progress.md` from the template (Start here, section 2). The
tester is at the repository root.

## Steps

### Step 1 — Introductions

Coach: introduce yourself as the tester's QA coach. Say in two sentences what QA means here:
finding problems before users do, and proving that things work, with evidence. Then ask, one at a
time: the tester's name; what they do at work; which AI agents they use; how they like to learn
(see it first, try it first, or have it explained first).
Tester does: answers.
Expected: the coach fills in "About me" in the progress file.

### Step 2 — The maintainer

Coach: ask who the tester reports to and how they reach that person.
Expected: the coach fills in the "Maintainer" line.

### Step 3 — How the program works

Coach: explain [the tiers]{../missions/README.md#the-tiers} in plain words: the bootcamp first,
then docs testing, hands-on testing, checking pull requests, writing tests, and a locked live tier
that opens only when the maintainer gives the tester a spend-capped key.
Tester does: answers "Which tier can spend real money, and what unlocks it?"
Expected: tier 5; the maintainer's key.
If different: explain again with the table in `missions/README.md`.

### Step 4 — Modes

Coach: explain [training and work mode]{../coach/rules.md#modes}. In training mode the tester
types every command that changes something. In work mode the coach may run allowed commands after
saying what and why. The bootcamp is always training mode.
Expected: the progress file says `Mode default: training`.

### Step 5 — The rules that matter most

Coach: walk through five of [the coach rules]{../coach/rules.md}:
- R1: no real credentials
- R2: the never-run list, with three examples: `pitwall setup`, `pitwall serve` without
  `--dry-run`, and `git push` to `main`
- R6: evidence
- R9: the tester approves everything posted
- R14: severity 1 is urgent

Tester does: says one rule back in their own words.
Expected: a correct paraphrase.

### Step 6 — How sessions work

Coach: every session starts with the kickoff sentence, progress lives in `qa/.work/`, and every
session ends with an update and a summary.
Tester does:

```bash
ls qa/.work
```

Expected: `evidence  notes  progress.md`.

```bash
head -3 qa/.work/progress.md
```

Expected: the first line starts with `# QA progress —`.
If different: the bootstrap in Start here, section 2, did not run. Run it now.

### Step 7 — Asking for help

Coach: tell the tester they can say "I'm stuck", "explain that", "slow down", or "why?" at any
time.
Expected: the tester knows how to ask.

## Checkpoint

Ask the tester to explain training mode and work mode in their own words, and to name three rules.

## Done when

- "About me" and "Maintainer" are filled in.
- The tester answered the checkpoint correctly.

## Record in progress

Add a `Completed` row for B0, set `Current item: B1`, and write the session log entry.

## Next

[B1 — Terminal basics]{B1-terminal-basics.md}
````

- [ ] **Step 2: Write `qa/bootcamp/B1-terminal-basics.md` from this contract**

- Metadata: `training | none beyond B0 | none`.
- Goal: get comfortable moving around and running commands in a terminal.
- You will learn: the prompt; `ls`, `cd`, and `pwd`; absolute versus relative paths; `head` and
  `less`; Tab completion and history; Ctrl-C; copy and paste; pipes and `tee`; exit codes; what
  `sudo` means.
- Before you start: a terminal at the repository root. Link the cards `terminal-and-shell`,
  `files-paths-and-permissions`, and `logs-tracebacks-and-exit-codes`.
- Steps:
  1. **Where am I:** `pwd`. Expected: a path ending in `/pitwall`. If different: `cd ~/pitwall`.
  2. **What's here:** `ls`, then `ls -a`. Expected: `README.md`, `qa`, `src`, `tests`; then also
     `.git`. Explain dot-files.
  3. **Moving around:** run `cd qa` then `pwd` then `ls` then `cd ..` then `pwd` again. Expected: `.../pitwall/qa`; a listing
     that includes `START-HERE.md`, `bootcamp`, `missions`; back to `.../pitwall`. Explain `..` and
     `~`.
  4. **Reading files:** `head -5 qa/README.md`, then `less qa/START-HERE.md`. Expected: the first
     line is `# QA program`; `less` scrolls with the arrow keys and space, searches with `/`, and
     quits with `q`.
  5. **Shortcuts:** type `head -3 qa/STA` and press Tab. Expected: it completes to
     `qa/START-HERE.md`. Then press the up arrow to recall a command, and run `sleep 30` and
     press Ctrl-C. Expected: `^C` and the prompt returns.
  6. **Copy and paste:** `uname -sr`. Expected: `Linux` and a version. The tester selects it, copies
     with Ctrl-Shift-C, and pastes it into the chat with Ctrl-Shift-V.
  7. **Pipes and tee:** `ls qa | wc -l`, then
     `ls -la qa 2>&1 | tee qa/.work/evidence/B1-ls.txt | head -5`, then `ls qa/.work/evidence`.
     Expected: a number; the first lines of the listing; `B1-ls.txt`. Explain `|`, `2>&1`, and
     `tee`.
  8. **Errors and exit codes:** `cat does-not-exist.txt; echo "exit=$?"`, then
     `ls qa > /dev/null; echo "exit=$?"`. Expected: `No such file or directory` with `exit=1`, then
     `exit=0`.
  9. **sudo:** no command. Explain that `sudo` runs a command as the administrator; the coach
     never runs it; the tester types their own password; it is allowed only in B2 (R2).
- Checkpoint: "What does `|` do? What does `tee` add? What does exit code 0 mean?"
- Done when: the tester navigated to the repository root, listed `qa/`, saved output with `tee`, and
  explained exit codes.
- Record in progress: B1 row; add shell, path, pipe, and exit code to "Terms taught"; set
  `Current item: B2`.
- Next: `B2-install-and-clone.md`.

- [ ] **Step 3: Write `qa/bootcamp/B2-install-and-clone.md` from this contract**

- Metadata: `training | B1 | evidence files`.
- Goal: a complete toolchain and a working Pitwall environment.
- You will learn: package managers; what each tool is for; the Docker group; `uv` and the project
  environment.
- Before you start: the kickoff already installed git and `gh` and cloned the repository. This is
  the only lesson where `sudo` is allowed (R2). The tester types every `sudo` command and their own
  password. The coach never runs `sudo`.
- Steps:
  1. **Which Linux:** `grep -E '^(ID|ID_LIKE|VERSION_ID)=' /etc/os-release`. Expected: lines such
     as `ID=ubuntu`. Pick the package manager:
     - `apt` for `ubuntu`, `debian`, `linuxmint`, `pop`, or any `ID_LIKE` containing `debian` or
       `ubuntu`
     - `dnf` for `fedora`, or `ID_LIKE` containing `fedora` or `rhel`
     - `pacman` for `arch`

     Record the distribution on the "Machine" line.
  2. **What's installed:** one at a time: `git --version`, `gh --version`,
     `curl --version | head -1`, `make --version | head -1`, `ss -V`, `script --version`,
     `docker --version`, `docker compose version`, `uv --version`. Expected: each prints a version.
     `command not found` means the tool is missing; write it down.
  3. **Small tools, only if missing:**
     - `sudo apt update && sudo apt install -y curl make iproute2 util-linux`
     - `sudo dnf install -y curl make iproute util-linux`
     - `sudo pacman -S --needed curl make iproute2 util-linux`

     Expected: no errors, and the version checks now pass.
  4. **Docker Engine, only if missing:** `curl -fsSL https://get.docker.com -o /tmp/get-docker.sh`,
     `sudo sh /tmp/get-docker.sh`, `sudo usermod -aG docker "$USER"`, then log out and back in, then
     `docker run --rm hello-world`. Expected: the output contains `Hello from Docker!`.
     - Explain first: this is Docker's own quick-install script for development machines, and
       `sudo sh` runs a downloaded script as the administrator. That is why it must come from
       Docker's own address.
     - If different: `permission denied` on `docker.sock` means the group change is not active
       yet. Log out and in again, or reboot.
  5. **uv, only if missing:** `curl -LsSf https://astral.sh/uv/install.sh | sh`, open a new
     terminal, `uv --version`. Expected: `uv 0.` followed by numbers.
  6. **GitHub access:** `gh auth status`, then
     `gh repo view Buckeyes22/pitwall --json visibility --jq .visibility`. Expected:
     `Logged in to github.com`, then `PRIVATE` or `PUBLIC`. If different: the collaborator
     invitation has not been accepted. Ask the maintainer.
  7. **The working clone:** `git remote -v`, `git status`, then
     `git check-ignore qa/.work/progress.md`. Expected: `origin` points at
     `github.com/Buckeyes22/pitwall`; `On branch main` and a clean tree; the progress path is
     printed, which proves git ignores it.
  8. **The project environment:** `uv sync --frozen --extra dev 2>&1 | tail -3`, then `ls -d .venv`.
     Expected: no error, then `.venv`. The first run downloads Python 3.14.7 and packages, which
     can take several minutes.
  9. **First Pitwall commands:** `uv run pitwall --version`, then
     `uv run pitwall --help 2>&1 | head -3`. Expected: a version such as `0.1.0a2`, then a first
     line starting `Usage: pitwall`. Say it clearly: `--version` and `--help` are always safe, and
     bare `pitwall` is not (R2).
  10. **Save the evidence:**
      `{ git --version; gh --version | head -1; docker --version; docker compose version; uv --version; uv run pitwall --version; } 2>&1 | tee qa/.work/evidence/B2-versions.txt`.
      Expected: six version lines.
- Checkpoint: "What is Docker for in this project? What does `uv sync` do?" Answers: it runs the
  test database and Redis; it installs the exact packages into `.venv`.
- Done when: every tool prints a version, `hello-world` works, `uv run pitwall --help` prints
  usage, and `B2-versions.txt` exists.
- Record in progress: B2 row; fill in the "Machine" line; set `Current item: B3`.
- Next: `B3-fresh-eyes-readme.md`.

- [ ] **Step 4: Write `qa/bootcamp/B3-fresh-eyes-readme.md` from this contract**

- Metadata: `training | B2 | raw findings in the progress file`.
- Goal: test the README the way a stranger would, before learning how Pitwall works.
- You will learn: what fresh-eyes testing is; recording a problem the moment you hit it; tearing
  down what you started.
- Before you start (give these lines verbatim):
  - "Coach: for this lesson, do not explain what Pitwall is or why a step exists. Help only with
    mechanics (typing, copy and paste, which terminal). Every stumble is data."
  - R13 check: `docker ps --format '{{.Names}}  {{.Ports}}'`. Expected: no line containing `5444`
    or `6380`. If one appears, run `make down` in the clone that started it.
- Steps:
  1. **A scratch folder:** `mkdir -p ~/qa-scratch/$(date +%Y%m%d) && cd ~/qa-scratch/$(date +%Y%m%d)`
     then `pwd`. Expected: the path ends in today's date. If a `pitwall` folder already exists
     there, use `~/qa-scratch/$(date +%Y%m%d)-2`.
  2. **Read first:** the tester reads the README top to bottom on GitHub
     (`gh repo view Buckeyes22/pitwall --web`) and says what is unclear. Expected: the coach
     records each note word for word under "Findings not yet filed", prefixed `B3 README <section>:`.
  3. **Skip the paid block:** the first Quick Start block (`pitwall serve ...`) needs a real
     RunPod account, so it belongs to tier 5 and is skipped. Record whether the README made that
     clear to a first-time reader.
  4. **Follow the registry path exactly:** one command at a time, as the README writes them:
     - the `git clone` and `cd pitwall`
     - `uv sync --frozen --extra dev`
     - `docker compose -f docker-compose.testinfra.yml up -d --wait`
     - the five `export` lines, copied from the README
     - `uv run pitwall db migrate`
     - `uv run pitwall init --non-interactive`

     Expected: each finishes without an error, and `init` ends by printing a smoke-test command.
     For every surprise, record the README section, the exact command, the output, and what the
     tester expected.
  5. **The second terminal:** the README says to start the API in a second terminal with
     `uv run pitwall-api`. The tester opens one, changes to the scratch clone, and runs it.
     Expected: log lines showing the server on `127.0.0.1:8080`. If different: record exactly
     what happened before giving any hint. Only then use the hint ladder.
  6. **The smoke call:** in the first terminal, run the README `curl` (or the one `init` printed).
     Expected: JSON containing `"dry_run": true`. Compare the response with the README's
     description and record any differences.
  7. **Useful onboarding commands:** run the three commands under that heading in the README, each
     followed by `echo "exit=$?"`. Expected: `exit=0` each time.
  8. **Tear down:** Ctrl-C the API; `docker compose -f docker-compose.testinfra.yml down`;
     `docker ps`. Expected: no `5444` or `6380`. The scratch clone can stay; `rm -rf` on it later
     is allowed (R2).
  9. **Debrief:** now the coach explains in three sentences what the tester just did: a local dry
     run of Pitwall's routing with fake settings. Then review each raw finding with the tester for
     its section, command, output, and expectation.
- Checkpoint: "Why did we do this before learning how Pitwall works?" Answer: fresh eyes see gaps
  that experts no longer notice.
- Done when: the README was attempted end to end, or up to where it broke, and every stumble is
  recorded with its section, command, output, and expectation.
- Record in progress: a B3 row with "<n> raw findings"; set `Current item: B4`.
- Next: `B4-git-and-github.md`.

- [ ] **Step 5: Write `qa/bootcamp/B4-git-and-github.md` from this contract**

- Metadata: `training | B3 | none`.
- Goal: read the project's history and use issues and pull requests with git and `gh`.
- You will learn: repository, commit, branch, pull request, review, CI check, issue, label, DCO
  sign-off. Link the cards `git-basics` and `github-issues-and-prs`.
- Steps:
  1. **Commits:** `git log --oneline -5`. Expected: five lines, each an id and a message. Explain
     the prefixes `feat`, `fix`, `docs`, and `test`.
  2. **Inside a commit:** `git show --stat HEAD | head -20`. Expected: the author, date, message,
     and changed files.
  3. **Branches:** `git branch --show-current`, `git switch -c practice/$USER`, `git switch main`,
     `git branch -d practice/$USER`. Expected: `main`; `Switched to a new branch`; back on `main`;
     `Deleted branch`.
  4. **Status:** `echo scratch > scratch-test.txt`, `git status --short`, `rm scratch-test.txt`,
     `git status --short`. Expected: `?? scratch-test.txt`, then no output. Explain untracked,
     modified, and staged, and why `qa/.work/` never shows.
  5. **Issues and labels:** `gh issue list --limit 10`, then `gh label list`. Expected: a list
     (maybe empty), then labels including `found-by-qa`, `needs-qa`, and four `severity:` labels.
  6. **Duplicate search:** `gh issue list --state all --search "README in:title,body"`. Expected:
     a list, possibly empty. Explain why we search before filing.
  7. **Pull requests:** `gh pr list --state merged --search "QA onboarding in:title" --limit 5`,
     then `gh pr view <N>`, `gh pr diff <N> --name-only | head -20`, and `gh pr checks <N>`.
     Expected: the pull request that added `qa/`; a description with a `## QA notes` section; its
     changed files; the `CI` check passed.
  8. **Sign-offs:** `git log -5 --format='%h %s%n  %(trailers:key=Signed-off-by)'`. Expected:
     `Signed-off-by:` lines. Explain the DCO and `git commit -s`, used from tier 4 on.
  9. **The website:** `gh repo view --web`. Show the Issues, Pull requests, and Actions tabs.
- Checkpoint: "What is the difference between an issue and a pull request? What does a sign-off
  certify?"
- Done when: the tester found the pull request's changed files and CI result with `gh`, and
  explained the sign-off.
- Record in progress: B4 row; set `Current item: B5`.
- Next: `B5-pitwall-tour.md`.

- [ ] **Step 6: Write `qa/bootcamp/B5-pitwall-tour.md` from this contract**

- Metadata: `training | B4 | none`.
- Goal: understand what Pitwall is, its parts, and what is safe to test.
- You will learn: link the cards `pitwall-in-one-page`, `capabilities-and-providers`,
  `dry-run-and-hermetic`, `routing-and-plans`, `cost-budget-and-guardrails`, `kill-switch`, and
  `the-tui`.
- Steps:
  1. **One page:** read `pitwall-in-one-page` together. Expected: the tester explains Pitwall in one
     sentence.
  2. **Architecture:** walk the flow line in `../../README.md#architecture` from clients to audit.
  3. **Surfaces:** a table of the CLI (`uv run pitwall ...`), the REST API (`uv run pitwall-api`,
     `/docs`), the TUI (`uv run pitwall dashboard`), MCP (`pitwall-mcp`, used by agents), the
     background services, and the test suites, each with the tier that tests it (tiers 1–2, 2,
     2, 2, 2, 1 and 4).
  4. **Start the stack:** `docker ps` check (R13);
     `docker compose -f docker-compose.testinfra.yml up -d --wait`; the five README `export`
     lines; `uv run pitwall db migrate`; `uv run pitwall init --non-interactive`. Expected: `init`
     prints a smoke-test command.
  5. **The CLI:** `uv run pitwall --help 2>&1 | head -40`. Expected: the command list. The coach
     points out the tier 5 commands on the list (`setup`, `serve`, `status`, `stop`, `runpod`,
     `runpod-onboard`, `volume-files`, `terminate-pod`): look, don't run.
  6. **The API docs page:** in a second terminal with the `export` lines, run
     `env -u PITWALL_API_TOKEN uv run pitwall-api`. Explain GC-17 in plain words: with a token set,
     even the docs page needs it, and on this machine only (loopback) Pitwall allows running
     without one. Expected: a log warning that authentication is off on loopback, then the server on
     `127.0.0.1:8080`. Open `http://127.0.0.1:8080/docs`. Expected: the Swagger page with route
     groups. Use "Try it out" on `GET /healthz`. Expected: `200` with `"ok": true`.
  7. **The TUI:** in a third terminal with the `export` lines, run `uv run pitwall dashboard`.
     Expected: the Overview. Press `p`, `m`, and `?`, then `q`. Explain the console trap (R2).
  8. **Dry run:** in the first terminal, run the README `curl`. Expected: `"dry_run": true`. Read
     `dry-run-and-hermetic` together.
  9. **Why tier 5 is locked:** the README key value is a placeholder, so every paid path fails at
     authentication; the rules forbid the rest. No command.
  10. **Stop everything:** Ctrl-C the API; `make down`; `docker ps`. Expected: no `5444` or `6380`.
- Checkpoint: explain dry run, hermetic, and why tier 5 is locked.
- Done when: the checkpoint is passed and everything is stopped.
- Record in progress: B5 row; set `Current item: B6`.
- Next: `B6-qa-fundamentals.md`.

- [ ] **Step 7: Write `qa/bootcamp/B6-qa-fundamentals.md` from this contract**

- Metadata: `training | B5 | first GitHub issues`.
- Goal: turn the B3 raw findings into real, useful GitHub issues.
- You will learn: expected versus actual; reproducing from a clean state; severity; evidence;
  duplicate search; the bug-report checklist; labels.
- Steps:
  1. **Three cards:** `expected-vs-actual`, `reproducing-a-bug`, `severity-and-priority`. Expected:
     the tester answers each card's check question.
  2. **Pick a finding:** the first B3 raw finding. The tester decides whether it is a product bug or
     a doc bug ([product bug or doc bug]{../handbook/bug-reports.md#product-bug-or-doc-bug}).
  3. **Reproduce it:** repeat the steps in the scratch clone and save the output with an absolute
     path, because the scratch clone is outside the working clone:
     `<command> 2>&1 | tee ~/pitwall/qa/.work/evidence/B6-<n>.txt`. Expected: the problem happens
     again. If different: record "could not reproduce" and decide with the tester whether to drop
     it, with the reason written down.
  4. **Duplicates:** `gh issue list --state all --search "<keywords>"`. Expected: no matching
     issue. If one matches, comment there instead.
  5. **Draft:** `cp qa/templates/bug-report.md qa/.work/notes/B6-<n>.md`. The tester writes it. The
     coach reviews it against [the checklist]{../handbook/bug-reports.md#the-bug-report-checklist}.
     The tester picks the severity ([scale]{../handbook/triage-and-labels.md#severity-scale}).
  6. **Post (R9):** after the tester approves:
     `gh issue create --title "<title>" --body-file qa/.work/notes/B6-<n>.md --label documentation --label found-by-qa --label severity:3-medium`
     (the labels as chosen). Expected: the new issue's address. Record it in `Completed`.
  7. **Repeat** for each B3 finding, or drop it with a reason in the progress file.
  8. **Unlock tier 1:** ask the Tier 1 questions from [the tier check]{../templates/tier-check.md}.
     Expected: all correct, with at most one hint each. Record `Unlocked tiers: 0, 1 (<date>)`,
     `Current tier: 1`, and `Current item: T1-01`.
- Checkpoint: "What makes repro steps good? Why search for duplicates first?"
- Done when: every B3 finding is filed or dropped with a reason, and tier 1 is unlocked.
- Record in progress: B6 row with the issue links.
- Next: [the mission ladder]{../missions/README.md}.

- [ ] **Step 8: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/bootcamp/*.md`
Expected: `shape check: 7 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/bootcamp`), and VC-secrets. Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add qa/bootcamp
git commit -s -m "docs(qa): bootcamp lessons B0 to B6"
```

---

### Task 10: Mission ladder and tier 1 (lane `10-tier1`)

**Lane:** ID `10-tier1`. You own `qa/missions/README.md` and the seven files in
`qa/missions/tier1-docs/`. Do not touch any other file.

**Interfaces:**
- Consumes: skeleton paths for every mission, `../handbook/bug-reports.md#the-bug-report-checklist`,
  `../templates/tier-check.md`, and the external anchors in the registry.
- Produces: the anchors `the-tiers`, `unlocking-a-tier`, `how-the-coach-picks-the-next-item`, and
  `mission-list`, used by coach files and B0.

- [ ] **Step 1: Write `qa/missions/README.md`**

```markdown
# Missions

Missions are real QA work. Each one ends with something real: an issue, a QA report on a pull
request, a test pull request, or a recorded result.

## The tiers

| Tier | What you do | Recommended mode |
| --- | --- | --- |
| 0 | [Bootcamp]{../bootcamp/B0-welcome.md}: foundations | training |
| 1 | Docs QA: follow the docs exactly and report where they are wrong | training |
| 2 | Manual and exploratory testing of the API, CLI, TUI, and MCP | training, then work |
| 3 | Acceptance testing: check pull requests before they merge | work |
| 4 | Automation: write tests and open test pull requests | training for T4-01 to T4-03, then work |
| 5 | Live testing against real providers (locked) | training, always |

## Unlocking a tier

| Tier | Unlocks when |
| --- | --- |
| 1 | B0–B6 are complete |
| 2 | T1-01 and T1-02 are complete; at least three issues filed that pass [the bug-report checklist]{../handbook/bug-reports.md#the-bug-report-checklist}; the maintainer agrees |
| 3 | T2-01 to T2-07 are complete; at least one exploratory session (T2-12) is done; the maintainer agrees |
| 4 | At least two QA reports are posted (T3-02 or T3-03); at least one fix is verified (T3-04); the maintainer agrees |
| 5 | The maintainer has given the tester a spend-capped key; T5-00 is passed; the maintainer agrees |

Before recording an unlock, run the questions for that tier from
[the tier check]{../templates/tier-check.md}. The maintainer's agreement comes from the weekly
check-in; ask the tester whether the maintainer agreed. Record it in the progress file as
`Unlocked tiers: ... N (<date>, confirmed by the maintainer)`.

Unlocked tiers stay open, and repeatable missions from lower tiers are always available.

## How the coach picks the next item

1. Severity 1 work (rule R14).
2. Pull requests labeled `needs-qa` (tier 3 and up).
3. Closed `found-by-qa` issues without `qa-verified` (tier 3 and up).
4. The lowest-numbered incomplete mission in the highest unlocked tier.
5. An [exploratory session]{tier2-manual/T2-12-exploratory-session.md}.

## Mission list

| Id | Mission | Repeatable | Output |
| --- | --- | --- | --- |
| T1-01 | [CONTRIBUTING walkthrough]{tier1-docs/T1-01-contributing-walkthrough.md} | no | issues |
| T1-02 | [Install checklist, part 1]{tier1-docs/T1-02-install-checklist-part-1.md} | no | issues |
| T1-03 | [Install checklist, part 2]{tier1-docs/T1-03-install-checklist-part-2.md} | no | issues |
| T1-04 | [CLI help vs CLI reference]{tier1-docs/T1-04-cli-help-vs-reference.md} | no | issues |
| T1-05 | [Journey catalog vs harness]{tier1-docs/T1-05-journey-catalog-vs-harness.md} | no | issues |
| T1-06 | [Configuration docs cross-check]{tier1-docs/T1-06-configuration-docs.md} | no | issues |
| T1-07 | [Troubleshooting guide messages]{tier1-docs/T1-07-troubleshooting-messages.md} | no | issues |
| T2-01 | [The REST API in a browser]{tier2-manual/T2-01-api-in-the-browser.md} | no | issues |
| T2-02 | [The REST API with curl]{tier2-manual/T2-02-api-with-curl.md} | no | issues |
| T2-03 | [The API's locks]{tier2-manual/T2-03-api-locks.md} | no | issues |
| T2-04 | [Money safety]{tier2-manual/T2-04-money-safety.md} | no | issues |
| T2-05 | [The CLI off the happy path]{tier2-manual/T2-05-cli-off-the-happy-path.md} | no | issues |
| T2-06 | [Database lifecycle guards]{tier2-manual/T2-06-database-guards.md} | no | issues |
| T2-07 | [TUI tour and exploratory pass]{tier2-manual/T2-07-tui-tour.md} | no | issues |
| T2-08 | [Pitwall as an agent tool (MCP)]{tier2-manual/T2-08-mcp-agent-client.md} | no | issues |
| T2-09 | [Background services]{tier2-manual/T2-09-background-services.md} | no | issues |
| T2-10 | [Journey harness vs your manual results]{tier2-manual/T2-10-journey-harness.md} | yes | issues |
| T2-11 | [Agent Routing component checks]{tier2-manual/T2-11-agent-routing-checks.md} | no | issues |
| T2-12 | [Exploratory session]{tier2-manual/T2-12-exploratory-session.md} | yes | notes, issues |
| T3-01 | [Practice acceptance on a merged pull request]{tier3-acceptance/T3-01-practice-acceptance.md} | no | local QA report |
| T3-02 | [QA a dependency update pull request]{tier3-acceptance/T3-02-dependency-update-pr.md} | yes | QA review |
| T3-03 | [Acceptance-test a needs-qa pull request]{tier3-acceptance/T3-03-needs-qa-pr.md} | yes | QA review, issues |
| T3-04 | [Verify fixes for issues you filed]{tier3-acceptance/T3-04-verify-fixes.md} | yes | issue comments |
| T3-05 | [Release candidate regression pass]{tier3-acceptance/T3-05-release-candidate-pass.md} | yes | sign-off report |
| T4-01 | [Reading and running the test suite]{tier4-automation/T4-01-reading-the-test-suite.md} | no | progress entry |
| T4-02 | [The pre-PR routine and reading CI]{tier4-automation/T4-02-pre-pr-routine.md} | no | progress entry |
| T4-03 | [Your first regression test]{tier4-automation/T4-03-first-regression-test.md} | no | pull request |
| T4-04 | [CLI contract tests]{tier4-automation/T4-04-cli-contract-tests.md} | yes | pull request |
| T4-05 | [API contract tests]{tier4-automation/T4-05-api-contract-tests.md} | yes | pull request |
| T4-06 | [Journey harness assertions]{tier4-automation/T4-06-journey-harness-assertions.md} | yes | pull request |
| T4-07 | [Property tests with Hypothesis]{tier4-automation/T4-07-property-tests.md} | yes | pull request |
| T5-00 | [Live safety briefing and key handling]{tier5-live/T5-00-live-safety-briefing.md} | no | tier check |
| T5-01 | [Read-only live checks]{tier5-live/T5-01-read-only-live-checks.md} | yes | issues |
| T5-02 | [Personal serving smoke]{tier5-live/T5-02-personal-serving-smoke.md} | yes | issues, cleanup evidence |
| T5-03 | [Pod lease lifecycle (L3)]{tier5-live/T5-03-pod-lease-lifecycle.md} | yes | issues, cleanup evidence |
| T5-04 | [Live audit (L4)]{tier5-live/T5-04-live-audit.md} | yes | audit report |
| T5-05 | [Serverless endpoints (L1, L2)]{tier5-live/T5-05-serverless-endpoints.md} | yes | issues, cleanup evidence |
```

- [ ] **Step 2: Write the seven tier 1 missions from these contracts**

Common to all tier 1 missions: Mode `training`; the Safety line (GC-11); "What counts as a finding"
lists: a command that fails as written; a missing prerequisite; a vague instruction; output that
contradicts the doc; a table row that points at something that does not exist. Every finding is
filed through [bug reports]{../../handbook/bug-reports.md} with `found-by-qa` and a severity.

- **`T1-01-contributing-walkthrough.md`**
  - Metadata: Tier 1, training, not repeatable, needs B6, output issues.
  - Why: CONTRIBUTING is the first doc a new contributor follows; wrong commands there fail people
    on day one.
  - Sources: `../../../CONTRIBUTING.md#dev-environment`, `#running-tests`, `#quality-gates`,
    `#pr-process`.
  - Safety extra: run `make test-int` only against the local test stack.
  - Setup: working clone on `main` after `git pull`; `docker ps` shows no `5444` or `6380`.
  - Steps:
    1. `uv sync --frozen --extra dev` then `uv run python --version`. Expected: `Python 3.14.7`.
       Note whether the doc's clone command makes clear that `your-fork` is a placeholder.
    2. `make test 2>&1 | tee qa/.work/evidence/T1-01-make-test.txt | tail -15` then
       `echo "exit=${PIPESTATUS[0]}"`. Expected: a summary with `passed` and no `failed`, and
       `exit=0`. It can take several minutes.
    3. `make up`, then `docker compose -f docker-compose.testinfra.yml ps`. Expected: the
       containers are listed. Record whether CONTRIBUTING's "wait a moment" tells a newcomer how
       to know when the databases are ready. Then
       `make test-int 2>&1 | tee qa/.work/evidence/T1-01-test-int.txt | tail -15` and
       `echo "exit=${PIPESTATUS[0]}"`. Expected: `passed`, no `failed`, and `exit=0`.
    4. `make down` then `docker ps`. Expected: no `5444` or `6380`.
    5. For each Quality Gates row, check that the named target exists:
       `grep -E '^(test|test-int|test-cov|sec|sec-semgrep|sec-test|sec-fuzz|mutation-gate|load-smoke|openapi-check|ci-tools):' Makefile`.
       Expected: every make target named in the table appears. Run `make sec-test 2>&1 | tail -5`.
       Expected: `passed`.
    6. Read PR Process and compare it with `.github/pull_request_template.md`. Expected: the same
       test commands in both.
  - Done when: steps 1–6 are run with evidence saved, and every mismatch is filed.
- **`T1-02-install-checklist-part-1.md`**
  - Metadata: Tier 1, training, not repeatable, needs T1-01, output issues.
  - Why: the install checklist is the project's own hermetic acceptance runbook; if it drifts,
    releases get signed off against wrong steps.
  - Sources: `../../../docs/operator/install-acceptance-checklist.md#prerequisites` through
    `#step-6--successful-inference-dry-run`.
  - Safety extra: the checklist says not to source internal-only environment files, so use only
    the values it lists. Its Step 1 clone is a scratch clone.
  - Setup: `docker ps` shows no `5444` or `6380`.
  - Steps: for each checklist step 1–6, run every command exactly as written, save the output as
    `qa/.work/evidence/T1-02-step<N>.txt` (with an absolute path once you are inside the temporary
    clone), and compare with the step's **Expected**. Tick the step in a note, or record a finding.
    Call out that Step 2 edits `.env` inside the temporary clone only. Record whether the
    `PITWALL_CLOUD_WORKER_IMAGE` placeholder in Step 2 is usable as written.
  - Done when: steps 1–6 are each ticked or filed; note in the progress file whether the stack was
    left running for part 2.
- **`T1-03-install-checklist-part-2.md`**
  - Metadata: Tier 1, training, not repeatable, needs T1-02, output issues.
  - Why: the same runbook covers the money and safety gates (cost, budget, kill switch, alerts,
    leak markers).
  - Sources: `#step-7--cost-estimation-and-budget-gate` through `#step-11--teardown`, and
    `#launch-gate`.
  - Safety extra: Step 8's kill switch runs with a placeholder key and no pods. If any step would
    need a real key, stop (R1).
  - Steps: as in T1-02, for checklist steps 7–11, then read "Launch gate" and record whether every
    item in it was covered by steps 1–11.
  - Done when: steps 7–11 are ticked or filed, and the teardown left `docker ps` clear of `5444`
    and `6380`.
- **`T1-04-cli-help-vs-reference.md`**
  - Metadata: Tier 1, training, not repeatable, needs T1-01, output issues.
  - Why: the CLI reference promises commands, flags, and exit codes that scripts depend on.
  - Sources: `../../../docs/sdlc/18-cli.md#3-command-inventory`, `#exit-codes`,
    `#6-failure-modes--error-types`.
  - Safety extra: for never-run-list commands, run only `--help`, never the command itself (R2).
  - Setup: the stack and the README `export` lines are needed only for step 4.
  - Steps:
    1. `uv run pitwall --help 2>&1 | tee qa/.work/evidence/T1-04-help.txt`. Expected: a `Usage:`
       line listing the commands, and one description per command.
    2. For each command in the usage line, find its section in `18-cli.md` (search for the name).
       Expected: every command is documented. A missing one is a finding.
    3. For each command, run `uv run pitwall <command> --help` and compare the flags with the
       reference. Expected: the same flags.
    4. Trigger the exit codes that are safe to trigger:
       - `env -u DATABASE_URL -u REDIS_URL uv run pitwall config check; echo "exit=$?"`.
         Expected: `missing-runtime-config` and `exit=78`, as the reference's note under the
         exit-code table says.
       - `uv run pitwall dashboard --bogus; echo "exit=$?"`. Expected: `unrecognized arguments`
         and `exit=2`.
       - `uv run pitwall mcp; echo "exit=$?"`. Expected: `arguments are required` and `exit=2`.
       - With the stack up and the `export` lines set: `uv run pitwall db reset; echo "exit=$?"`.
         Expected: refused without `--force`, and `exit=1`.
    5. `uv run pitwall config check --json | uv run python -m json.tool | head -5` with the
       `export` lines set. Expected: valid JSON.
  - Done when: every command in `--help` is found in the reference or filed; flags are compared;
    the four exit codes are checked.
- **`T1-05-journey-catalog-vs-harness.md`**
  - Metadata: Tier 1, training, not repeatable, needs T1-01, output issues.
  - Why: the catalog promises what each journey proves; if the harness checks less, the promise is
    false.
  - Sources: `../../../docs/operator/user-journey-catalog.md`,
    `../../../scripts/release/run-user-journeys.sh`.
  - Safety extra: this mission only reads files; it runs nothing but `grep` and `sed`.
  - Steps: for each hermetic row J01–J27, find the function with
    `grep -n '^j07()' scripts/release/run-user-journeys.sh` (for example), read it with
    `sed -n '<start>,<end>p' scripts/release/run-user-journeys.sh`, and compare what it asserts
    with the row's "Expected outcome" column. Expected: every claim in the row is checked in the
    function. A claim that is not checked is a finding (the J09 row was fixed this way in the pull
    request that added `qa/`).
  - Done when: all 27 rows are compared, each result is recorded in a note, and mismatches are
    filed.
- **`T1-06-configuration-docs.md`**
  - Metadata: Tier 1, training, not repeatable, needs T1-01, output issues.
  - Why: configuration docs that disagree cause failed starts and unsafe deployments.
  - Sources: `../../../README.md#configuration`, `../../../.env.example`,
    `../../../docs/sdlc/16-core-config.md`.
  - Safety extra: never put real values in `.env`; this mission only compares names.
  - Steps:
    1. For each variable in the README Configuration table, run
       `grep -n '<NAME>' .env.example docs/sdlc/16-core-config.md`. Expected: found in both.
    2. With the `export` lines set: `uv run pitwall config check; echo "exit=$?"`. Expected: a
       configuration report and `exit=0`.
    3. `env -u DATABASE_URL uv run pitwall config check; echo "exit=$?"`. Expected: a message
       naming `DATABASE_URL` and `exit=78`.
  - Done when: every README variable is checked in both files, and steps 2–3 are compared with
    `18-cli.md`.
- **`T1-07-troubleshooting-messages.md`**
  - Metadata: Tier 1, training, not repeatable, needs T1-04, output issues.
  - Why: an error message that doesn't match its troubleshooting entry leaves users stuck.
  - Sources: `../../../docs/operator/troubleshooting.md`.
  - Safety extra: `pitwall status` is on the never-run list except in the exact form below, with
    input redirected from `/dev/null`, so it can never offer to run setup.
  - Steps:
    1. `env -u RUNPOD_API_KEY -u DATABASE_URL HOME="$(mktemp -d)" .venv/bin/pitwall status < /dev/null; echo "exit=$?"`.
       Expected: ``no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY`` (write it
       as a double-backtick code span) and `exit=2`, matching the first troubleshooting entry.
       The temporary home guarantees that no saved credential is found. `.venv/bin/pitwall` is
       used instead of `uv run` because `uv` would otherwise try to rebuild its cache in the empty
       home.
    2. `env -u DATABASE_URL uv run pitwall warm-volume --model x --volume-id y --dry-run; echo "exit=$?"`,
       then the same with `--json`. Expected: `warm-volume needs DATABASE_URL (registry-backed launch planning)`
       and `exit=2`, then a JSON object with `"error": "missing_database_url"`, matching the
       registry-backed entry.
    3. List the entries that need live credentials (serve failures, orphaned pods, dead routes) in
       the progress file under "Coach notes" as tier 5 checks.
  - Done when: both reproducible entries are compared, and the live-only entries are listed.

- [ ] **Step 3: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/missions/README.md qa/missions/tier1-docs/*.md`
Expected: `shape check: 8 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/missions`), and VC-secrets. Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add qa/missions/README.md qa/missions/tier1-docs
git commit -s -m "docs(qa): mission ladder and tier 1 docs-QA missions"
```

---

### Tier 2 standard setup (restate it in each tier 2 mission's `## Setup`)

1. `docker ps --format '{{.Names}}  {{.Ports}}'`. Expected: no `5444` or `6380` from another clone.
2. `docker compose -f docker-compose.testinfra.yml up -d --wait`. Expected: both services started.
3. The five `export` lines from the README Quick Start, in every terminal the mission uses.
4. `uv run pitwall db migrate` and `uv run pitwall init --non-interactive`. Expected: both finish
   without errors.
5. When the mission needs the API: `uv run pitwall-api` in a second terminal. Expected: it is
   running on `127.0.0.1:8080`.

Every `curl` to a non-health route adds `-H "Authorization: Bearer $PITWALL_API_TOKEN"`, because
the README `export` lines turn API authentication on (GC-17). Admin calls also add
`-H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET"`.

### Task 11: Tier 2, part A (lane `11-tier2a`)

**Lane:** ID `11-tier2a`. You own `qa/missions/tier2-manual/T2-01-api-in-the-browser.md` through
`T2-06-database-guards.md` (six files). Do not touch any other file.

**Interfaces:**
- Consumes: registry anchors; `../../../docs/sdlc/02-api-rest.md#3-route-inventory` and
  `#6-failure-modes--error-types`; `../../../docs/operator/user-journey-catalog.md`;
  `../../../scripts/release/run-user-journeys.sh` functions `j01`, `j06`–`j08`, and `j12`–`j17`.
- Produces: the manual API, CLI, and database missions that tier 3 unlocks depend on.

- [ ] **Step 1: Write the six missions from these contracts**

Common: Mode `training` for a first run and `work` for repeats; not repeatable; output issues.
"What counts as a finding": a status code that differs from the journey or doc; an error body that
differs from `02-api-rest.md`; any 500 (severity 2); any authentication or budget bypass
(severity 1).

- **`T2-01-api-in-the-browser.md`** (needs T1-02)
  - Why: the Swagger page is how people explore an API. It must list working routes.
  - Sources: J07; `02-api-rest.md#3-route-inventory`.
  - Setup: the standard setup, but start the API with `env -u PITWALL_API_TOKEN uv run pitwall-api`
    and explain why (GC-17). Expected: a warning that authentication is off on loopback.
  - Steps:
    1. Open `http://127.0.0.1:8080/docs`. Expected: the Swagger page with route groups.
    2. Use "Try it out" on each J07 route: `/healthz`, `/health`, `/v1/health`,
       `/v1/capabilities`, `/v1/capabilities/embedding.demo`, `/v1/providers`,
       `/v1/providers/prov_demo_runpod_lb`, `/v1/providers/prov_demo_runpod_lb/health`, and
       `/openapi.json`. Expected: 200 each, with fields that match the route inventory.
    3. `POST /v1/inference` with body `{"capability":"embedding.demo","texts":["hello"],"dry_run":true}`.
       Expected: 200, `dry_run` true, `selected_provider_id` `prov_demo_runpod_lb`.
    4. The same with `"capability":"does.not.exist"`. Expected: 404 with a JSON error.
    5. Stop the API, start it with plain `uv run pitwall-api` (token on), and reload `/docs`.
       Expected: 401. Record whether any doc tells a README follower that the docs page then needs
       a token.
- **`T2-02-api-with-curl.md`** (needs T2-01)
  - Why: scripts and programs use the API from the command line, and they depend on the exact
    status codes.
  - Sources: J01, J08, J17; `02-api-rest.md#6-failure-modes--error-types`.
  - Setup: the standard setup, with the API token on.
  - Steps:
    1. The README Quick Start `curl`. Expected: `"dry_run": true` and
       `"selected_provider_id": "prov_demo_runpod_lb"`.
    2. `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz`. Expected: `200`,
       with no token needed.
    3. The README `curl` with `-i` and the capability changed to `does.not.exist`. Expected:
       `HTTP/1.1 404` and a JSON error body.
    4. `curl -s -i -X POST http://127.0.0.1:8080/v1/inference -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"nope":1}'`.
       Expected: `422`.
    5. For `GET /v1/jobs/wkl_doesnotexist/status`, `GET /v1/jobs/wkl_doesnotexist/result`, and
       `POST /v1/jobs/wkl_doesnotexist/cancel`:
       `curl -s -i -X <METHOD> -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080<path>`.
       Expected: `404` with a JSON error, three times.
    6. Compare every error body with the failure-modes section. Expected: the same shape.
- **`T2-03-api-locks.md`** (needs T2-02)
  - Why: authentication and rate limits are what stop strangers from spending the operator's
    money.
  - Sources: J12, J13, J14; `../../../docs/sdlc/14-security.md`.
  - Setup: the standard setup, with the API token on.
  - Steps:
    1. `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/v1/capabilities`, then the
       same with the bearer header, then `/healthz` without it. Expected: `401`, `200`, `200`.
    2. `POST /v1/admin/capabilities` with the bearer, `-H 'Content-Type: application/json'`,
       `-d '{}'`, and no secret; then with `-H "X-Pitwall-Secret: $RANDOM"`. Expected: `401` or
       `403` both times.
    3. With the real admin header, body
       `{"name":"embedding.qa","version":"1.0.0","class":"embedding","cost_mode":"per_request"}`.
       Expected: `201` and an `id`. Then `POST /v1/admin/capabilities/<id>/disable` and
       `/enable`. Expected: `200` each. Then `POST /v1/admin/audit-capability/embedding.demo`.
       Expected: `200`.
    4. Stop the API. Start it with `PITWALL_INBOUND_RATE_LIMIT=3/60s uv run pitwall-api`. Send the
       bearer request to `/v1/capabilities` six times. Expected: `200`s, then `429`. Then
       `curl -s -D - -o /dev/null -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/v1/capabilities | grep -i retry-after`.
       Expected: a `Retry-After` header. Restart the API normally afterwards.
  - Extra finding rule: a `200` where `401` or `403` is expected is severity 1.
- **`T2-04-money-safety.md`** (needs T2-03)
  - Why: the budget gate, the proxy guards, the kill switch, and guardrails keep money and secrets
    safe.
  - Sources: J15, J16, J21; `../../../docs/sdlc/05-cost-budget.md`;
    `../../../docs/sdlc/18-cli.md#guardrails-status--guardrails-preview`.
  - Safety extras:
    - Steps 1 and 2 send non-dry-run requests on purpose, with the placeholder key and a near-zero
      budget. The gate must refuse them before any provider call. This is the only mission in
      tiers 1–4 that does this.
    - The kill-switch drill always sends `"terminate_compute": false`.
    - Guardrail test values are made up on the spot and stored only in `qa/.work/notes/`.
  - Setup: the standard setup, without starting the API.
  - Steps:
    1. Start `PITWALL_MONTHLY_BUDGET_USD=0.000001 uv run pitwall-api`. Then
       `curl -s -i -X POST http://127.0.0.1:8080/v1/inference -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"capability":"embedding.demo","texts":["hello"]}'`.
       Expected: `402` with a JSON body. Any other code, especially a provider error, is
       severity 1.
    2. `curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/openai/embedding.demo/v1/chat/completions -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"model":"m","messages":[{"role":"user","content":"hi"}]}'`.
       Expected: `402`.
    3. Copy the URL-injection path from the first `http_code` call in `j16()`
       (`grep -n -A4 '^j16()' scripts/release/run-user-journeys.sh`) and POST to it on port 8080
       with the bearer. Expected: a 4xx code. A 2xx or 5xx is severity 1.
    4. Guardrails:
       - `uv run pitwall guardrails status`. Expected: the mode and a `Guardrail rules` table.
       - `uv run pitwall guardrails preview --payload '{"texts":["hello"]}'; echo "exit=$?"`.
         Expected: decision `allow`, `exit=0`.
       - Pick one secret rule from the table. The coach writes a made-up value of that shape into
         `qa/.work/notes/guardrail-secret.json` as `{"texts":["<value>"]}`. Then
         `uv run pitwall guardrails preview --payload "$(cat qa/.work/notes/guardrail-secret.json)"; echo "exit=$?"`.
         Expected: the decision matches that rule's balanced action, and `exit=0`.
       - Repeat with a made-up email address such as `jane.doe@example.com`. Expected: the
         decision matches the personal-data rule's action.
       - `uv run pitwall guardrails preview --payload '{bad'; echo "exit=$?"`. Expected: `exit=2`.
    5. Kill-switch drill, last: restart the API normally.
       - POST `/v1/admin/kill-switch` with the bearer and `-d '{"reason":"qa drill"}'`, but no
         secret. Expected: `401` or `403`.
       - With the admin header and `-d '{"reason":"qa drill","terminate_compute":false}'`.
         Expected: `200` with a report.
       - Then `/healthz`. Expected: `200`.
       - Tear down with `make down` so no kill state leaks into later missions.
- **`T2-05-cli-off-the-happy-path.md`** (needs T1-04)
  - Why: real users mistype flags and forget settings, and the CLI must fail clearly and with the
    documented exit code.
  - Sources: `../../../docs/sdlc/18-cli.md#exit-codes` and `#6-failure-modes--error-types`.
  - Safety extra: use only these commands: `db status`, `db migrate`, `init`,
    `create-capability`, `seed`, `config check`, `set-provider-health`, `register-endpoint`,
    `leases list`, `cost summary`, `cost workloads`, `burn-rate`, `guardrails status`,
    `guardrails preview`, `models list`, `models show`. Never add wrong arguments to a command on
    the never-run list.
  - Setup: the standard steps 1–4, without the API.
  - Steps: for at least ten of those commands, record a row in a notes table for each of these
    runs:
    - `uv run pitwall <command> --bogus; echo "exit=$?"`
    - `env -u DATABASE_URL uv run pitwall <command>; echo "exit=$?"`
    - `uv run pitwall <command> --json | uv run python -m json.tool > /dev/null; echo "exit=$?"`,
      where the command accepts `--json`
    - `uv run pitwall <command> --help; echo "exit=$?"`

    Expected: a clear message and a non-zero code for the first two, compared with the exit-code
    table; `exit=0` for valid JSON; usage text for help.
  - Done when: at least ten commands have all four rows, and every mismatch with the reference is
    filed.
- **`T2-06-database-guards.md`** (needs T2-05)
  - Why: `db reset` destroys data, and its guards must refuse unsafe targets.
  - Sources: J06; `18-cli.md#exit-codes`.
  - Safety extra: `db reset` runs only against the local stack. Step 4 points at a made-up remote
    host on purpose, to prove the refusal.
  - Setup: the standard steps 1–4.
  - Steps:
    1. `uv run pitwall db status; echo "exit=$?"`. Expected: the migration status and `exit=0`.
    2. `uv run pitwall db migrate; echo "exit=$?"`, twice. Expected: `exit=0` both times, and the
       second run applies nothing new.
    3. `uv run pitwall db reset; echo "exit=$?"`. Expected: refused without `--force`, and
       `exit=1`.
    4. `REMOTE_URL="${DATABASE_URL/127.0.0.1/db.example.invalid}"`, then
       `DATABASE_URL="$REMOTE_URL" uv run pitwall db reset --force; echo "exit=$?"`. Explain the
       `${VAR/old/new}` substitution. Expected: refused because the host is not local, and
       `exit=1`.
    5. `uv run pitwall db reset --force; echo "exit=$?"`, then `uv run pitwall db status`,
       `uv run pitwall db migrate`, and `uv run pitwall init --non-interactive`. Expected:
       `exit=0`, then pending migrations, then everything re-applied and the demo data back.

- [ ] **Step 2: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/missions/tier2-manual/T2-0[1-6]-*.md`
Expected: `shape check: 6 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/missions/tier2-manual`), and VC-secrets. Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add qa/missions/tier2-manual/T2-0[1-6]-*.md
git commit -s -m "docs(qa): tier 2 missions for the API, money safety, CLI, and database guards"
```

### Task 12: Tier 2, part B (lane `12-tier2b`)

**Lane:** ID `12-tier2b`. You own `qa/missions/tier2-manual/T2-07-tui-tour.md` through
`T2-12-exploratory-session.md` (six files). Do not touch any other file.

**Interfaces:**
- Consumes: registry anchors; `../../../docs/sdlc/18-cli.md`, `03-mcp-server.md`,
  `09-webhooks.md`, `10-reconciler-lifecycle.md`, `13-observability.md`;
  `../../../packages/agent-routing/CONTRIBUTING.md`, `../../../packages/agent-routing/docs/doctor.md`;
  `../../handbook/exploratory-testing.md#pitwall-charter-list`.
- Produces: the TUI, MCP, services, harness, component, and exploratory missions.

- [ ] **Step 1: Write the six missions from these contracts**

- **`T2-07-tui-tour.md`** (training; needs T2-01)
  - Why: operators live in the TUI. Crashes, missing views, and unreadable layouts are findings.
  - Sources: J09; `18-cli.md` TUI sections; `../../../docs/support-matrix.md`.
  - Safety extras:
    - Start the TUI only in a terminal with the README `export` lines (GC-16).
    - Never finish a type-to-confirm dialog.
    - Do not press `g` or `a` on the Providers view, and do not open a model's hardware fit (`g`
      in a model's detail). They contact providers and belong to tier 5.
  - Setup: the standard steps 1–4.
  - Steps:
    1. `uv run pitwall dashboard`. Expected: the Overview view, with keys in the footer.
    2. Press `o`, `p`, `l`, `m`, `s`, `d`, `t`, `c`, `e`, `a` in turn. Expected: ten views open
       with no traceback. Note each view's title.
    3. Press `:`, type `cost`, press Enter. Expected: the Cost view. Press `?`. Expected: the key
       list. Escape closes it.
    4. In Models, press `/` and type part of a model name. Expected: the rows filter. Escape
       clears the filter.
    5. In Models, press Enter on a row. Expected: the dossier. Escape goes back.
    6. In Serve (`s`), read the form without submitting. In Pods (`d`), open a stop confirmation
       if there is anything to open, then cancel. Record what each confirmation asks you to type.
    7. Make the window narrow (under 100 columns) and short, then revisit Models and Providers.
       Expected: compact tables that are still readable.
    8. Quit, `make down`, and start the dashboard again with the `export` lines set. Expected: the
       Overview shows `Overview unavailable: <reason>`, with no traceback and no
       password. Quit, then restart the stack and re-run migrate and init.
    9. Compare the ten views with the README "Operator TUI" row and the support matrix. Expected:
       the same ten names.
  - Findings: a traceback (severity 2); connection details or secrets on screen (severity 1); a
    view missing or misnamed; keys that differ from help; unreadable layouts.
- **`T2-08-mcp-agent-client.md`** (training; needs T2-02)
  - Why: AI agents use Pitwall through MCP, so the tester's own agent becomes the test client.
  - Sources: J10, J11; `03-mcp-server.md`.
  - Safety extras: use Claude Code for this mission (switch agents as the agent guide describes);
    the wrapper script lives only in `qa/.work/`.
  - Setup: the standard steps 1–4.
  - Steps:
    1. Create `qa/.work/pitwall-mcp.sh` in an editor. Line 1: `#!/usr/bin/env bash`. Line 2:
       `cd "$(dirname "$0")/../.."`. Then the five README `export` lines, pasted by the tester.
       Last line: `exec uv run pitwall-mcp`. Then `chmod +x qa/.work/pitwall-mcp.sh`.
       Expected: `ls -l qa/.work/pitwall-mcp.sh` shows `x` permissions.
    2. `claude mcp add --scope local pitwall-local -- "$PWD/qa/.work/pitwall-mcp.sh"`, then
       `claude mcp list`. Expected: `pitwall-local` is listed as connected.
    3. Exit Claude Code, start it again at the repository root, and type the kickoff sentence.
       Expected: the coach resumes from the progress file.
    4. Ask: "Using the pitwall-local MCP server, list the capabilities." Expected: the agent calls
       `pitwall_list_capabilities` and shows `embedding.demo`.
    5. Ask: "Using pitwall-local, submit a dry-run inference for embedding.demo with the text
       hello." Expected: a result with `dry_run` true.
    6. Explore: ask for a capability that does not exist, and ask for a non-dry-run request.
       Expected: clear errors, and the same gates as the API.
    7. `PITWALL_MCP_TRANSPORT=sse uv run pitwall-mcp; echo "exit=$?"`. Expected: an immediate
       non-zero exit and a message that only stdio is supported.
    8. Keep `pitwall-local` registered for the QA smoke set, or remove it with
       `claude mcp remove --scope local pitwall-local`. Record which in the progress file.
- **`T2-09-background-services.md`** (training; needs T2-03)
  - Why: the webhook receiver, reconciler, and cost exporter fail quietly unless someone checks.
  - Sources: J18, J19, J20; `09-webhooks.md`, `10-reconciler-lifecycle.md`,
    `13-observability.md`.
  - Setup: the standard steps 1–4.
  - Steps:
    1. `openssl rand -hex 16 > qa/.work/notes/webhook-secret.txt`. In terminals 1 and 2:
       `export PITWALL_WEBHOOK_SECRET="$(cat qa/.work/notes/webhook-secret.txt)"`.
    2. Terminal 2: `PITWALL_WEBHOOK_RECEIVER_PORT=18082 uv run pitwall-webhook`. Terminal 1:
       `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18082/healthz`. Expected:
       `200`.
    3. `printf '%s' '{"id":"job-qa-1","status":"COMPLETED"}' > qa/.work/notes/webhook.json`, then
       `curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18082/webhooks/runpod -H 'Content-Type: application/json' --data-binary @qa/.work/notes/webhook.json`.
       Expected: `401` (unsigned).
    4. Sign the payload:
       `SIG="$(uv run python -c "import os, pathlib; from pitwall.webhook_dispatcher.signer import sign; print(sign(pathlib.Path('qa/.work/notes/webhook.json').read_bytes(), os.environ['PITWALL_WEBHOOK_SECRET']))")"`.
       Then post it with `-H "X-Pitwall-Webhook-Signature: $SIG"`, twice. Expected: `200`, then
       `200` with `"duplicate": true`.
    5. `uv run python -m pitwall.reconciler check; echo "exit=$?"`, then
       `REDIS_URL='not-a-dsn' uv run python -m pitwall.reconciler check; echo "exit=$?"`, then
       `timeout 8 uv run pitwall-reconciler; echo "exit=$?"`. Expected: `exit=0`; a clear message
       and a non-zero exit; `exit=124` with no `Traceback`.
    6. Terminal 2: `PITWALL_COST_EXPORTER_PORT=18090 uv run pitwall-cost-exporter`. Terminal 1:
       `curl -s http://127.0.0.1:18090/metrics | grep '^pitwall_' | head -5`. Expected: metric
       lines starting with `pitwall_`. Compare the names with `13-observability.md`.
  - Findings: `200` for an unsigned delivery (severity 1); a replay processed twice (severity 2);
    tracebacks; missing metrics.
- **`T2-10-journey-harness.md`** (work; repeatable; needs T2-01 to T2-09)
  - Why: automation and manual testing should agree. When they don't, one of them is wrong.
  - Sources: `../../handbook/regression-and-release.md#full-journey-harness`.
  - Safety extra: the harness resets the database, so run it only against the local stack.
  - Setup: standard steps 1–3; nothing else running:
    `ss -tln | grep -E ':(8080|1808[0-9]|18090) '`. Expected: no output.
  - Steps:
    1. `bash scripts/release/run-user-journeys.sh 2>&1 | tee qa/.work/evidence/T2-10-journeys.txt | tail -40`,
       then `echo "exit=${PIPESTATUS[0]}"`. Expected: `0 failed` and `exit=0`.
    2. For every journey the tester ran by hand in T2-01 to T2-09, compare the two results. For a
       disagreement, find which one the docs support, and file.
    3. `uv run pitwall db migrate`, `uv run pitwall init --non-interactive`, then `make down`.
- **`T2-11-agent-routing-checks.md`** (work; needs T2-02)
  - Why: Agent Routing is a separate component the tester's own agents can use, with its own tests
    and health check.
  - Sources: `../../../packages/agent-routing/CONTRIBUTING.md`,
    `../../../packages/agent-routing/docs/doctor.md`.
  - Safety extra: run `doctor` only with a temporary home, so it cannot touch the tester's real
    agent configuration. Harness CLIs that it probes may create first-run files in that temporary
    home; that is expected.
  - Setup: none. No test stack.
  - Steps:
    1. `cd packages/agent-routing`,
       `pitwall_uv() { uvx --isolated --from 'uv==0.11.19' uv "$@"; }`, then
       `pitwall_uv sync --frozen --group dev --python 3.14.7 2>&1 | tail -3`. Expected: no error.
    2. `.venv/bin/python -m unittest discover -s tests 2>&1 | tail -4`. Expected: `Ran <n> tests`
       and `OK` (skipped tests are fine). It can take several minutes.
    3. `HOME="$(mktemp -d)" .venv/bin/python scripts/pitwall-agent-routing doctor 2>&1 | tail -3`,
       then `echo "exit=${PIPESTATUS[0]}"`. Expected: a last line starting `doctor:` with counts
       where `'fail': 0`, and `exit=0`. Compare the check groups with `docs/doctor.md`.
    4. `.venv/bin/python scripts/pitwall-agent-routing --help`. Expected: usage text. Compare the
       subcommands with the component README.
    5. `cd ../..`.
- **`T2-12-exploratory-session.md`** (work; repeatable; needs T2-01)
  - Why: scripted checks cover known paths, and exploration finds what nobody wrote down.
  - Sources: `../../handbook/exploratory-testing.md`.
  - Setup: whatever the charter needs, from the standard setup.
  - Steps:
    1. Pick a charter from the [Pitwall charter list]{../../handbook/exploratory-testing.md#pitwall-charter-list}
       or write one. Expected: one sentence with an area, a technique, and a kind of problem.
    2. `cp qa/templates/exploratory-session.md qa/.work/notes/explore-$(date +%F)-<topic>.md`.
    3. Record the commit (`git rev-parse --short HEAD`) and the setup in the notes.
    4. Explore with the heuristics, logging every notable thing in the notes as you go.
    5. Stop at the end of the session and write the areas not reached.
    6. File the findings and move the questions into the progress file.
  - Done when: the notes are saved and every finding is filed or recorded as a question.

- [ ] **Step 2: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/missions/tier2-manual/T2-0[7-9]-*.md qa/missions/tier2-manual/T2-1[0-2]-*.md`
Expected: `shape check: 6 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/missions/tier2-manual`), and VC-secrets. Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add qa/missions/tier2-manual/T2-0[7-9]-*.md qa/missions/tier2-manual/T2-1[0-2]-*.md
git commit -s -m "docs(qa): tier 2 missions for the TUI, MCP, services, harness, Agent Routing, and exploration"
```

---

### Task 13: Tier 3 acceptance missions (lane `13-tier3`)

**Lane:** ID `13-tier3`. You own the five files in `qa/missions/tier3-acceptance/`. Do not touch
any other file.

**Interfaces:**
- Consumes: `../../handbook/acceptance-testing.md` (all anchors),
  `../../handbook/regression-and-release.md#qa-smoke-set`, `#release-candidate-pass`,
  `#sign-off-report`, `../../handbook/triage-and-labels.md#queries-the-coach-uses`, and the QA report
  template.
- Produces: the acceptance workflow missions. The Task 23 pull request's `## QA notes` are T3-01's
  practice material.

- [ ] **Step 1: Write the five missions from these contracts**

Common: Mode `work` (the coach may drive after announcing, and the tester approves every post);
"What counts as a finding": a failed acceptance criterion, a regression in the smoke set, or
behavior that contradicts the pull request's own description. Every mission ends with
`git switch main && uv sync --frozen --extra dev`.

- **`T3-01-practice-acceptance.md`** (not repeatable; needs tier 3; output a local QA report)
  - Why: practice the whole loop on a pull request where nothing is at stake.
  - Sources: the merged pull request that added `qa/`;
    `../../handbook/acceptance-testing.md#tester-side-the-acceptance-run`.
  - Safety extra: nothing is posted in this mission. The report stays in `qa/.work/notes/`.
  - Steps:
    1. `gh pr list --state merged --search "QA onboarding in:title" --limit 5`, then
       `gh pr view <N>`. Expected: the pull request, with a `## QA notes` section listing F1–F9.
    2. On `main` (which contains it), check each F-criterion with the command its QA notes give,
       saving evidence. Expected: every criterion passes. If one fails, that is a real finding;
       file it.
    3. `cp qa/templates/qa-report.md qa/.work/notes/T3-01-qa-report.md` and fill it in.
    4. The coach reviews the draft against
       [the QA report]{../../handbook/acceptance-testing.md#the-qa-report}. Expected: every
       criterion has a command, a result, and evidence.
- **`T3-02-dependency-update-pr.md`** (repeatable; output a posted QA review)
  - Why: dependency updates break things quietly, and Dependabot opens them regularly.
  - Sources: `gh pr list --author app/dependabot`;
    `../../handbook/acceptance-testing.md#outcomes-and-labels`.
  - Steps:
    1. `gh pr list --author app/dependabot`. Pick one that touches a surface the tester knows (for
       example `textual` for the TUI, or `fastapi` for the API). Expected: at least one open pull
       request, or record "none open" and stop.
    2. `gh pr checkout <N>`, then `uv sync --frozen --extra dev`, then `make test 2>&1 | tail -15`.
       Expected: no failures.
    3. A targeted smoke test of the affected surface: the T2-07 steps 1–3 for `textual`; the T2-02
       steps 1–5 plus `make openapi-check` for `fastapi`. Expected: the same results as before.
    4. Draft the report, get the tester's approval, post it with the matching review command, then
       `gh pr edit <N> --add-label qa-passed` (or `qa-failed`).
- **`T3-03-needs-qa-pr.md`** (repeatable; output a posted QA review and issues)
  - Why: this is the tester's main job in tier 3. The maintainer asks for QA, and the tester gives
    a verdict with evidence.
  - Sources: the pull request's `## QA notes` and linked plan;
    `../../handbook/acceptance-testing.md`.
  - Steps: follow
    [the tester side]{../../handbook/acceptance-testing.md#tester-side-the-acceptance-run}
    steps 1–12, one step per message. Criteria that need live credentials go under "Skipped, and
    why".
  - Done when: every criterion has a result with evidence, the smoke set is run, the review is
    posted, the label is swapped, and the issues are filed and linked.
- **`T3-04-verify-fixes.md`** (repeatable; output issue comments)
  - Why: an issue is not truly done until QA confirms the fix on `main`.
  - Sources: `../../handbook/triage-and-labels.md#issue-lifecycle`.
  - Steps:
    1. `gh issue list --state closed --search "label:found-by-qa -label:qa-verified"`. Expected: a
       list, possibly empty.
    2. For each issue: `gh issue view <N>`; `git switch main && git pull`;
       `uv sync --frozen --extra dev`; re-run the issue's repro steps and save the evidence.
    3. If fixed: `gh issue comment <N> --body-file qa/.work/notes/verify-<N>.md` (the command,
       output, and commit), then `gh issue edit <N> --add-label qa-verified`. If not fixed:
       `gh issue reopen <N>` and a comment with the evidence. The tester approves each post.
- **`T3-05-release-candidate-pass.md`** (repeatable; output a sign-off report)
  - Why: before a release, someone must run every hermetic gate and the journeys and sign off with
    evidence.
  - Sources: `../../../docs/operator/release-testing-checklist.md`;
    `../../handbook/regression-and-release.md#release-candidate-pass` and `#sign-off-report`.
  - Safety extra: live tiers are never part of this pass.
  - Steps: follow the release candidate pass in the handbook, sections 1–7 in order, then the full
    journey harness, then draft the sign-off report, get the tester's approval, and post it where
    the maintainer asked.

- [ ] **Step 2: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/missions/tier3-acceptance/*.md`
Expected: `shape check: 5 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/missions/tier3-acceptance`), and VC-secrets. Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add qa/missions/tier3-acceptance
git commit -s -m "docs(qa): tier 3 acceptance-testing missions"
```

### Task 14: Tier 4 automation missions (lane `14-tier4`)

**Lane:** ID `14-tier4`. You own the seven files in `qa/missions/tier4-automation/`. Do not touch
any other file.

**Interfaces:**
- Consumes: `../../handbook/test-automation.md` (all anchors),
  `../../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation`, `../../../tests/conftest.py`,
  `../../../tests/_hermetic_env.py`, `../../../tests/fakes/`.
- Produces: the automation missions.

- [ ] **Step 1: Write the seven missions from these contracts**

Common: Safety extra for every tier 4 mission: change test files only, on a `test/<short-name>`
branch (R8); the temporary un-fix in T4-03 is never committed. "What counts as a finding": a test
that passes when it should fail, a flaky result, or a gate that disagrees with CONTRIBUTING.

- **`T4-01-reading-the-test-suite.md`** (training; not repeatable; output a progress entry)
  - Why: before writing tests, learn how this suite is laid out and run.
  - Sources: `17-testing-strategy.md` sections 1–3; `tests/conftest.py`; `tests/_hermetic_env.py`;
    `tests/fakes/`.
  - Steps:
    1. `ls tests | head -40`. Expected: folders per area (`api`, `cli`, `property`, ...).
    2. `grep -n -A14 'markers = \[' pyproject.toml`. Expected: the marker list.
    3. `sed -n '1,30p' tests/_hermetic_env.py`. Expected: the variables removed before tests run.
       Explain why.
    4. `uv run pytest -q tests/cli/test_cli_output.py 2>&1 | tail -3`. Expected: `passed`.
    5. `uv run pytest --collect-only -q tests/cli/test_cli_output.py | head -5`. Pick one test id
       and run `uv run pytest -q "<id>"`. Expected: `1 passed`.
    6. `uv run pytest -q -m "security and not fuzz" tests/security 2>&1 | tail -3`. Expected:
       `passed`.
    7. Open the chosen test in an editor, change one expected value, and run it. Expected: a
       failure with an assertion diff. Then `git checkout -- <file>`, run it again, and check
       `git status --short`. Expected: `passed`, and a clean tree.
- **`T4-02-pre-pr-routine.md`** (training; not repeatable; output a progress entry)
  - Why: every pull request must pass the same gates CI runs, and be signed off.
  - Sources: `../../../CONTRIBUTING.md#quality-gates`, `#pr-process`;
    `../../handbook/test-automation.md#pre-pr-routine`, `#reading-ci-failures`.
  - Steps:
    1. `git switch -c test/practice-$USER`. Expected: a new branch.
    2. `uv run ruff check .` and `uv run ruff format --check .`. Expected: no errors.
    3. `make test 2>&1 | tail -15`. Expected: no failures.
    4. Pick a recent merged pull request: `gh pr list --state merged --limit 3`, then
       `gh pr checks <N>`. Read one job's log: `gh run list --limit 3`, then
       `gh run view <run-id> --log | tail -30`. Expected: the tester can say what the job ran.
    5. `git switch main && git branch -D test/practice-$USER`. Expected: the practice branch is
       gone. Nothing was pushed.
- **`T4-03-first-regression-test.md`** (training; not repeatable; output a pull request)
  - Why: find, then fix — every production fix needs a test that fails without it.
  - Sources: `../../handbook/test-automation.md#find-then-fix`; a closed `found-by-qa` issue with
    `qa-verified`.
  - Steps:
    1. `gh issue list --state closed --label qa-verified --limit 10`. Pick one whose fix is a code
       change. Find the fix: `gh issue view <N>` shows the closing pull request; then
       `gh pr view <P> --json mergeCommit --jq .mergeCommit.oid`.
    2. `git switch main && git pull && git switch -c test/regression-<N>`.
    3. Write a hermetic test next to the existing tests for that surface, reusing their helpers.
    4. `uv run pytest -q <test-file>::<test-name>`. Expected: `1 passed`.
    5. Prove it catches the bug with the find-then-fix commands. Expected: fail, then pass.
       `git status --short` shows only the new test.
    6. The pre-PR routine, then open the pull request (tester approves the description, which says
       `Refs #<N>`). Expected: CI is green; answer review comments.
- **`T4-04-cli-contract-tests.md`** (work; repeatable; output a pull request)
  - Why: T2-05 found CLI error paths that no test protects.
  - Sources: `tests/cli/` (for example `tests/cli/test_cli_dispatch.py` and
    `tests/cli/test_cli_output.py`); the tester's T2-05 notes.
  - Steps: pick one uncovered error path from the T2-05 notes (confirm with
    `grep -rn '<flag or message>' tests/cli`); write a test in the matching `tests/cli/` file's
    style; run it; the pre-PR routine; open the pull request.
  - Done when: the pull request is green and merged, or review feedback is addressed.
- **`T4-05-api-contract-tests.md`** (work; repeatable; output a pull request)
  - Why: T2-02 and T2-03 exercised error shapes and locks that must stay stable.
  - Sources: `tests/api/` (for example `tests/api/_contract_helpers.py` and
    `tests/api/test_capabilities_contract.py`); `02-api-rest.md#6-failure-modes--error-types`.
  - Steps: as in T4-04, for one API error shape or authorization case.
- **`T4-06-journey-harness-assertions.md`** (work; repeatable; output a pull request)
  - Why: T1-05 found catalog rows that claim more than their `jNN()` function checks.
  - Sources: `../../../scripts/release/run-user-journeys.sh`,
    `../../../docs/operator/user-journey-catalog.md`; the tester's T1-05 notes.
  - Steps: pick one row. Add the missing assertion to its function, following the file's
    `journey_fail` and `journey_pass` style. Run the full harness against the local stack
    (`../../handbook/regression-and-release.md#full-journey-harness`). Expected: the journey
    passes. Update the catalog row in the same pull request if its wording changes. Open the pull
    request.
  - Note: this changes a test script and the catalog. It is test work, so it is allowed in tier 4.
- **`T4-07-property-tests.md`** (work; repeatable; output a pull request)
  - Why: property tests check a rule across thousands of generated inputs.
  - Sources: `tests/property/` (for example `tests/property/conftest.py`);
    `17-testing-strategy.md#2-verification-tracks`.
  - Steps: read two existing property tests; pick one pure-logic rule with the maintainer; write a
    `@pytest.mark.property` test; run
    `HYPOTHESIS_PROFILE=ci uv run pytest -m "property and not live" -p no:randomly <file>`.
    Expected: `passed`. Then the pre-PR routine and a pull request.

- [ ] **Step 2: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/missions/tier4-automation/*.md`
Expected: `shape check: 7 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/missions/tier4-automation`), and VC-secrets. Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add qa/missions/tier4-automation
git commit -s -m "docs(qa): tier 4 test-automation missions"
```

### Task 15: Tier 5 live missions, locked (lane `15-tier5`)

**Lane:** ID `15-tier5`. You own the six files in `qa/missions/tier5-live/`. Do not touch any
other file.

**Interfaces:**
- Consumes: `../../handbook/live-testing-safety.md` (all anchors),
  `../../../docs/operator/personal-serving.md#what-pitwall-setup-changes-on-your-machine`,
  `../../../docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner`, and the
  operator docs named below.
- Produces: the locked tier. None of these commands run while building this packet (spec D12).
  Their syntax is checked against `--help` in Step 2.

- [ ] **Step 1: Write the six missions from these contracts**

Common: Mode `training, always`. Each mission's Safety starts with the GC-11 line and adds: "This
is a live mission. Follow [before every live step]{../../handbook/live-testing-safety.md#before-every-live-step}
first and the [cleanup check]{../../handbook/live-testing-safety.md#cleanup-check} last. Stop at the
first surprise charge." "What counts as a finding": any difference from the linked operator doc, a
charge higher than the agreed ceiling (severity 1), or anything left running after cleanup
(severity 1).

- **`T5-00-live-safety-briefing.md`** (not repeatable; output a tier check)
  - Why: nothing live happens until the tester can explain every safety layer.
  - Steps:
    1. Read `live-testing-safety.md` together, one section per message.
    2. Store the key as described in [key handling]{../../handbook/live-testing-safety.md#key-handling}.
       Then `ls -l ~/.config/pitwall-qa/runpod.env`. Expected: `-rw-------`. The file content is
       never shown.
    3. Agree `PITWALL_MONTHLY_BUDGET_USD`, the TTL, and the maximum price with the maintainer.
       Record them in the progress file.
    4. The Tier 5 questions from the tier check. Expected: all correct.
- **`T5-01-read-only-live-checks.md`** (repeatable)
  - Why: read-only calls prove the key works without spending anything.
  - Sources: `../../../docs/operator/runpod-market.md`.
  - Steps: load the key into this terminal only; `uv run pitwall runpod catalogue --json | uv run python -m json.tool | head -40`.
    Expected: catalogue, availability, prices, and balance, as `runpod-market.md` describes.
    Compare the fields. `--refresh` forces one provider refresh; use it once.
- **`T5-02-personal-serving-smoke.md`** (repeatable)
  - Why: personal serving is the fastest path to real spend. Smoke-test it with the tightest
    limits.
  - Sources: `../../../docs/operator/personal-serving.md`,
    `../../../docs/operator/serve-quickstart.md`.
  - Steps: read "What `pitwall setup` changes on your machine", then follow `serve-quickstart.md`
    with:
    - the smallest catalogue model (`uv run pitwall models list`)
    - the cheapest suitable GPU
    - `--ttl-minutes` and `--max-usd-per-hour` set to the agreed values
    - `--route qa-smoke`

    Check with `uv run pitwall status`, send one request as the doc shows, then
    `uv run pitwall stop qa-smoke` and the cleanup check.
- **`T5-03-pod-lease-lifecycle.md`** (repeatable)
  - Why: journey L3, launch, renew, and stop on real capacity.
  - Sources: `../../../docs/operator/release-testing-checklist.md`,
    `../../../docs/sdlc/06-leases.md`.
  - Steps: follow `06-leases.md` for launch, renew, and stop through the documented surface, with
    the agreed limits, then the cleanup check.
- **`T5-04-live-audit.md`** (repeatable; output an audit report)
  - Why: journey L4, the audit against a real account.
  - Sources: `../../../docs/operator/16-check-audit-procedure.md`.
  - Steps: follow the procedure exactly and post the report where the maintainer asks.
- **`T5-05-serverless-endpoints.md`** (repeatable)
  - Why: journeys L1 and L2, real serverless endpoints and a real inference round trip.
  - Sources: `../../../docs/operator/create-lb-endpoint.md`,
    `../../../docs/operator/create-vllm-endpoint.md`.
  - Steps: follow each doc with the agreed limits. Remove each endpoint at the end, then the
    cleanup check.

- [ ] **Step 2: Check tier 5 commands against help, without running them**

Run each and confirm that every flag the missions name appears. The personal verbs show their
personal flags only when `DATABASE_URL` is unset. Help is parsed before any credential check (verified on
`71885b9`: exit 0, nothing written to an empty home):

```bash
H="$(mktemp -d)"
for c in "runpod catalogue" serve stop status models; do
  env -u DATABASE_URL HOME="$H" .venv/bin/pitwall $c --help < /dev/null | head -12
done
rm -rf "$H"
```
Expected: `--refresh` and `--json` for catalogue; `--model`, `--gpu-class`, `--ttl-minutes`,
`--max-usd-per-hour`, `--route` for serve. A missing flag means you correct the mission text to
match the help.

- [ ] **Step 3: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/missions/tier5-live/*.md`
Expected: `shape check: 6 ok, 0 with problems`.
Run VC-links, VC-policy (path `qa/missions/tier5-live`), and VC-secrets. Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add qa/missions/tier5-live
git commit -s -m "docs(qa): locked tier 5 live-testing missions"
```

---

### Task 16: Human entry and maintainer setup (lane `16-front`)

**Lane:** ID `16-front`. You own `qa/README.md` and `qa/maintainer-setup.md`. Do not touch any
other file.

**Interfaces:**
- Consumes: skeleton paths; `../CONTRIBUTING.md#definition-of-done` (exists; Task 17 adds standard
  3 under it).
- Produces: the tester's and maintainer's human entry points, and the kickoff message.

- [ ] **Step 1: Write `qa/README.md`**

````markdown
# QA program

This folder turns a new tester into Pitwall's QA function, with an AI coding agent as coach. The
agent reads these files and guides the tester through a short bootcamp, then real QA missions:
testing the docs, testing the product by hand, checking pull requests before they merge, writing
automated tests, and, once the maintainer allows it, testing against real cloud providers.

## What this is

- A **coach** for the agent: [START-HERE.md]{START-HERE.md} and the [coach rules]{coach/rules.md}.
- A **bootcamp** of seven lessons, starting at [B0 — Welcome]{bootcamp/B0-welcome.md}.
- **Missions** in five tiers: [the mission ladder]{missions/README.md}.
- **Concept cards** that each explain one idea: [concepts]{concepts/README.md}.
- A **handbook** for how QA works here: [handbook]{handbook/README.md}.
- **Templates** for progress, bug reports, QA reports, and notes, in `templates/`.

## For the tester: how to start

1. Get set up with the maintainer ([maintainer setup]{maintainer-setup.md}).
2. Open a terminal in your Pitwall clone, the folder that contains `qa/`.
3. Start your AI agent (Claude Code, Codex, or opencode).
4. Type: `Read qa/START-HERE.md and follow it.`

Do this at the start of every session. Your progress is saved in `qa/.work/`, which git ignores.

## For the maintainer

- Onboarding a tester: [maintainer setup]{maintainer-setup.md}.
- Asking for QA on a pull request: fill in its `## QA notes` section and add the `needs-qa` label
  ([details]{handbook/acceptance-testing.md#maintainer-side-requesting-qa}).
- Triage and labels: [triage and labels]{handbook/triage-and-labels.md}.

## How it is organized

```text
qa/
  START-HERE.md        the agent starts here
  README.md            this file
  maintainer-setup.md  onboarding a tester
  coach/               rules and teaching instructions for the agent
  bootcamp/            B0 to B6
  missions/            tiers 1 to 5 (tier 5 is locked)
  concepts/            one idea per card
  handbook/            how QA works on this project
  templates/           fill-in forms
  .work/               the tester's private progress (ignored by git)
```

## Keeping it current

A pull request that changes a command, route, screen, or heading that a lesson uses updates that
lesson in the same pull request. See [maintaining the packet]{handbook/maintaining-the-packet.md}
and the [Definition of Done]{../CONTRIBUTING.md#definition-of-done}.
````

- [ ] **Step 2: Write `qa/maintainer-setup.md`**

````markdown
# Maintainer setup

What the maintainer does once to onboard a tester, and the parts that repeat.

## 1. GitHub access

1. Ask the tester for their GitHub username.
2. Invite them with the Write role:
   `gh api -X PUT repos/Buckeyes22/pitwall/collaborators/<username> -f permission=push`
3. The tester accepts the invitation from their email or GitHub notifications.
4. Confirm: `gh api repos/Buckeyes22/pitwall/collaborators --jq '.[].login'` lists them.

Write access lets the tester push branches, apply labels, and review pull requests. `main`
requires a pull request and the `CI` check, with zero required approvals. So the tester cannot
push to `main`, and their reviews inform you but never gate a merge.

## 2. Labels

The QA labels are created once per repository. Check them:

```bash
gh label list --search qa
gh label list --search severity
```

Expected: `found-by-qa`, `needs-qa`, `qa-passed`, `qa-failed`, `qa-verified`, `qa-packet`, and the
four `severity:` labels. Create any that are missing:

```bash
gh label create found-by-qa --color 5319e7 --description "Filed by the QA program" --force
gh label create severity:1-critical --color b60205 --description "Money, secrets, or safety" --force
gh label create severity:2-high --color d93f0b --description "Core journey broken, no workaround" --force
gh label create severity:3-medium --color fbca04 --description "Wrong behavior or docs, with a workaround" --force
gh label create severity:4-low --color c5def5 --description "Cosmetic" --force
gh label create needs-qa --color 1d76db --description "Ready for acceptance testing" --force
gh label create qa-passed --color 0e8a16 --description "Acceptance testing passed" --force
gh label create qa-failed --color e11d21 --description "Acceptance testing failed" --force
gh label create qa-verified --color 006b75 --description "Fix confirmed by QA on main" --force
gh label create qa-packet --color bfd4f2 --description "Defect in the qa/ training packet" --force
```

## 3. Agent access

- Claude Code and Codex: the tester needs their own sign-in for each. Arrange a subscription or
  seat with them.
- opencode with your self-hosted model: set up the tester's opencode with an OpenAI-compatible
  provider that points at your model server. Send the address and key privately. They belong only
  in the tester's local opencode configuration, never in this repository, an issue, or a pull
  request.
- Ask the tester to keep approval prompts on in every agent.

## 4. The kickoff message

Send this once sections 1–3 are done:

```text
Welcome aboard as Pitwall's tester! Here's how to start:

1. Accept the GitHub invitation for Buckeyes22/pitwall (check your email).
2. Install your AI agents: Claude Code, Codex, and opencode. We'll connect opencode to my model
   server together.
3. Install git and the GitHub CLI (gh) with your Linux package manager, then sign in:
     gh auth login
   Choose GitHub.com, HTTPS, and "Login with a web browser", and say yes to authenticating Git.
   If installing gh is confusing, start your agent in your home folder and ask it to help you
   install the GitHub CLI for your Linux distribution.
4. Get the code:
     gh repo clone Buckeyes22/pitwall ~/pitwall
     cd ~/pitwall
5. Start your agent in that folder and type exactly:
     Read qa/START-HERE.md and follow it.
6. Keep your agent's approval prompts on. When it asks to run something, read it first.

Do step 5 at the start of every session. Bring your questions to our weekly check-in.
```

## 5. Weekly check-in

- The tester brings their check-in notes (from `qa/templates/weekly-checkin.md`).
- Triage new `found-by-qa` issues: keep each one, or close it as `duplicate`, `invalid`, or
  `wontfix` with a reason ([issue lifecycle]{handbook/triage-and-labels.md#issue-lifecycle}).
- Answer the tester's "Questions for the maintainer".
- Decide tier unlocks ([unlocking a tier]{missions/README.md#unlocking-a-tier}), and tell the tester
  plainly. The coach records the unlock.

## 6. Requesting QA on a pull request

Fill in the pull request's `## QA notes` section, then add the label with
`gh pr edit <N> --add-label needs-qa`. Details are in
[the maintainer side of acceptance testing]{handbook/acceptance-testing.md#maintainer-side-requesting-qa}.

## 7. Unlocking tier 5

1. Create a dedicated provider account with a small prepaid balance. The balance is the hard
   spend ceiling.
2. Create an API key for that account only, and send it to the tester privately.
3. Agree on `PITWALL_MONTHLY_BUDGET_USD`, the TTL, and the maximum hourly price.
4. Confirm the tester passed
   [T5-00]{missions/tier5-live/T5-00-live-safety-briefing.md}.
5. Revoke the key whenever live testing pauses.
````

- [ ] **Step 3: Validate**

Run: `uv run --frozen python /tmp/qa_shape_check.py qa/README.md qa/maintainer-setup.md`
Expected: `shape check: 2 ok, 0 with problems`.
Run VC-links, VC-policy (paths `qa/README.md qa/maintainer-setup.md`), and VC-secrets. Expected:
pass.

- [ ] **Step 4: Commit**

```bash
git add qa/README.md qa/maintainer-setup.md
git commit -s -m "docs(qa): human entry point and maintainer setup with the kickoff message"
```

### Task 17: Fixes F1–F9 and pointers into `qa/` (lane `17-repo`)

**Lane:** ID `17-repo`. You own `README.md`, `CONTRIBUTING.md`,
`.github/pull_request_template.md`, `docs/operator/user-journey-catalog.md`,
`docs/operator/troubleshooting.md`, `docs/sdlc/18-cli.md`, `packages/agent-routing/README.md`, and
`packages/agent-routing/docs/doctor.md`. Do not touch any other file.

**Files and line numbers** are as of `71885b9`. Match on the quoted text, not the line number.
In this task, `[text]{target}` is written as a real Markdown link, and old strings that show this
notation are the real links in the file (GC-4).

**Interfaces:**
- Consumes: `qa/README.md` exists (skeleton).
- Produces: the F1–F9 fixes that T3-01 practices on, and the `## QA notes` section every future pull
  request carries.

- [ ] **Step 1: Confirm no test pins the old strings**

```bash
for s in "all six screens registered" "Overview, Providers, Leases, Models, Cost, Resources, Operations" \
  "pytest tests/ -v" "Settings load error / domain-config errors" "Argument parsing / unhandled app error" \
  "It ships read-only Overview" "The command is read-only except for guarded model launch" \
  "python tools/guards/repo_text_policy.py <files>" "Two non-negotiable standards" \
  "local, read-only, and performs no live model discovery"; do
  grep -rln -F -- "$s" tests packages/agent-routing/tests tools || true
done
```

Expected: no output (verified on `71885b9`).

- [ ] **Step 2: F1 — `README.md` line 60**

Old: `| Operator TUI | Textual console with guarded actions: Overview, Providers, Leases, Models, Cost, Resources, Operations |`
New: `| Operator TUI | Textual console with guarded actions: Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost, Resources, Operations |`

- [ ] **Step 3: F2 — `docs/operator/user-journey-catalog.md` J09 row (line 32)**

Old ending: `| Alive after boot window, no traceback, all six screens registered |`
New ending: `| Alive after boot window, no traceback |`

- [ ] **Step 4: F3 — `docs/sdlc/18-cli.md` line 108 and lines 645–649**

First, confirm which screens push confirmation modals:

```bash
grep -n -E 'class Confirm[A-Za-z]*Modal|push_screen\(Confirm' src/pitwall/tui/*.py
grep -n -E 'serve-confirm-text|pods-confirm-text' src/pitwall/tui/personal.py
```

Expected: `ConfirmOnboardingModal` (`onboarding.py`), `ConfirmVolumeFileModal`
(`volume_files.py`), `ConfirmRoutingJobModal` (`routing_jobs.py`), the Resources and serve
confirmations, and the two personal inputs. If a modal is pushed from a screen other than the one
named below, name the screen the code shows.

In line 108, replace the sentence `It ships read-only Overview, Providers, Pods / Leases, and Models views, with one guarded mutation: launching a catalogue selection after a mandatory dry-run and exact type-to-confirm step.`
with:

```text
It binds ten views in `src/pitwall/tui/app.py`: Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost, Resources, and Operations. Overview, Leases, Routes, and Cost are read-only. Every mutation sits behind a preview and an exact typed confirmation: the Models catalogue launch (mandatory dry-run and type-to-confirm, described below), the Serve launch and Pods stop ([Personal Serving Views]{#addendum-personal-serving-views-serve-pods-routes}), RunPod onboarding apply from Providers ([RunPod onboarding CLI and TUI]{#addendum-runpod-onboarding-cli-and-tui}), Resources mutations ([TUI Resources View]{#addendum-tui-resources-view}), and routing-job and volume-file actions from Operations ([TUI Operations View]{#addendum-tui-operations-view}).
```

Replace lines 645–649 (from `The command is read-only except for guarded model launch` through `and exact resource/name confirmation before apply.`) with:

```text
The command is read-only except for the guarded mutations listed in the TUI responsibility
paragraph. Overview, Leases, Routes, and Cost are read-only views; Models retains authored dossier
Markdown and routes a selected hardware row through mandatory dry-run preview and `serve.launch`
type-to-confirm before calling serve-model. Serve launches a personal route only after the
operator types the route name, and Pods stops a pod only after a typed confirmation
(`src/pitwall/tui/personal.py:249`, `src/pitwall/tui/personal.py:350`). Resources similarly
requires its shared-service preview and exact resource/name confirmation before apply.
```

- [ ] **Step 5: F6 and F8 — `docs/sdlc/18-cli.md` exit-code table and `cmd_dashboard` bullets**

Line 708, old: `| \`pitwall config check [service]\` | Config report (exit 0) | Settings load error / domain-config errors → \`EX_CONFIG\` | — | — |`
New: `| \`pitwall config check [service]\` | Config report | — | — | — |`

Line 716, old: `| \`pitwall dashboard\` | Textual app launched and exited | Argument parsing / unhandled app error | — | — |`
New: `| \`pitwall dashboard\` | Textual app launched and exited | Unhandled app error | Argument parsing (argparse); personal backend with no credential in a non-interactive shell | — |`

After the paragraph that ends `` `served_model_mismatch`, and unexpected failures. ``, add a new
paragraph:

```text
`pitwall config check` exits `78` (`os.EX_CONFIG`) on a settings load error or domain-config
errors (`src/pitwall/cli.py:1294`, `src/pitwall/cli.py:1301`); the table's columns stop at `3`.
```

Between the bullets `- Parses \`pitwall dashboard\` arguments.` (line 641) and
`- Lazily imports \`PitwallApp\` from \`pitwall.tui\`.`, add:

```text
- When `DATABASE_URL` is unset (personal backend) and no RunPod credential resolves, it runs
  `pitwall setup` in an interactive terminal; otherwise it prints
  ``no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY`` and returns `2`
  (`src/pitwall/cli.py:850`–`862`).
```

Verify both facts before committing:

```bash
env -u DATABASE_URL -u REDIS_URL uv run pitwall config check > /dev/null 2>&1; echo "exit=$?"
uv run pitwall dashboard --bogus > /dev/null 2>&1; echo "exit=$?"
```

Expected: `exit=78`, then `exit=2`.

- [ ] **Step 6: F4 and QA notes — replace `.github/pull_request_template.md` entirely**

````markdown
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
````

- [ ] **Step 7: F5 — `docs/operator/troubleshooting.md` lines 172–174**

Old:

```text
**Symptom:** `pitwall warm-volume ...` exits 2 with
`{"error": "missing_database_url", "detail": "warm-volume needs DATABASE_URL
(registry-backed launch planning)"}`; or a `pitwall serve` invocation using
```

New:

```text
**Symptom:** `pitwall warm-volume ...` exits 2 with the message
`warm-volume needs DATABASE_URL (registry-backed launch planning)` (with `--json`:
`{"error": "missing_database_url", "detail": "warm-volume needs DATABASE_URL
(registry-backed launch planning)"}`); or a `pitwall serve` invocation using
```

Verify: `env -u DATABASE_URL uv run pitwall warm-volume --model x --volume-id y --dry-run 2>&1 | grep -c 'warm-volume needs DATABASE_URL'`.
Expected: `1`.

- [ ] **Step 8: F7, standard 3, and Testing help — `CONTRIBUTING.md`**

Line 78, old: `| Pattern scrub | \`python tools/guards/repo_text_policy.py <files>\` | banned literals, real RunPod IDs/hosts |`
New: `| Pattern scrub | \`uv run python tools/guards/repo_text_policy.py <files>\` | banned literals, real RunPod IDs/hosts |`

Line 89: change `Two non-negotiable standards apply` to `Three non-negotiable standards apply`.

Insert immediately before the line `## PR Process`:

```text
### 3. QA packet parity (`qa/`)

[`qa/`]{qa/README.md} is the agent-guided tester program. Its lessons link to the commands,
routes, screens, and doc headings they exercise. A change that renames, removes, or changes the
behavior of anything a lesson uses updates that lesson in the same PR:

- `make docs-check` fails when a heading a lesson links to is renamed or removed.
- Review catches changed commands, flags, output, and screens.
- A new user-facing surface adds its standing mission or concept card in the PR that adds it, and
  the PR's `## QA notes` drive the tester's acceptance run.

```

Insert immediately before the line `## Code of Conduct`:

```text
## Testing help

Pitwall has an agent-guided QA program in [`qa/`]{qa/README.md}. A tester opens an AI coding
agent at the repository root and types `Read qa/START-HERE.md and follow it.` To request acceptance
testing on a pull request, fill in its `## QA notes` section and add the `needs-qa` label.

```

- [ ] **Step 9: Pointer — `README.md` Contributing section (line 307)**

After the line `Contributions are welcome under the guidelines in [CONTRIBUTING.md]{CONTRIBUTING.md}.`
add a blank line and:

```text
Testers start with the agent-guided QA program in [qa/README.md]{qa/README.md}.
```

- [ ] **Step 10: F9 — Agent Routing doctor docs**

`packages/agent-routing/README.md` line 397, old:
`The default doctor is local, read-only, and performs no live model discovery:`
New:
`The default doctor is local, changes nothing itself, and performs no live model discovery. It does run each installed harness's local help and version commands, and some harness CLIs create their own first-run, state, or log files when invoked, which is most visible on a fresh home directory:`

`packages/agent-routing/docs/doctor.md` line 20: after the sentence
`A provider filter omits the summary because \`provider.summary\` is not tied to one provider.`
append:
` Doctor itself writes nothing, but harness CLIs may create their own first-run, state, or log files when these help and version commands run.`

If `feat/orchestrator-channel-b-d` has merged to `main` first and moved these lines, apply the same
two sentences to the new text after rebasing.

Verify the behavior the new sentence describes:

```bash
cd packages/agent-routing
pitwall_uv() { uvx --isolated --from 'uv==0.11.19' uv "$@"; }
pitwall_uv sync --frozen --group dev --python 3.14.7 > /dev/null 2>&1
H="$(mktemp -d)"; HOME="$H" .venv/bin/python scripts/pitwall-agent-routing doctor > /dev/null 2>&1; echo "exit=$?"
find "$H" -type f | wc -l; rm -rf "$H"; cd ../..
```

Expected: `exit=0` and a count above `0` on a machine with harness CLIs installed (8 on the
maintainer's machine at `71885b9`).

- [ ] **Step 11: Validate**

Run VC-links. Expected: pass (the new `#addendum-...` anchors and `qa/README.md` resolve).
Run VC-policy with paths
`README.md CONTRIBUTING.md .github/pull_request_template.md docs/operator docs/sdlc/18-cli.md packages/agent-routing/README.md packages/agent-routing/docs/doctor.md`.
Expected: pass.
Run VC-secrets. Expected: pass.
Run: `cd packages/agent-routing && .venv/bin/python tools/check_markdown_links.py; cd ../..`.
Expected: the component link check passes.

- [ ] **Step 12: Commit**

```bash
git add README.md CONTRIBUTING.md .github/pull_request_template.md docs/operator/user-journey-catalog.md \
  docs/operator/troubleshooting.md docs/sdlc/18-cli.md packages/agent-routing/README.md packages/agent-routing/docs/doctor.md
git commit -s -m "fix(docs): TUI views, J09 outcome, exit codes, PR template gates, doctor side effects; point testers at qa/"
```

---

### Task 18: Integrate the lanes

**Files:** none new. Brings commits from `qa-lane/02-coach` … `qa-lane/17-repo` onto
`docs/qa-onboarding`.

**Interfaces:**
- Consumes: 16 lane branches, each with one signed commit and a lane report ending
  `STATUS: DONE`.
- Produces: one linear branch with every lane's commit.

- [ ] **Step 1: Check every lane report**

Run: `grep -L 'STATUS: DONE' ~/git/pitwall-qa-lanes/*/report.md`
Expected: no output. For any lane listed, read its report, fix the unmet criterion in that lane's
worktree, re-run its validation, and update its report before continuing.

- [ ] **Step 2: Verify each lane touched only its own files**

```bash
cd ~/git/pitwall-qa-onboarding
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/qa-lane/); do
  echo "== $b"; git diff --name-only docs/qa-onboarding..."$b"
done
```

Expected: each lane lists only the files in its Lane block. Any other file means the lane broke
ownership. Drop that file from the lane's commit and rerun the lane's validation.

- [ ] **Step 3: Rebase and fast-forward each lane in task order**

```bash
for id in 02-coach 03-templates 04-handbook-a 05-handbook-b 06-cards-a 07-cards-b 08-cards-c \
          09-bootcamp 10-tier1 11-tier2a 12-tier2b 13-tier3 14-tier4 15-tier5 16-front 17-repo; do
  git -C "~/git/pitwall-qa-lane-$id" rebase docs/qa-onboarding || break
  git merge --ff-only "qa-lane/$id" || break
done
git log --oneline docs/qa-onboarding | head -20
```

Expected: 16 new commits above the Task 1 skeleton commit, with no conflicts (the lanes are
file-disjoint). Each rebase keeps its `Signed-off-by:` trailer.

- [ ] **Step 4: Remove lane worktrees and branches**

```bash
for id in 02-coach 03-templates 04-handbook-a 05-handbook-b 06-cards-a 07-cards-b 08-cards-c \
          09-bootcamp 10-tier1 11-tier2a 12-tier2b 13-tier3 14-tier4 15-tier5 16-front 17-repo; do
  git worktree remove "~/git/pitwall-qa-lane-$id" && git branch -d "qa-lane/$id"
done
git worktree list | grep -c pitwall-qa-lane- ; git branch --list 'qa-lane/*' | wc -l
```

Expected: `0` and `0`. The lane reports stay in `~/git/pitwall-qa-lanes/` for review.

### Task 19: Create the GitHub labels

**Files:** none. GitHub repository labels on `Buckeyes22/pitwall` (spec §11.3, §13.3).

- [ ] **Step 1: Create or update the ten labels**

```bash
R=Buckeyes22/pitwall
gh label create found-by-qa --repo $R --color 5319e7 --description "Filed by the QA program" --force
gh label create severity:1-critical --repo $R --color b60205 --description "Money, secrets, or safety" --force
gh label create severity:2-high --repo $R --color d93f0b --description "Core journey broken, no workaround" --force
gh label create severity:3-medium --repo $R --color fbca04 --description "Wrong behavior or docs, with a workaround" --force
gh label create severity:4-low --repo $R --color c5def5 --description "Cosmetic" --force
gh label create needs-qa --repo $R --color 1d76db --description "Ready for acceptance testing" --force
gh label create qa-passed --repo $R --color 0e8a16 --description "Acceptance testing passed" --force
gh label create qa-failed --repo $R --color e11d21 --description "Acceptance testing failed" --force
gh label create qa-verified --repo $R --color 006b75 --description "Fix confirmed by QA on main" --force
gh label create qa-packet --repo $R --color bfd4f2 --description "Defect in the qa/ training packet" --force
```

- [ ] **Step 2: Verify (V7)**

Run: `gh label list --repo Buckeyes22/pitwall --limit 100 --json name --jq '.[].name' | grep -E '^(found-by-qa|severity:|needs-qa|qa-)' | sort`
Expected, exactly:

```text
found-by-qa
needs-qa
qa-failed
qa-packet
qa-passed
qa-verified
severity:1-critical
severity:2-high
severity:3-medium
severity:4-low
```

### Task 20: Gates on the integrated branch (V1–V3 plus shape and inventory)

**Files:** none changed unless a gate fails. A failure is fixed in place, in the file that owns
it, with a signed `fix(qa):` commit.

- [ ] **Step 1: Inventory**

Run VC-count. Expected: `99`.
Run: `git ls-files qa | grep -c '^qa/.work/'`. Expected: `0`.

- [ ] **Step 2: Shape check over every packet file**

Run: `uv run --frozen python /tmp/qa_shape_check.py $(git ls-files 'qa/*.md')`
Expected: `shape check: 99 ok, 0 with problems`.

- [ ] **Step 3: No empty skeleton leftovers and no plan notation**

```bash
grep -rn 'Template content arrives in Task 3' qa || echo "no skeleton markers"
grep -rn -F ']{' qa || echo "no plan notation"
```

Expected: `no skeleton markers`, then `no plan notation`.

- [ ] **Step 4: V1 links, V2 text policy, V3 secrets**

Run VC-links, then the CI text-policy command
`git ls-files -z | xargs -0 -r -n 200 uv run --frozen python tools/guards/repo_text_policy.py`,
then VC-secrets.
Expected: `markdown links passed: <N> files (internal)`; no output; `secret scan passed: 304 reviewed findings`.

- [ ] **Step 5: External link check (CI runs it too)**

Run: `uv run --frozen python tools/ci/check_markdown_links.py --external 2>&1 | tail -3`
Expected: it passes. `qa/` has no external Markdown links (GC-5), so any failure comes from
elsewhere. Record it as the gate's result either way.

- [ ] **Step 6: The fast suite and the release policy tests still pass**

Run: `make test 2>&1 | tail -5` and
`uv run --frozen pytest -q tests/test_markdown_links.py tests/test_repo_text_policy_guard.py 2>&1 | tail -3`.
Expected: no failures in either.

- [ ] **Step 7: DCO on every commit of the branch**

Run: `PITWALL_DCO_BASE_SHA=$(git merge-base origin/main HEAD) PITWALL_DCO_HEAD_SHA=$(git rev-parse HEAD) uv run --frozen python tools/ci/check_dco.py`
Expected: `DCO sign-off passed for <n> non-merge commit(s)`.

---

### Task 21: V4 — run every lesson command in a clean room

**Files:**
- Create: `docs/evidence/<run date>-qa-packet-verification.md` (date format `YYYY-MM-DD`).
- Modify: any `qa/` file whose command or `Expected:` line proves wrong. Fix it in place (spec D9).
  Any product doc the command contradicts is fixed too, as a documentation defect, with a signed
  `fix(docs):` commit.

**Interfaces:**
- Consumes: the integrated branch; labels from Task 19; the local Docker daemon.
- Produces: success criterion S2 and the evidence file.

- [ ] **Step 1: Preconditions**

```bash
docker ps --format '{{.Names}}  {{.Ports}}' | grep -E '5444|6380' || echo "test ports free"
ss -tln | grep -E ':(8080|1808[0-9]|18090) ' || echo "service ports free"
```

Expected: `test ports free`, then `service ports free`. If another session's stack holds the
ports, stop and ask the maintainer. Another session may be using that database.

- [ ] **Step 2: Build the clean room**

```bash
export QA_ROOM="$(mktemp -d /tmp/qa-room.XXXXXX)"
mkdir -p "$QA_ROOM/home"
git clone --branch docs/qa-onboarding ~/git/pitwall "$QA_ROOM/home/pitwall"
env -i HOME="$QA_ROOM/home" USER="$USER" TERM="$TERM" LANG="${LANG:-C.UTF-8}" \
  PATH="$(dirname "$(command -v uv)"):$(dirname "$(command -v claude)"):/usr/local/bin:/usr/bin:/bin" \
  UV_CACHE_DIR="$HOME/.cache/uv" UV_PYTHON_INSTALL_DIR="$HOME/.local/share/uv/python" \
  GH_CONFIG_DIR="$HOME/.config/gh" bash --noprofile --norc
```

Inside the clean shell:

```bash
env | grep -c -E '^(RUNPOD|PITWALL)_'
cd ~/pitwall && uv sync --frozen --extra dev 2>&1 | tail -2
```

Expected: `0`, then a completed sync. The clean room has an empty home, no provider variables, a
fresh clone, and only `gh` read access, borrowed through `GH_CONFIG_DIR`. The `uv` caches are
shared to avoid re-downloading, which does not affect correctness.

- [ ] **Step 3: List the commands to verify**

Write `/tmp/qa_commands.py`:

```python
"""List fenced bash blocks in qa/ lesson, mission, card, and handbook files, in reading order."""

from pathlib import Path

ORDER = [
    "qa/bootcamp",
    "qa/missions/tier1-docs",
    "qa/missions/tier2-manual",
    "qa/missions/tier3-acceptance",
    "qa/missions/tier4-automation",
    "qa/concepts",
    "qa/handbook",
]
for folder in ORDER:
    for path in sorted(Path(folder).glob("*.md")):
        block, inside, lines = 0, False, []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip().startswith("```bash"):
                inside, block, lines = True, block + 1, []
                start = number
            elif inside and line.strip() == "```":
                inside = False
                print(f"{path}#{block} (line {start})")
                for item in lines:
                    print(f"    {item}")
            elif inside:
                lines.append(line)
```

Run: `uv run python /tmp/qa_commands.py > /tmp/qa_commands.txt && grep -c '^qa/' /tmp/qa_commands.txt`
Expected: a count of blocks. This is the checklist for Step 4. Inline commands in step text
(single backticks) are verified from the same files as you read them.

- [ ] **Step 4: Run the commands in order, recording each row**

Run each lesson's and mission's steps in file order, inside the clean room, using that lesson's
setup. Record every command in the evidence table (Step 6). Use one of these result categories:

| Category | Applies to | How it is verified |
| --- | --- | --- |
| `run` | Every command that is safe and hermetic | Executed. The exit code and a stable excerpt are compared with `Expected:` |
| `syntax` | Commands that publish to GitHub: `gh issue create`, `gh issue comment`, `gh issue edit`, `gh issue reopen`, `gh pr review`, `gh pr edit`, `gh pr create`, `git push` | `<command> --help` shows every flag used; label names exist (Task 19); never executed |
| `install` | B2 steps 3–5 | Tools already exist on this host. `apt-cache policy curl make iproute2 util-linux` shows each package, and `curl -fsSL https://get.docker.com -o /dev/null -w '%{http_code}\n'` and `curl -LsSf https://astral.sh/uv/install.sh -o /dev/null -w '%{http_code}\n'` print `200`; scripts are never executed |
| `browser` | Swagger steps in B5 and T2-01 | `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/docs` gives `200` with the API started via `env -u PITWALL_API_TOKEN`, and `401` with the token on; each J07 route is fetched with `curl` |
| `tui` | B5 step 7, T2-07, the TUI card, smoke item 5 | Driven with tmux, as below |
| `agent` | T2-08 steps 3–6 | Run as below with `claude -p --strict-mcp-config --mcp-config`, so no configuration is written |
| `live` | Tier 5 | Help output only (Task 15 Step 2); never executed |

TUI procedure (the README `export` lines must be set in the tmux session):

```bash
tmux new-session -d -s qa -x 160 -y 50 "cd ~/pitwall && bash --norc"
tmux send-keys -t qa '<the five README export lines, one send-keys each>' Enter
tmux send-keys -t qa 'uv run pitwall dashboard' Enter; sleep 6
for k in o p l m s d t c e a; do tmux send-keys -t qa "$k"; sleep 2; tmux capture-pane -p -t qa | head -3; done
tmux send-keys -t qa '?'; sleep 2; tmux capture-pane -p -t qa | grep -c -E 'Overview|Providers'
tmux send-keys -t qa Escape q; sleep 2; tmux kill-session -t qa
```

Expected: every capture shows the view's title and no `Traceback`, and the help count is `1` or
more. Repeat with `-x 90 -y 30` for the narrow-window step.

Agent procedure for T2-08 (uses the maintainer's own Claude Code login; nothing is written to its
configuration):

```bash
claude mcp add --help | head -3
printf '{"mcpServers":{"pitwall-local":{"command":"%s"}}}\n' "$PWD/qa/.work/pitwall-mcp.sh" > /tmp/qa-mcp.json
HOME=/home/<maintainer> claude -p --strict-mcp-config --mcp-config /tmp/qa-mcp.json \
  "Using the pitwall-local MCP server, list the capabilities. Reply with the capability names only."
```

Expected: the usage line for `claude mcp add`, then a reply that includes `embedding.demo`. Build
`qa/.work/pitwall-mcp.sh` exactly as T2-08 step 1 describes. Repeat the call with T2-08 step 5's
request. Expected: a dry-run result.

- [ ] **Step 5: Fix what fails, in place**

For every row whose actual result differs from `Expected:`:

1. Decide which is wrong: the lesson, or the product doc it follows.
2. Fix the lesson (the command or the `Expected:` line), or fix the product doc if the code is
   right and the doc is wrong, or record a product bug if the code is wrong. A product bug gets a
   GitHub issue labeled `bug`, and the lesson's expected line states the documented behavior.
3. Re-run the row and record the new result.
4. Commit the fixes with `git commit -s`.

- [ ] **Step 6: Write the evidence file**

````markdown
# QA packet command verification — <run date>

- **Branch:** `docs/qa-onboarding` at `<short sha>`
- **Clean room:** empty home, no `RUNPOD_*` or `PITWALL_*` variables, fresh clone, shared `uv`
  caches, and read-only `gh` through `GH_CONFIG_DIR`
- **Host:** `<uname -sr>`, Docker `<version>`, uv `<version>`, Python 3.14.7

## Commands

| File | Step | Command | Category | Exit | Result | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `qa/bootcamp/B1-terminal-basics.md` | 1 | `pwd` | run | 0 | pass | |

## Fixes made during verification

| File | What was wrong | Fix | Commit |
| --- | --- | --- | --- |

## Weak-model comprehension

Filled in by Task 22.
````

The evidence file contains no credential values and no URLs with a password (GC-6). Commands that
used the README `export` lines say "README Quick Start environment" in Notes.

- [ ] **Step 7: Validate and commit**

Run VC-links, VC-policy (paths `docs/evidence qa`), VC-secrets, and VC-shape on every changed
`qa/` file. Expected: pass.

```bash
git add docs/evidence qa
git commit -s -m "docs(evidence): clean-room verification of every QA packet command"
```

Then leave the clean room: `exit`, `make down` inside the clone if the stack is up, and
`rm -rf "$QA_ROOM"`.

### Task 22: V5 — weak-model comprehension pass

**Files:**
- Modify: `docs/evidence/<run date>-qa-packet-verification.md`, section "Weak-model
  comprehension".
- Modify: any `qa/` file whose wording the model misreads.

**Interfaces:**
- Consumes: route profile `q38-pi` (model `qwen3.8-27b-huihui-int8-mtp`, Pi harness; `q38-qwen` reported `needs-key` on 2026-09-11)
  through `~/.claude/scripts/route-shim.sh`; a fresh clone of the branch.
- Produces: success criterion S3.

- [ ] **Step 1: Probe the route**

Run: `printf 'Reply with the single word READY.\n' > /tmp/qa-v5-probe.md && ~/.claude/scripts/route-shim.sh q38-pi /tmp/qa-v5-probe.md | tail -3`
Expected: `READY` and a `SHIM-DONE exit=0` line. If the endpoint is down, report that in one
sentence and stop. Do not substitute another model.

- [ ] **Step 2: Write the questionnaire prompts**

Create `/tmp/qa-v5-core.md`:

```text
You are an AI coding agent started at the root of a Pitwall clone. The tester typed:
"Read qa/START-HERE.md and follow it."
Do not run any commands. Only read files. Read qa/START-HERE.md and every file it tells you to read
first. Assume qa/.work/progress.md does not exist. Answer these questions, numbered, one or two
sentences each:
1. What is your role?
2. What exactly do you do first in this session, given that the progress file does not exist?
3. In training mode, who types commands that change something?
4. List five commands you must never run outside tier 5.
5. What must happen before anything is posted to GitHub?
6. What do you do if you are not sure whether something is a bug?
7. What do you do if a command's output does not match the lesson's Expected line?
8. A terminal asks whether to run pitwall setup. What do you answer?
```

Answer key for `/tmp/qa-v5-core.md`:

1. QA coach who trains the tester and helps with real QA work, without doing the work for them.
2. Create `qa/.work/`, `evidence/`, and `notes/`; copy `templates/progress.md` to
   `qa/.work/progress.md`; start B0.
3. The tester.
4. Any five from R2.
5. The tester approves a draft written in `qa/.work/notes/`, then it is posted with
   `--body-file` (R9).
6. Add it to "Questions for the maintainer", or file a `question` issue (R7).
7. Treat it as a finding or a mistake: record it, and do not quietly work around it (R11).
8. No.

For each bootcamp lesson, create `/tmp/qa-v5-B<n>.md`:

```text
You are an AI coding agent at the root of a Pitwall clone, coaching a beginner tester. Do not run
any commands. Read qa/START-HERE.md, qa/coach/rules.md, and qa/bootcamp/<lesson file>. Answer,
numbered, one or two sentences each:
1. What is this lesson's goal?
2. What is the first step, and who does what in it?
3. When is the lesson done?
4. Name one command in this lesson and its expected result.
5. Is there anything in this lesson you are not allowed to do, and if so what?
```

The answer key for each lesson comes from its `## Goal`, first step, `## Done when`, any command
block with its `Expected:` line, and its safety notes (B2: only the tester types `sudo`; B3: do
not explain Pitwall; B5: start the API without the token for the docs page, and never start the
TUI without the `export` lines).

- [ ] **Step 3: Run each prompt from a fresh clone**

```bash
V5="$(mktemp -d)"; git clone --branch docs/qa-onboarding ~/git/pitwall "$V5/pitwall"
cd "$V5/pitwall"
for p in core B0 B1 B2 B3 B4 B5 B6; do
  ~/.claude/scripts/route-shim.sh q38-pi "/tmp/qa-v5-$p.md" > "/tmp/qa-v5-$p.out" 2>&1
  tail -1 "/tmp/qa-v5-$p.out"
done
```

Expected: eight `SHIM-DONE exit=0` lines. Grade each output against its answer key.

- [ ] **Step 4: Fix and re-run until every answer is correct**

For each wrong answer, find the sentence that misled the model and rewrite it: shorter, explicit
order, the rule quoted where it applies. Re-run that prompt from a new fresh clone of the updated
branch. Repeat until correct. Each fix is a signed `fix(qa):` commit, and each changed file passes
VC-shape.

- [ ] **Step 5: Record the results**

Fill in "Weak-model comprehension" in the evidence file: one row per prompt, with the round, the
questions correct out of the total, and the files changed. Commit:

```bash
git add docs/evidence qa
git commit -s -m "docs(evidence): Qwen 3.8 27B comprehension pass for the QA coach and bootcamp"
```

---

### Task 23: Review, push, and open the single pull request

**Files:** this plan (continuation tasks if review finds defects); no others unless review
findings require fixes.

**Interfaces:**
- Consumes: the verified branch.
- Produces: one pull request whose title contains "QA onboarding" (B4 and T3-01 search for it)
  and whose `## QA notes` are T3-01's practice material.

- [ ] **Step 1: Independent review against the spec**

One reviewer (Claude, when Codex implemented the lanes) checks the branch against the spec, not
the lane reports:

- every spec section maps to a file
- rules R1–R14 appear exactly
- every lesson and mission follows its format
- the nine fixes match spec §13.1
- the evidence file covers every command

Findings are fixed in place and appended to this plan as numbered continuation tasks (Task 24
onward) under a dated heading, following the one-plan-per-batch rule. Re-run Task 20 after any
fix.

- [ ] **Step 2: Rebase on the current `main`**

Run: `git fetch origin && git rebase origin/main`, then Task 20 Steps 1–7.
Expected: a clean rebase, and every gate passes as before.

- [ ] **Step 3: Push the branch**

Run: `git push -u origin docs/qa-onboarding`
Expected: the branch is created on `origin`.

- [ ] **Step 4: Write `/tmp/qa-pr-body.md`**

Fill the Testing section with the real Task 20 outputs and the V4 and V5 summary lines. End the
body with the attribution lines the executing session's harness requires.

````markdown
## Summary

Adds `qa/`, an agent-guided program that trains a beginner tester and runs Pitwall's QA function:
a coach that works in Claude Code, Codex, and opencode; a seven-lesson bootcamp; 37 missions in
five tiers (tier 5 stays locked until the maintainer issues a spend-capped key); 29 concept cards;
a QA handbook; and templates. It also fixes nine documentation defects found while designing it,
adds a `## QA notes` section to the pull-request template, and adds QA packet parity to the
Definition of Done.

- Design: `docs/superpowers/specs/2026-09-11-qa-onboarding-design.md`
- Plan: `docs/superpowers/plans/2026-09-11-qa-onboarding.md`
- Evidence: `docs/evidence/<run date>-qa-packet-verification.md`

## Linked Issue

None.

## What Changed

- `qa/`: 99 Markdown files and `qa/.gitignore`
- Documentation fixes F1–F9 (see QA notes)
- `.github/pull_request_template.md`: correct test commands, a `## QA notes` section, and a QA
  checklist line
- `CONTRIBUTING.md`: Definition of Done standard 3 (QA packet parity) and a Testing help section
- GitHub labels: `found-by-qa`, four `severity:` labels, `needs-qa`, `qa-passed`, `qa-failed`,
  `qa-verified`, `qa-packet`

## Testing

<Task 20 gate outputs; the V4 row count and fix count; the V5 rounds and scores>

## QA notes

- **What changed for users:** testers get `qa/`; operators get corrected docs.
- **Acceptance criteria:**
  1. F1: the README "Operator TUI" row lists the same ten views the TUI footer shows
     (`uv run pitwall dashboard` with the README Quick Start environment).
  2. F2: the J09 row in `docs/operator/user-journey-catalog.md` claims only what `j09()` in
     `scripts/release/run-user-journeys.sh` asserts.
  3. F3: `docs/sdlc/18-cli.md` names all ten views in the TUI paragraph and the `cmd_dashboard`
     paragraph, and every guarded mutation it names shows a typed confirmation in the TUI (open it
     and cancel).
  4. F4: the template's test commands match `CONTRIBUTING.md`, and `make test` passes as written.
  5. F5: `env -u DATABASE_URL uv run pitwall warm-volume --model x --volume-id y --dry-run` prints
     the message the troubleshooting guide quotes and exits 2; with `--json` it prints the quoted
     JSON.
  6. F6: `env -u DATABASE_URL -u REDIS_URL uv run pitwall config check; echo "exit=$?"` prints
     `exit=78`, as the CLI reference now states.
  7. F7: CONTRIBUTING's Pattern scrub row uses `uv run python`.
  8. F8: `uv run pitwall dashboard --bogus; echo "exit=$?"` prints `exit=2`, as the exit table now
     states.
  9. F9: the Agent Routing README and doctor guide say doctor changes nothing itself but probed
     harness CLIs may create files; `doctor` with an empty `HOME` exits 0.
  10. In a fresh clone, an agent given `Read qa/START-HERE.md and follow it.` creates
      `qa/.work/progress.md` and starts B0.
- **How to exercise it:** a fresh clone of `main` after merge, the README Quick Start environment,
  and any of the three agents.
- **Risk areas worth exploring:** links and anchors inside `qa/`; whether the coach keeps to rule
  R2; whether lesson commands still match the product.
- **Needs live credentials (the tester skips these):** none. Tier 5 missions are written and
  help-checked, never executed.

## Checklist

- [x] Tests added / updated: documentation only; no test changes needed
- [x] Gate commands pass
- [x] Documentation updated
- [x] User-facing change: QA notes filled in
- [x] DCO signed
````

- [ ] **Step 5: Open the pull request**

```bash
gh pr create --repo Buckeyes22/pitwall --base main --head docs/qa-onboarding \
  --title "Agent-guided QA onboarding program and nine doc fixes" --body-file /tmp/qa-pr-body.md
```

Expected: the pull request's address.

- [ ] **Step 6: One bounded wait on CI**

Run: `gh pr checks <N> --watch --interval 60`
Expected: every check passes, including `CI` and `Agent Routing CI / required` (the branch
touches `packages/agent-routing/`). A failure is read with `gh run view <run-id> --log-failed`,
fixed in place, pushed, and waited on again.

- [ ] **Step 7: Report**

Report to the maintainer: the pull request address, the final `gh pr checks <N>` output, the V4
and V5 summary lines, and the label list from Task 19. The pull request stays open for the
maintainer to merge. Merging makes `qa/` available to the tester's clone.

---

## Spec Coverage

| Spec section | Task |
| --- | --- |
| §4 D1 entry sentence, no root agent files | 2, 16 (GC-2, GC-3) |
| §4 D2 `qa/`, generic tester, `qa/.work/` ignored | 1, 3 (GC-1) |
| §4 D3 GitHub tracker, reviews, labels | 4, 13, 19 |
| §4 D4 hermetic until tier 5 | 2 (R1, R2), 15 |
| §4 D5 link, don't copy | GC-6, all content tasks, 20 |
| §4 D6 small files | GC-7, VC-shape |
| §4 D7 modes | 2 (R4, R5), 9 (B0) |
| §4 D8 no external links | GC-5, VC-shape, 20 Step 5 |
| §4 D9 fix every finding | 17, 21 Step 5 |
| §4 D10 PR template QA notes | 17 Step 6 |
| §4 D11 packet parity | 5 (maintaining-the-packet), 17 Step 8 |
| §4 D12 tier 5 help-checked only | 15 Step 2, 21 (`live`) |
| §5 layout and formats | 1, GC-8, lesson/mission/card contracts |
| §6 coach layer | 2 |
| §7 tester state | 1 (`.gitignore`), 3 (progress template) |
| §8 bootcamp | 9 |
| §9 missions and ladder | 10–15 |
| §10 concept cards | 6–8 |
| §11 handbook, severity, labels, lifecycle, acceptance | 4, 5, 19 |
| §12 templates | 3 |
| §13 changes outside `qa/` | 17, 19 |
| §14 safety model | 2, 5 (live safety), 15, GC-16, GC-17 |
| §15 maintainer setup | 16 |
| §16 keeping it current | 5, 17 |
| §17 V1–V7 | 20 (V1–V3), 21 (V4), 22 (V5), 23 (V6), 19 (V7) |

## Execution Record (2026-09-11)

Decisions taken when execution started, at the maintainer's direction to implement with MiniMax
M3 (`minimax-coding-plan/MiniMax-M3` through `opencode-shim`) while Claude orchestrates:

- **Verbatim files are extracted by script, not re-typed by a model.** The 28 files this plan gives
  verbatim are written by the orchestrator, which extracts each fenced block and converts
  `[text]{target}` to Markdown links. Those files belong to Tasks 2, 3, 4, 5, and 16, plus the
  concept index and reference card in Task 6, B0 in Task 9, and the mission ladder in Task 10.
  MiniMax lanes author the 71 contract-specified files and the Task 17 fixes.
- **One shared worktree, orchestrator commits.** Lanes write disjoint files in
  `~/git/pitwall-qa-onboarding` and never run state-changing git commands. After
  reviewing each lane, the orchestrator commits it with `git commit -s`. Task 18's rebase and
  fast-forward is therefore replaced by per-lane review and commit.
- **Lane sizes** are smaller than the plan's Tasks 9–14, so each fits one dispatch:
  - bootcamp B1–B3 and B4–B6
  - T1-01–T1-04 and T1-05–T1-07
  - T2-01–03, T2-04–06, T2-07–09, and T2-10–12
  - T3 as one lane
  - T4-01–03 and T4-04–07
  - T5 as one lane
  - the three concept-card groups, one lane each
  - the repository fixes
- **Comprehension route:** `q38-pi` replaces `q38-qwen`, which `model-routing routes list`
  reported as `needs-key`.
- **Quota outage:** the MiniMax Token Plan hit its usage limit (error 2056) while lanes 09a, 11c,
  14a, 14b, and 15 were running. The tier 4 and tier 5 drafts they left on disk were reviewed and
  committed. Tier 5 was corrected inline against the code, because it governs spend. Lanes 09a, 09b,
  11c, 11d, 17, and 17b are dispatched when the quota window reopens.
- **Product defects found during review and V4 are fixed, not ticketed.** Each is fixed in place
  with a regression test. The continuation tasks below record them.

## Continuation tasks (verification findings)

Each task names the files it touched and the command that proves it. All ran on
`docs/qa-onboarding`.

- **C1 — Personal serve refuses a missing routing CLI before launch.**
  - Files: `src/pitwall/personal/routes.py` (`RouteRunner.available`) and
    `src/pitwall/personal/service.py` (`PersonalServeService.plan`), plus
    `tests/personal/test_routes.py` and `tests/personal/test_service.py`.
  - Also: `docs/operator/personal-serving.md`, `docs/operator/troubleshooting.md`, and
    `docs/sdlc/18-cli.md` (the `routing_cli_missing` code).
  - Validate: `uv run pytest -q tests/personal` passes.
- **C2 — `pitwall init` smoke command carries the bearer header when a token is set.**
  - Files: `src/pitwall/cli.py` (`_print_smoke_inference_command`) and
    `tests/cli/test_init_seed.py`.
  - Validate: `uv run pytest -q tests/cli/test_init_seed.py` passes.
- **C3 — The capability audit evaluates the budget caps the spend path enforces.**
  - Files: `src/pitwall/audit/capability.py` (`CapabilityAuditService._configured_caps`) and
    `tests/audit/test_capability_audit.py`.
  - Validate: `uv run pytest -q tests/audit` passes, and the clean-room audit of `embedding.demo`
    reports `ready_to_invoke: true`.
- **C4 — An exhausted budget answers with `BudgetRejected` instead of `no_providers_available`.**
  - Files: `src/pitwall/routing/production.py` (`build_production_plan`,
    `ProductionRoutingService._plan_safe_payload`, `_budget_rejection`),
    `src/pitwall/api/routes/openai.py`, and `src/pitwall/api/routes/routing.py`.
  - Tests: `tests/routing/test_production_routing.py`, `tests/api/test_openai_proxy.py`, and
    `tests/api/test_production_routing_routes.py`. Doc: `docs/sdlc/04-routing.md`.
  - Validate: `uv run pytest -q tests/routing tests/api` passes, and journeys J15 and J16 pass.
- **C5 — A bad `REDIS_URL` gives a clear reconciler error instead of an import traceback.**
  - Files: `src/pitwall/reconciler/__init__.py` (`_redis_settings_from_env`),
    `src/pitwall/reconciler/__main__.py` (`main`), and `tests/reconciler/test_main.py`.
  - Validate: `uv run pytest -q tests/reconciler` passes.
- **C6 — The journey harness matches the code and is hermetic.**
  - Changes to `scripts/release/run-user-journeys.sh`:
    - it unsets inherited `PITWALL_API_TOKEN` and `PITWALL_API_SCOPED_TOKENS`;
    - J01 asserts `result.plan.selected_provider_id`;
    - J09 runs the TUI under `timeout --foreground`, so it cannot stop on a terminal read;
    - J22 uses `warm-volume --model` and checks leases for leaks.
  - The proxy header tests (`tests/api/test_openai_proxy.py` and
    `tests/api/test_openai_proxy_routes.py`) pin `RUNPOD_API_KEY` at request time, so J27 passes
    with the README exports set.
  - Validate: `bash scripts/release/run-user-journeys.sh`, with the README exports set, ends
    `27 passed, 0 failed`.
- **C7 — Operator docs that failed in the clean room.**
  - `docs/operator/install-acceptance-checklist.md` (K1–K3, the `python3` snippets, and the Step 6
    and Step 7 contracts).
  - `docs/operator/16-check-audit-procedure.md` (no printed secret, `uv run`, both auth headers).
  - `docs/operator/create-lb-endpoint.md` and `docs/operator/create-vllm-endpoint.md`
    (`serverless_lb`, the canonical GPU name, the capability name in the audit path, the bearer
    header).
  - `docs/operator/user-journey-catalog.md` (L3 source) and `README.md` (the `/docs` token note).
  - Validate: VC-links, VC-policy, and VC-secrets pass, and each changed command reruns in the
    clean room.
- **C8 — `docs/sdlc/18-cli.md` exit-code table covers every command.**
  - Lane 17b owns the table and adds rows for the 13 commands `pitwall --help` lists without a
    row. The orchestrator then corrects each cell against its handler, gives the `db` rows their
    flags, rewrites section 6 (argparse exits `2`; the two hand-written dispatchers exit `1`), and
    notes that `pitwall` with no arguments opens the console.
  - Validate: T1-04 step 2's `comm` command prints nothing, and `make docs-check` passes.
- **C9 — MCP tool errors keep the stable code the REST API returns.**
  - Files: `src/pitwall/mcp/safe_boundary.py` (reads `error_code` from the cause's class) and
    `tests/mcp/test_safe_boundary_codes.py`. Docs: `docs/sdlc/03-mcp-server.md` and T2-08.
  - Validate: `uv run pytest -q tests/mcp` passes.
- **C10 — `pitwall db` rejects unrecognized arguments.**
  - Files: `src/pitwall/db/__init__.py` (`main`: extra arguments exit `2` like argparse) and
    `tests/db/test_db_cli_help.py`.
  - Validate: `uv run pitwall db status --bogus; echo $?` prints the error and `2`.
- **C11 — `pitwall status` and `stop` keep their flags on the registry backend.**
  - Files: `src/pitwall/cli_personal.py` (`cmd_status` parses before choosing the backend) and
    `src/pitwall/personal/backend.py` (`RegistryBackend.status` and `stop` forward `--json`), plus
    `tests/cli/test_personal_verbs.py`. Doc: `docs/sdlc/18-cli.md` (`status` and `stop`).
  - Validate: with the README exports set, `uv run pitwall status --bogus` exits `2` and
    `uv run pitwall status --json` prints JSON; T2-05 step 4 guards it.
- **C12 — `pitwall db` usage lists its flags; `retention run` reports an unreachable database.**
  - Files: `src/pitwall/db/__init__.py` (`_usage`), `src/pitwall/retention/__main__.py` (`main`
    catches connection errors from `create_pool`), `tests/db/test_db_cli_help.py`, and
    `tests/retention/test_cli.py`.
  - Validate: `uv run pytest -q tests/db/test_db_cli_help.py tests/retention/test_cli.py` passes.
- **C14 — A down database no longer crashes the console or prints its password.**
  - Files: `src/pitwall/tui/app.py` (`_DeferredPoolOverviewSource`, `_DeferredPoolLeasesSource`,
    `PitwallApp._fatal_error` without frame locals), `src/pitwall/tui/errors.py`
    (`source_failure_message` redacts), `tests/tui/test_overview.py`, `tests/tui/test_errors.py`,
    and the dashboard failure modes in `docs/sdlc/18-cli.md`.
  - Validate: `uv run pytest -q tests/tui` passes; T2-07 step 7 shows `Overview unavailable:
    [Errno 111] ...` with no traceback and no password.
- **C13 — The pull request carries what the packet points at.**
  - B4 step 7 finds the packet's pull request by searching titles for `QA onboarding` and reads
    its `## QA notes` section, so the title contains `QA onboarding` and the body has that section.
  - Validate: `gh pr view <N> --json title,body --jq '.title, (.body | contains("## QA notes"))'`
    prints the title and `true`.

## Decisions (2026-09-18)

- Starting the tester -> invited `mikemcg94` (Michael McGraw) as a Write collaborator on 2026-09-18 (invitation pending acceptance); the maintainer sends the §4 kickoff message from `qa/maintainer-setup.md`. Tier 5 stays locked until a spend-capped key is issued.
- Pitwall core release `v0.1.0a3` -> prepare (version, changelog, release notes PR and the local checklist items) but do not publish; the maintainer sets `PITWALL_RELEASE_ENABLED` and tags when the window is chosen.
