# Agent-Guided QA Onboarding and Tester Program — Design

- **Date:** 2026-09-11
- **Status:** Draft for maintainer review
- **Branch:** `docs/qa-onboarding` (worktree `~/git/pitwall-qa-onboarding`, based on
  `origin/main` at `71885b9`)
- **Implementation plan:** `docs/superpowers/plans/2026-09-11-qa-onboarding.md`

## 1. Summary

Pitwall gets a `qa/` directory: a self-contained program that an AI coding agent reads in order to
train a beginner and turn them into the project's QA function. The tester opens any of three agents
(Claude Code, Codex, or opencode running the maintainer's self-hosted Qwen 3.8 27B) in their
Pitwall clone and types one sentence: `Read qa/START-HERE.md and follow it.` The agent becomes a
coach. It runs a short bootcamp, then assigns real QA missions in five tiers:

1. Documentation testing
2. Manual and exploratory testing
3. Acceptance testing of pull requests
4. Automated test writing
5. Live-provider testing (locked until the maintainer issues a spend-capped key)

Every mission ends in a real artifact on GitHub: an issue, a QA report on a pull request, or a test
pull request. A QA handbook defines the process the tester and the maintainer share: severity,
labels, triage, how QA is requested on a pull request, regression passes, and release sign-off.

The same batch fixes nine documentation defects found while researching this design (section 13.1).

## 2. Context

### 2.1 The tester

- Beginner technical skills. Works retail technical support, so they already diagnose customer
  problems, follow procedures, and write ticket notes. The teaching method builds on that.
- Develops on a Linux laptop.
- Will switch between Claude Code, Codex, and opencode with a local Qwen 3.8 27B model.
- Target role: the whole QA function for Pitwall.
- Committed files say "the tester". The tester's name and background live only in the ignored
  progress file (section 7), so the packet also works for future testers and is safe for a public
  repository.

### 2.2 What a tester can reach without spending money

| Surface | How the tester reaches it | Source of truth |
| --- | --- | --- |
| Operational CLI | `uv run pitwall ...`; the command list is whatever `pitwall --help` prints | `docs/sdlc/18-cli.md` |
| REST API | `uv run pitwall-api` on `127.0.0.1:8080`, including the Swagger UI at `/docs` | `docs/sdlc/02-api-rest.md` |
| Textual TUI | `uv run pitwall dashboard`; ten views bound in `src/pitwall/tui/app.py` (keys `o p l m s d t c e a`) | `docs/sdlc/18-cli.md` |
| MCP server | `pitwall-mcp` over local stdio only | `docs/sdlc/03-mcp-server.md` |
| Background services | `pitwall-reconciler`, `pitwall-webhook`, `pitwall-cost-exporter` | journeys J18–J20 |
| Test lanes | `make test`, `make up`, `make test-int`, `make down` | `CONTRIBUTING.md`, `docs/sdlc/17-testing-strategy.md` |
| Journey harness | `scripts/release/run-user-journeys.sh` (J01–J27, destructive to the local test DB) | `docs/operator/user-journey-catalog.md` |
| Agent Routing component | `packages/agent-routing`, its own uv project and `pitwall-agent-routing` CLI | `packages/agent-routing/README.md`, `packages/agent-routing/CONTRIBUTING.md` |

These surfaces need real credentials and can spend money: personal serving (`pitwall setup`,
`pitwall serve`, `pitwall stop`), RunPod resource control, onboarding apply, and live journeys L1–L4.
They belong to tier 5 only.

### 2.3 QA material that already exists

- `docs/operator/user-journey-catalog.md`: personas (including **Evaluator**, "a stranger who cloned
  the repo and follows the README with no prior context"), hermetic journeys J01–J27, and live
  journeys L1–L4.
- `scripts/release/run-user-journeys.sh`: one bash function per journey (`j01()` … `j27()`).
- `docs/operator/install-acceptance-checklist.md`: an 11-step hermetic acceptance runbook.
- `docs/operator/release-testing-checklist.md`: the human companion to the release gates.
- `docs/sdlc/17-testing-strategy.md`: markers, lanes, the hermetic environment guard, and the live
  egress gate.
- `.github/ISSUE_TEMPLATE/bug_report.md` and the default label set (`bug`, `documentation`,
  `question`, `good first issue`, …).
- `CONTRIBUTING.md` Definition of Done: documentation parity, testing parity, and "find → fix: every
  production bug ships with a hermetic regression test."
- The GitHub issue tracker is empty. The tester's reports will be its first issues.

### 2.4 Repository constraints this design must respect

- The repo-root `AGENTS.md` is the maintainer's local-only file, excluded through
  `.git/info/exclude`. No root agent-instruction file can be committed. There is no root
  `CLAUDE.md`, and adding one would load QA-coach instructions into every maintainer session.
- CI checks the packet on every push:
  - the internal link and anchor check over every tracked `.md` file (`tools/ci/check_markdown_links.py`)
  - the external link check with retries (`--external`)
  - the repository text policy over every tracked path (`tools/guards/repo_text_policy.py`; it bans,
    among other things, CGNAT-range addresses such as Tailscale IPs)
  - the detect-secrets baseline (`tools/security/check_secrets.py`)
  - DCO sign-off on every commit
- The sdist uses an include allowlist in `pyproject.toml`, so `qa/` never ships in a package.
- `packages/gateway` (the free-tier gateway) and the orchestrator-channel work exist only on
  unmerged branches. The packet documents what is on `main` when it lands. Section 16 covers how
  new surfaces enter it.
- `Buckeyes22/pitwall` is private, so the tester needs collaborator access. Branch protection on
  `main` requires a pull request and the strict `CI` check, with zero required approvals and no
  code-owner review. A tester's QA review therefore informs the maintainer but never gates a merge.
  Force pushes are disabled, and admins are not bound by these rules.

## 3. Goals and success criteria

### 3.1 Goals

- **G1:** A beginner starts from zero with one sentence to any of the three agents and is coached
  through everything, without the maintainer present.
- **G2:** Useful output starts early. The bootcamp's fresh-eyes README test produces the tester's
  first real issues.
- **G3:** The program covers the full QA function: docs, manual and exploratory, acceptance,
  automation, and gated live testing.
- **G4:** The weakest agent (the 27B model through opencode) can run the coach role correctly.
- **G5:** No step in tiers 0–4 can spend money or touch a cloud account. Tier 5 is locked by the
  absence of a key, not only by written rules.
- **G6:** The packet stays correct as Pitwall changes.

### 3.2 Success criteria (each is checked in the plan)

- **S1:** Every CI gate passes on the single pull request for this batch.
- **S2:** Every command in the bootcamp and tiers 1–4 was run on a fresh clone of the branch and
  matched the expected result the lesson states. Evidence is committed under `docs/evidence/`.
- **S3:** The Qwen comprehension pass (section 17) answers the fixed questionnaire correctly for
  `START-HERE.md`, every `coach/` file, and every bootcamp lesson.
- **S4:** Findings F1–F9 are fixed.
- **S5:** The QA labels in section 11.3 exist on `Buckeyes22/pitwall`.

## 4. Decisions

| Id | Decision | Rationale |
| --- | --- | --- |
| D1 | Plain Markdown that works in any agent. Entry is the kickoff sentence `Read qa/START-HERE.md and follow it.`, typed at the repo root. No root `AGENTS.md` or `CLAUDE.md`. | Serves three agents from one source. Respects the local-only `AGENTS.md` and keeps the maintainer's sessions clean. Agents run at the repo root, so `uv`, `make`, and sandbox write scopes work unchanged. |
| D2 | The packet lives in the Pitwall repo at `qa/` and is written for a generic tester. Personal state lives in `qa/.work/`, ignored by `qa/.gitignore`. | The tester gets it by cloning. It sits next to the code it describes, and CI link checks keep its anchors honest. Ignored state survives while the working clone exists, and agent sandboxes can write to it without extra permissions. |
| D3 | GitHub (`Buckeyes22/pitwall`) is the tracker. Findings are issues. Acceptance results are pull-request reviews whose body follows the QA report template, plus outcome labels. Test contributions are pull requests. | The maintainer chose it. It uses the real open-source workflow, the existing bug template, and CI. |
| D4 | Hermetic until tier 5. The tester's machine holds no RunPod key until the maintainer issues a spend-capped one. | Money safety for a beginner working with a 27B coach. The missing key is the lock. |
| D5 | Link, don't copy. Lessons link to headings in canonical docs and to journey ids and harness function names. Commands appear only where no canonical doc carries them, and never as secret-looking assignments. | The internal anchor check turns heading renames into CI failures instead of silently wrong lessons. It also keeps detect-secrets quiet. |
| D6 | Small files: lessons and missions under about 150 lines, concept cards under about 60, one topic per file, a fixed heading structure. | A 27B model has to hold a whole lesson at once and find the same sections every time. |
| D7 | Two modes. **Training** (default): the tester types every state-changing command, and the agent explains, predicts, checks, and may run read-only checks itself. **Work**: the agent may run allowed commands itself, but announces each one first and leaves the agent's approval prompts on. | Coding agents default to doing the work, and nothing is learned that way. Work mode lets a trained tester go faster without losing control. |
| D8 | No external Markdown links in `qa/`. URLs that must appear go in code spans. | The external link job retries but can still flake. Tool installation is guided by the agent, which knows the official instructions. |
| D9 | The nine doc defects found during research (F1–F9) are fixed in this batch. Doc defects found while verifying lesson commands (V4) are fixed in place in the same batch. | The maintainer's rule: every finding encountered is closed. |
| D10 | The pull-request template gets correct test commands (F4) and a `## QA notes` section. That section is how the maintainer requests acceptance testing, together with the `needs-qa` label. | The acceptance loop needs a standard handoff that the maintainer's own agents can fill in. |
| D11 | `CONTRIBUTING.md` Definition of Done gains a third standard, **QA packet parity**: a change that alters a command, route, screen, or heading a `qa/` lesson uses updates that lesson in the same pull request. | Mechanizes G6 the same way docs parity is already enforced at review. |
| D12 | Tier 5 missions are written and command-checked against `--help` and the docs. They are never executed while building the packet. | Building the packet must not spend money. The missions still have to exist so the locked tier is ready when unlocked. |

## 5. Packet architecture

### 5.1 Layout

```text
qa/
  START-HERE.md            agent entry: identity, read order, routing, state bootstrap
  README.md                human entry: what this is, how to start, how to request QA
  maintainer-setup.md      one-time setup and the kickoff message for a new tester
  .gitignore               ignores .work/
  coach/
    rules.md               non-negotiable rules R1–R14
    session-routine.md     start and end of every session
    teaching-method.md     explain → show → do → check → recap; hint ladder; analogies
    agent-guide.md         which agent for which job; verifying agent claims; approval settings
  bootcamp/
    B0-welcome.md
    B1-terminal-basics.md
    B2-install-and-clone.md
    B3-fresh-eyes-readme.md
    B4-git-and-github.md
    B5-pitwall-tour.md
    B6-qa-fundamentals.md
  missions/
    README.md              tier ladder, unlock rules, how the coach picks the next item
    tier1-docs/            T1-01 … T1-07
    tier2-manual/          T2-01 … T2-12
    tier3-acceptance/      T3-01 … T3-05
    tier4-automation/      T4-01 … T4-07
    tier5-live/            T5-00 … T5-05 (locked)
  concepts/                29 concept cards (section 10) plus README.md index
  handbook/
    README.md
    qa-role.md
    bug-reports.md
    triage-and-labels.md
    acceptance-testing.md
    exploratory-testing.md
    regression-and-release.md
    test-automation.md
    evidence-standard.md
    live-testing-safety.md
    maintaining-the-packet.md
  templates/
    progress.md            copied to qa/.work/progress.md in B0
    bug-report.md
    qa-report.md
    exploratory-session.md
    weekly-checkin.md
    tier-check.md
  .work/                   (ignored) progress.md, evidence/, notes/
```

File names inside `missions/tierN-*/` are the mission id plus a slug, for example
`missions/tier2-manual/T2-01-api-in-the-browser.md`.

### 5.2 Conventions for every packet file

- Markdown with no front matter. The first line is `# <id> — <title>` for lessons, missions, and
  cards.
- Relative links only, to files and headings (`../../docs/sdlc/18-cli.md#3-command-inventory`). No
  external Markdown links (D8).
- Commands go in fenced `bash` blocks. Each is followed by an **Expected** line describing what
  success looks like in words or a stable substring, never a full transcript.
- Environment values come from `README.md#quick-start` or
  `docs/operator/install-acceptance-checklist.md#step-2--dependency-installation` by link. No
  `NAME=value` lines for credentials or tokens appear in `qa/`.
- No hostnames, IP addresses, or endpoint URLs belonging to the maintainer's infrastructure. The
  Qwen endpoint reaches the tester privately (section 15).
- No literal secret-shaped test strings (fake cloud keys, fake tokens). A lesson that needs one,
  such as T2-04, describes its shape in words, and the coach builds it at run time in
  `qa/.work/`. Otherwise detect-secrets flags the lesson.
- Plain language, with every term defined on first use or linked to its concept card.

### 5.3 Lesson format (bootcamp)

```text
# B<n> — <title>
**Mode:** training | **Needs:** <previous lessons> | **Output:** <artifact or "none">

## Goal
## You will learn
## Before you start
## Steps
### Step 1 — <name>
Coach: <what to explain or ask first>
Tester does: <command or action>
Expected: <result>
If different: <what to check; when to record a finding>
## Checkpoint
## Done when
## Record in progress
## Next
```

### 5.4 Mission format (tiers 1–5)

```text
# T<tier>-<nn> — <title>
**Tier:** <n> | **Mode:** training or work | **Repeatable:** yes/no | **Needs:** <ids>
**Output:** issue(s) | QA report | pull request | progress entry

## Why this matters
## Sources            (links to canonical docs, journey ids, harness functions)
## Safety             (what not to run in this mission)
## Setup
## Steps
## What counts as a finding
## Done when
## Record in progress
```

### 5.5 Concept card format

```text
# <card title>
## In one sentence
## Why it matters when testing Pitwall
## Try it              (one safe command or observation, with Expected)
## Common confusions
## Check yourself      (1–2 questions; answers in a <details> block)
## Go deeper           (links to canonical docs)
```

## 6. The coach layer

### 6.1 `START-HERE.md`

Kept under 100 lines. Contents in order:

1. **Role:** "You are the QA coach for a beginner tester on the Pitwall project. You train them and
   help them do real QA work. You are not here to do the work for them."
2. **Read order:** `coach/rules.md`, `coach/session-routine.md`, `coach/teaching-method.md`,
   `coach/agent-guide.md`, then `qa/.work/progress.md`.
3. **Bootstrap:** if `qa/.work/progress.md` does not exist, create `qa/.work/` and copy
   `templates/progress.md` into it, then start `bootcamp/B0-welcome.md`.
4. **Routing:** a table mapping "the tester says X" to "open Y". Examples: continue, what's next,
   work mode, I found a bug, the maintainer asked me to test PR N, I'm stuck, explain a word, end
   session.
5. **Where things are:** a one-line map of the `qa/` folders.
6. **Repo facts the coach needs every session:** repo root, which commands are always safe (the
   read-only list from R3), where the canonical docs are.

### 6.2 `coach/rules.md` — the non-negotiable rules

Written as short numbered imperatives so a 27B model can quote them back:

- **R1 — No real credentials in tiers 0–4.** Never use, request, create, print, or store a real
  provider key, token, or endpoint secret. Use only the placeholder values linked from the README
  Quick Start.
- **R2 — Never-run list.** Outside tier 5, never run:
  - `pitwall setup`
  - `pitwall` with no arguments, or `pitwall dashboard`, unless the README Quick Start variables
    (including `DATABASE_URL`) are exported. Without `DATABASE_URL`, the console takes the personal
    backend, and in an interactive terminal with no credential it starts `pitwall setup`
    (`src/pitwall/cli.py:850`)
  - `pitwall serve` without `--dry-run`
  - `pitwall status` or `pitwall stop`, except the exact credential-free `pitwall status` form in
    T1-07, which reads input from `/dev/null`
  - `pitwall terminate-pod`
  - `pitwall warm-volume` or `pitwall register-template` without `--dry-run`
  - `pitwall runpod` anything except `--help`, `pitwall runpod-onboard apply`, and
    `pitwall runpod-onboard resume`
  - `pitwall volume-files`, `pitwall provider-ops`, and `pitwall retention run`
  - `--help` on any of these is always allowed
  - any command with `RUNPOD_LIVE`, `PITWALL_RUN_LIVE`, `PITWALL_RUNPOD_LIVE`, `--run-live`, or
    `-m live`
  - `db reset` or the journey harness against anything other than the local test stack
  - `git push` to `main`, `git push --force`, or `git clean` with `-x`
  - `sudo`, except during the B2 install steps
  - `rm -rf` outside `qa/.work/` and the tester's scratch clones
- **R3 — When unsure, stop.** If a command could spend money, contact a cloud provider, delete
  something outside `qa/.work/` or the local test stack, or post to GitHub, stop and ask. The
  read-only list is always fine: `git status`, `git log`, `git diff`, `ls`, `cat` on repo files,
  `docker ps`, `uv run pitwall --help`, `gh ... list` and `gh ... view`.
- **R4 — Training mode by default** (D7). The tester types state-changing commands. The coach may
  run read-only checks to confirm.
- **R5 — Work mode only when the tester asks for it.** In work mode the coach says what it will run
  and why before running it, and never turns off the agent's approval prompts.
- **R6 — Evidence or it didn't happen.** Every PASS, FAIL, "works", or "broken" statement names the
  command that was run in this session and quotes the relevant output. Never report a result that
  was not observed.
- **R7 — Bug or intended? Ask, don't guess.** When unsure whether behavior is a defect, add it to
  **Questions for the maintainer** in the progress file, or file a `question` issue if it is about product
  behavior.
- **R8 — QA does not fix product code.** In tiers 0–3, change nothing outside `qa/.work/`. In tier 4,
  change test files only, on a feature branch, in a pull request. A doc defect is filed as an issue.
  Fixing the doc is the maintainer's call.
- **R9 — The tester approves everything posted.** Draft issues, comments, and reviews into
  `qa/.work/notes/`. Show the draft and post only after the tester says yes (`gh ... --body-file`).
- **R10 — Redact.** Never paste keys, tokens, the Qwen endpoint, personal data, or full environment
  dumps into GitHub. Use synthetic ids.
- **R11 — Stay on the page.** Follow the current lesson's steps in order. When a linked doc and the
  real behavior disagree, that is a finding. Record it and do not improvise a workaround silently.
- **R12 — Protect the progress file.** Update `qa/.work/progress.md` at every checkpoint and at
  session end. Never delete `qa/.work/`.
- **R13 — One test stack at a time.** Check `docker ps` before starting the test stack. The
  local stack uses ports 5444 and 6380, and the API uses 8080. Lessons start the stack with
  `docker compose -f docker-compose.testinfra.yml up -d --wait` (the README form, which waits for
  health) and stop it with `make down`.
- **R14 — Severity 1 is urgent.** If a finding involves money, secrets, or safety (section 11.2),
  stop the lesson, file it with the tester, and tell the tester to message the maintainer directly.

### 6.3 `coach/session-routine.md`

**Session start:**
1. Read the files in the `START-HERE.md` read order.
2. Greet the tester by name. Recap the last session log entry in two sentences.
3. From tier 3 onward, check the work queue before curriculum work, using the queries in
   `handbook/triage-and-labels.md`: open severity 1 issues, then `gh pr list --label needs-qa`,
   then closed `found-by-qa` issues without `qa-verified` (section 11.4).
4. Propose today's item: the queue first, otherwise the next curriculum item. Confirm the mode.
5. Check the environment only as far as the item needs it (`docker ps`, `git status`, current
   branch).

**Session end, or when the tester says stop:**
1. Stop anything started (API process, `make down` if appropriate).
2. Update the progress file: completed items, the exact stopping point, raw findings not yet
   filed, questions for the maintainer, and coach notes.
3. Give a plain-language summary: what was done, what was learned, what comes next.

### 6.4 `coach/teaching-method.md`

- **Loop:** explain in two to four sentences, show the command and predict the result, the tester
  runs it, compare, then recap in one sentence.
- **One step per message.** Never send a wall of steps.
- **Define every term the first time,** or link its concept card. Record taught terms in coach
  notes so they aren't re-taught.
- **Hint ladder when the tester is stuck:** a question that points the right way, then a specific
  hint, then show the answer and have them repeat it.
- **Checkpoint questions** at the end of every lesson section. Record wrong answers in coach notes
  and revisit them.
- **Analogies from technical support:**
  - reproducing a bug is reproducing a customer's problem
  - a bug report is a good ticket note
  - "what changed recently?" is regression thinking
  - checking the cable first is checking the environment first
- **Let the tester decide** severity and wording first, then coach the result.
- **Celebrate real outcomes:** the first issue filed, the first QA report, the first merged test.

### 6.5 `coach/agent-guide.md`

- **Which agent for what:**
  - Qwen through opencode: bootcamp drills, concept cards, running journeys and checklists, and
    drafting session notes.
  - Claude Code or Codex: tier 4 test writing, reading large diffs in tier 3, and any step where
    Qwen gives inconsistent answers.
- **Switching agents:** the progress file makes every session resumable. Start the new agent with
  the kickoff sentence.
- **Verifying agent claims:** re-run the command yourself, check `git status` and `git diff`, and
  distrust any command that isn't in a lesson or in `--help`.
- **Approval settings:** keep command approval on in all three agents. Never use bypass or
  full-auto modes (for example Claude Code's bypass-permissions mode, or Codex's full-auto or
  dangerous-bypass flags).
- **Claude Code tip:** prefix a command with `!` to run it in the session so the agent sees the
  output.

### 6.6 Modes

The mode is recorded in the progress file (`Mode default`) and can be changed by the tester at any
time. Bootcamp is always training mode. Missions state a recommended mode. Tier 5 is always
training mode, and the tester runs every command.

## 7. Tester state (`qa/.work/`)

`qa/.gitignore` contains `.work/`. Contents:

- `progress.md`, copied from `templates/progress.md`
- `evidence/`: saved command output (`<command> 2>&1 | tee qa/.work/evidence/<item>-<step>.txt`)
- `notes/`: drafts of issues, reviews, and exploratory session notes before posting

`templates/progress.md` structure:

```text
# QA progress — <tester name>

## About me
- Name:
- Maintainer (who I report to, and how to reach them):
- Background:
- Agents I use:
- How I learn best (coach fills this in):

## Status
- Current tier: 0 (bootcamp)
- Unlocked tiers: 0 (<date>)
- Mode default: training
- Current item:
- Stopped at:

## Completed
| Item | Date | Output (issue / PR / report link) | Notes |
| --- | --- | --- | --- |

## Findings not yet filed

## Questions for the maintainer

## Session log
### <YYYY-MM-DD> — agent: <name>
- Did:
- Learned:
- Next:

## Coach notes
- Terms taught:
- Easy for the tester:
- Needs more practice:
```

The working clone is long-lived (for example `~/pitwall`). Fresh-eyes tests use throwaway clones
under a separate scratch folder (for example `~/qa-scratch/`). Rule R2 bans `git clean -x`, which
would delete `qa/.work/`.

## 8. Bootcamp

The bootcamp runs in training mode, in order. The fresh-eyes README test (B3) comes before the
Pitwall tour (B5) on purpose: once the tester understands Pitwall, they can never again read the
README as a stranger.

| Id | Title | Goal | Main steps | Output | Done when |
| --- | --- | --- | --- | --- | --- |
| B0 | Welcome | Set up the working relationship and the progress file | Bootstrap `qa/.work/`; ask name, background, agents, learning preferences; explain the program, tiers, modes, the rules in plain words, and how to ask questions; explain the kickoff sentence | `qa/.work/progress.md` | The tester can explain training vs work mode and three rules in their own words |
| B1 | Terminal basics | Comfort in a shell | `pwd`, `ls`, `cd`, paths, `cat`/`less`, tab completion, up-arrow history, Ctrl-C, copy/paste in the terminal, reading an error, pipes and `tee`, what `sudo` means and why R2 limits it | none | The tester navigates to the repo, lists `qa/`, and saves command output to `qa/.work/evidence/` with `tee` |
| B2 | Install and clone | A working toolchain | Detect the distro (`/etc/os-release`); install or verify git, `gh`, `curl`, `make`, `ss` (iproute2), `script` (util-linux), Docker Engine with the compose plugin plus docker-group access, and `uv`; confirm `gh auth status`; confirm the working clone; `uv sync --frozen --extra dev`; `uv run pitwall --version` | evidence files | Every tool reports a version; `docker run hello-world` succeeds; `uv run pitwall --help` prints usage |
| B3 | Fresh-eyes README test | Test the README as a stranger | Throwaway clone in the scratch folder; follow `README.md#quick-start` literally, top to bottom; the coach does not explain Pitwall and only helps with mechanics; every stumble, unclear sentence, missing prerequisite, or mismatch is recorded as a raw finding; finish with the dry-run inference call or the point where it breaks; tear down | raw findings in the progress file | README attempted end to end; findings recorded with the exact step and output |
| B4 | Git and GitHub | Read and work the GitHub workflow | Clone vs fork, branches, `git status`, `log`, `diff`, `switch`; what a commit and a pull request are; DCO sign-off (`git commit -s`); browsing issues and pull requests with `gh`; search for duplicates (`gh issue list --search`); read the merged pull request that added `qa/` and its checks | none | The tester finds that pull request's changed files and CI result with `gh` and explains what a sign-off certifies |
| B5 | Pitwall tour | Know what Pitwall is and how it's built | The one-page concept card; architecture diagram from `README.md#architecture`; the surfaces table (section 2.2 of this spec, rewritten for the tester); the safe zone vs tier 5; glossary cards for capability, provider, dry-run, routing, budget gate, kill switch; open `/docs` and the TUI briefly | none | The tester explains dry-run, hermetic, and why tier 5 is locked |
| B6 | QA fundamentals | Turn raw findings into real issues | Expected vs actual; reproduction from a clean state; severity scale; evidence standard; duplicate search; the bug report template; rewrite each B3 raw finding as an issue with the coach and file it with labels `bug` or `documentation`, `found-by-qa`, and severity | first GitHub issues | Every B3 finding is filed or dropped with a reason recorded; tier 1 unlocks |

## 9. Missions

### 9.1 Tier ladder

| Tier | Purpose | Unlocks when | Recommended mode |
| --- | --- | --- | --- |
| 0 | Bootcamp | Start | training |
| 1 | Docs QA | B0–B6 complete | training |
| 2 | Manual and exploratory | T1-01 and T1-02 complete, at least three issues filed that meet the bug-report checklist, and the maintainer agrees | training, then work |
| 3 | Acceptance | T2-01 to T2-07 complete, at least one exploratory session (T2-12), and the maintainer agrees | work |
| 4 | Automation | At least two QA reports posted (T3-02 or T3-03), at least one fix verified (T3-04), and the maintainer agrees | training for T4-01 to T4-03, then work |
| 5 | Live (locked) | The maintainer issues a spend-capped key and T5-00 passes | training, always |

"the maintainer agrees" is recorded as `Unlocked tiers: N (<date>, confirmed by the maintainer)` after the tester
reports the weekly check-in outcome. The coach runs the `templates/tier-check.md` quiz (three to five
questions from the tier's concept cards) before recording an unlock. Unlocked tiers stay open, and
repeatable missions from lower tiers remain available.

**How the coach picks the next item:** severity 1 work first, then `needs-qa` pull requests, then
fixed issues awaiting verification, then the lowest-numbered incomplete mission in the highest
unlocked tier, then an exploratory session.

### 9.2 Tier 1 — Docs QA

| Id | Mission | Sources | Output | Done when |
| --- | --- | --- | --- | --- |
| T1-01 | CONTRIBUTING walkthrough: dev environment and both test lanes | `CONTRIBUTING.md` Dev Environment, Running Tests | issues for any mismatch | `make test`, `make up`, `make test-int`, `make down` each run as documented, with evidence saved |
| T1-02 | Install checklist, part 1 (steps 1–6) | `docs/operator/install-acceptance-checklist.md` | issues | Each step's **Expected** compared and ticked or filed |
| T1-03 | Install checklist, part 2 (steps 7–11) | same | issues | same |
| T1-04 | CLI help vs CLI reference | `docs/sdlc/18-cli.md#3-command-inventory`, exit codes table; `uv run pitwall --help` and per-command `--help` | issues | Every command in `--help` is found in the reference or filed; every documented exit code that can be triggered hermetically is checked |
| T1-05 | Journey catalog vs harness | `docs/operator/user-journey-catalog.md`; `scripts/release/run-user-journeys.sh` `jNN()` functions | issues | For each hermetic row, the **Expected outcome** column matches what its function asserts, or an issue is filed |
| T1-06 | Configuration docs cross-check | `README.md#configuration`, `.env.example`, `docs/sdlc/16-core-config.md`; `uv run pitwall config check` | issues | Every variable in the README table is found in `.env.example` and the config chapter, or filed; `config check` behaves as documented with and without `DATABASE_URL` |
| T1-07 | Troubleshooting guide messages | `docs/operator/troubleshooting.md` | issues | Every entry that can be triggered hermetically is triggered and its message compared; entries needing live credentials are listed as tier 5 checks in the progress file |

### 9.3 Tier 2 — Manual and exploratory

| Id | Mission | Sources | Output | Done when |
| --- | --- | --- | --- | --- |
| T2-01 | The REST API in a browser | J07; `docs/sdlc/02-api-rest.md`; `/docs` Swagger UI | issues | Every J07 route opened in Swagger with a 200; one request built and executed in the UI |
| T2-02 | The REST API with curl | J01, J08, J17; `docs/sdlc/02-api-rest.md` | issues | Dry-run inference, unknown capability (404), malformed body (422), and unknown workload (404) reproduced, with the error bodies compared to the docs |
| T2-03 | The API's locks: auth, admin, rate limit | J12, J13, J14; `docs/sdlc/14-security.md` | issues | 401/403/200 matrix and the 429 with `Retry-After` reproduced |
| T2-04 | Money safety: budget gate, proxy, kill switch, guardrails | J15, J16, J21; `docs/sdlc/05-cost-budget.md`; `docs/sdlc/18-cli.md` guardrails section | issues (severity 1 if a gate fails open) | 402 before any provider call, from the inference route and the OpenAI proxy; the proxy URL-injection path refused with a 4xx; the kill-switch drill (always `terminate_compute: false`) refused without the admin secret and reported with it; guardrail preview decisions match the rules table for made-up secret and personal-data values, and bad JSON exits 2 |
| T2-05 | The CLI off the happy path | `docs/sdlc/18-cli.md` exit codes and failure modes | issues | Bad arguments, missing environment, and `--json` output checked for ten or more commands; exit codes compared |
| T2-06 | Database lifecycle guards | J06 | issues | `db status`, idempotent `migrate`, `reset` refused without `--force`, refused for a remote host, allowed locally |
| T2-07 | TUI tour and exploratory pass | J09; `docs/sdlc/18-cli.md` TUI sections | issues | All ten views opened by key and by `:` palette; `?`, `/`, `Escape`, and resize tried; type-to-confirm dialogs opened and cancelled, never confirmed |
| T2-08 | Pitwall as an agent tool (MCP) | J10, J11; `docs/sdlc/03-mcp-server.md` | issues | The tester's own agent lists tools and runs a dry-run inference through the stdio server; network transport refused |
| T2-09 | Background services | J18, J19, J20; `docs/sdlc/09-webhooks.md`, `docs/sdlc/10-reconciler-lifecycle.md`, `docs/sdlc/13-observability.md` | issues | Webhook unsigned/signed/replayed, reconciler check mode, and exporter `/metrics` reproduced |
| T2-10 | Journey harness vs your manual results | `scripts/release/run-user-journeys.sh` | progress entry, issues | Harness run against the local stack; any journey where the harness and the tester's manual result disagree is investigated and filed |
| T2-11 | Agent Routing component checks | `packages/agent-routing/CONTRIBUTING.md`; `pitwall-agent-routing doctor` | issues | The component test lane passes as documented; `doctor` runs with `HOME` pointed at a temporary directory so the tester's real agent configs are untouched |
| T2-12 | Exploratory session (repeatable) | `handbook/exploratory-testing.md` charter list | session notes, issues | One charter explored within the session, notes saved from `templates/exploratory-session.md`, findings filed |

### 9.4 Tier 3 — Acceptance

| Id | Mission | Sources | Output | Done when |
| --- | --- | --- | --- | --- |
| T3-01 | Practice acceptance on a merged PR | The merged pull request that added `qa/`; its `## QA notes` list the F1–F9 fixes as acceptance criteria | local QA report in `qa/.work/notes/` (not posted) | Report drafted from the template with a verdict and evidence for every F1–F9 criterion |
| T3-02 | QA a dependency update PR | an open Dependabot PR that touches a surface the tester knows (for example the Textual or FastAPI bump) | posted QA review | Hermetic suite plus a targeted smoke test of the affected surface; review posted; outcome label set |
| T3-03 | Acceptance-test a `needs-qa` PR (repeatable) | the PR's `## QA notes` and linked plan; `handbook/acceptance-testing.md` | posted QA review, issues | Every acceptance criterion has a result with evidence; regression smoke run; review and labels posted |
| T3-04 | Verify fixes for issues you filed (repeatable) | closed `found-by-qa` issues without `qa-verified` | issue comments | Repro re-run on current `main`; `qa-verified` added, or the issue reopened with evidence |
| T3-05 | Release candidate regression pass (repeatable) | `docs/operator/release-testing-checklist.md` sections 1–7 hermetic parts; full journey harness | sign-off report | Every hermetic section run with evidence; report posted where the maintainer asks (release PR or issue) |

### 9.5 Tier 4 — Automation

| Id | Mission | Sources | Output | Done when |
| --- | --- | --- | --- | --- |
| T4-01 | Reading and running the test suite | `docs/sdlc/17-testing-strategy.md` sections 1–3; `tests/conftest.py`; `tests/_hermetic_env.py`; `tests/fakes/` | progress entry | Runs one file, one test, and one marker lane; breaks one assertion locally, sees red, reverts, sees green |
| T4-02 | The pre-PR routine and reading CI | `CONTRIBUTING.md` Quality Gates and PR Process; `handbook/test-automation.md` | progress entry | Branch, `ruff check`, `ruff format --check`, `make test`, signed commit, and a CI log read on an existing PR |
| T4-03 | Your first regression test | a fixed `found-by-qa` issue; the find → fix rule | pull request | A hermetic test that fails with the fix reverted locally and passes with it; PR opened with DCO sign-off and green CI |
| T4-04 | CLI contract tests | `tests/cli/`; a CLI error path found in T2-05 | pull request | Test added and merged, or review feedback addressed |
| T4-05 | API contract tests | `tests/api/`; error shapes from T2-02 and T2-03 | pull request | same |
| T4-06 | Journey harness assertions | `scripts/release/run-user-journeys.sh` | pull request | A `jNN()` function asserts what its catalog row claims; catalog and harness agree |
| T4-07 | Property tests with Hypothesis | `tests/property/`; `docs/sdlc/17-testing-strategy.md` | pull request | One invariant expressed as a property test, passing under the thorough profile |

### 9.6 Tier 5 — Live (locked)

Every tier 5 mission starts with a spend check and ends with a cleanup check: nothing running in
`pitwall status` and nothing in the provider console. The key is stored outside the repository
with `chmod 600`, and nothing from the key file is ever printed.

| Id | Mission | Sources | Output | Done when |
| --- | --- | --- | --- | --- |
| T5-00 | Live safety briefing and key handling | `handbook/live-testing-safety.md`; `docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner` | quiz result in progress | Tier check passed; key stored correctly; the tester explains TTL, max price, kill switch, `pitwall stop`, and orphan checks |
| T5-01 | Read-only live checks | `docs/operator/runpod-market.md`; `pitwall runpod` read operations | issues | Catalogue and balance reads compared to docs with no spend |
| T5-02 | Personal serving smoke | `docs/operator/personal-serving.md`, `docs/operator/serve-quickstart.md` | issues, cleanup evidence | Smallest suitable GPU, tight TTL and max price; serve, status, request, stop; cleanup verified |
| T5-03 | Pod lease lifecycle (L3) | `docs/operator/serve-quickstart.md`, `docs/sdlc/06-leases.md` | issues, cleanup evidence | Launch, renew, stop verified |
| T5-04 | Live audit (L4) | `docs/operator/16-check-audit-procedure.md` | audit report | Audit run as documented |
| T5-05 | Serverless endpoints (L1, L2) | `docs/operator/create-lb-endpoint.md`, `docs/operator/create-vllm-endpoint.md` | issues, cleanup evidence | Endpoint created, round trip done, endpoint removed |

## 10. Concept cards

`concepts/README.md` indexes the cards in three groups.

- **Computer basics (11):**
  - `terminal-and-shell`
  - `files-paths-and-permissions`
  - `environment-variables`
  - `processes-ports-and-localhost`
  - `docker-and-compose`
  - `git-basics`
  - `github-issues-and-prs`
  - `http-and-status-codes`
  - `json-and-apis`
  - `logs-tracebacks-and-exit-codes`
  - `python-uv-and-venvs`
- **Pitwall (10):**
  - `pitwall-in-one-page`
  - `capabilities-and-providers`
  - `dry-run-and-hermetic`
  - `routing-and-plans`
  - `cost-budget-and-guardrails`
  - `pods-leases-and-serving`
  - `kill-switch`
  - `background-services`
  - `mcp-and-agent-clients`
  - `the-tui`
- **QA (8):**
  - `expected-vs-actual`
  - `severity-and-priority`
  - `reproducing-a-bug`
  - `exploratory-testing`
  - `regression-and-smoke-tests`
  - `test-lanes-and-markers`
  - `pytest-fixtures-and-fakes`
  - `flaky-tests`

Each card follows section 5.5 and links to the canonical doc heading that goes deeper.

## 11. QA handbook

The handbook is the shared operating manual for the tester and the maintainer. The coach reads it
when a mission links to it. The maintainer reads `acceptance-testing.md`, `triage-and-labels.md`,
and `maintaining-the-packet.md`.

### 11.1 Documents

- **`qa-role.md`:** what the QA function owns and what the maintainer owns.
  - QA owns: finding and reporting defects with evidence, acceptance verdicts, fix verification,
    regression passes, test contributions, and packet bug reports.
  - The maintainer owns: priority, fixes, merges, tier unlocks, and live credentials.
  - Also covers the weekly check-in agenda (`templates/weekly-checkin.md`).
- **`bug-reports.md`:**
  - The bug-report checklist: specific title, numbered steps from a clean state, expected vs
    actual, evidence, environment, severity, redaction done, duplicate search done.
  - How to post with `gh issue create --title ... --body-file ... --label ...`.
  - `templates/bug-report.md` mirrors the headings of `.github/ISSUE_TEMPLATE/bug_report.md` and
    adds Severity, Found during, and Evidence.
- **`triage-and-labels.md`:** the severity scale (11.2), the label set (11.3), and the issue
  lifecycle (11.4).
- **`acceptance-testing.md`:** the pull-request QA loop (11.5), for both sides.
- **`exploratory-testing.md`:**
  - What a charter is, and the session notes template.
  - Heuristics: boundaries, wrong order, interruption, missing configuration, bad input types and
    sizes, Unicode, repetition and idempotency, concurrency across two terminals, restart mid-flow.
  - A Pitwall charter list of at least twelve charters. Each names a surface and a risk, for example:
    - "Explore capability creation with unusual names and classes to discover validation gaps"
    - "Explore inference request bodies with odd sizes and types to discover 500s"
    - "Explore CLI commands with missing or wrong environment to discover unclear errors"
    - "Explore the TUI at small terminal sizes to discover layout breakage"
    - "Explore the admin API with partial credentials to discover auth gaps"
    - "Explore repeated seed and init runs to discover idempotency problems"
- **`regression-and-release.md`:** the QA smoke set, in order:
  1. `make test`
  2. `docker compose -f docker-compose.testinfra.yml up -d --wait`, `make test-int`, `make down`
  3. README Quick Start dry-run inference
  4. `/docs` loads
  5. TUI opens and every view is reachable
  6. MCP stdio lists tools

  Also covers when to run the full journey harness, and the release candidate pass (T3-05) with its
  sign-off report format.
- **`test-automation.md`:** conventions for the tester's pull requests.
  - Hermetic by default. Place tests next to the surface's existing tests. Use markers.
  - Find → fix: a regression test must fail without the fix.
  - Pre-PR gates: `uv run ruff check .`, `uv run ruff format --check .`, `make test`, and
    `make test-int` when touching DB paths.
  - `git commit -s`, the branch naming from `CONTRIBUTING.md`, one concern per pull request, and
    how to read CI failures with `gh pr checks` and `gh run view --log-failed`.
- **`evidence-standard.md`:** what counts as evidence.
  - It is: the exact command, the exit code, a relevant output excerpt, the commit tested
    (`git rev-parse --short HEAD`), and the environment.
  - It is not: an agent's summary, "it works on my machine" without output, or a screenshot without
    the command behind it.
  - How to save output with `tee`, and how to attach excerpts to issues.
- **`live-testing-safety.md`** (tier 5):
  - Key storage and redaction.
  - Spend ceilings in layers: the dedicated account's prepaid balance,
    `PITWALL_MONTHLY_BUDGET_USD`, and the per-launch TTL and maximum price.
  - The kill switch, orphan-pod checks, and the mandatory cleanup check.
  - The rule that a surprise charge is severity 1.
- **`maintaining-the-packet.md`:** section 16.

### 11.2 Severity scale

| Severity | Meaning | Pitwall examples |
| --- | --- | --- |
| 1 — critical | Money, secrets, or safety | Spend without passing the budget gate; a key or token in output, logs, or an error; a dry-run that calls a real provider; kill switch fails; auth bypass |
| 2 — high | A documented core journey fails with no workaround | README Quick Start breaks; API returns 500; TUI crashes on boot; a migration fails on a clean database |
| 3 — medium | Wrong behavior or wrong docs, with a workaround | A documented command fails but a nearby variant works; wrong exit code; misleading error text; doc and code disagree |
| 4 — low | Cosmetic | Typos, formatting, confusing wording, TUI alignment |

Severity is the tester's call and follows these definitions. Priority is the maintainer's call
during triage.

### 11.3 Labels

Existing labels are reused: `bug`, `documentation`, `question`, `duplicate`, `invalid`, `wontfix`,
`good first issue`, `help wanted`.

New labels, created in this batch:

| Label | Use |
| --- | --- |
| `found-by-qa` | Every issue the tester files |
| `severity:1-critical`, `severity:2-high`, `severity:3-medium`, `severity:4-low` | Exactly one per `found-by-qa` bug |
| `needs-qa` | A pull request is ready for acceptance testing |
| `qa-passed`, `qa-failed` | Acceptance outcome; replaces `needs-qa` |
| `qa-verified` | A closed `found-by-qa` issue whose fix the tester confirmed on `main` |
| `qa-packet` | A defect in the `qa/` training packet itself |

### 11.4 Issue lifecycle

1. The tester files an issue: `bug` or `documentation`, plus `found-by-qa` and a severity.
2. The maintainer triages it:
   - keeps it, or closes it with `duplicate`, `invalid`, or `wontfix` and a reason
   - answers `question` issues
3. A fix pull request closes it with `Closes #N`.
4. The tester runs T3-04: re-tests on `main`, then adds `qa-verified` or reopens it with evidence.

Queries the coach uses:

- Pending verifications: `gh issue list --state closed --search "label:found-by-qa -label:qa-verified"`
- Open severity 1: `gh issue list --state open --label severity:1-critical`
- Acceptance queue: `gh pr list --label needs-qa`

### 11.5 Acceptance loop

**Maintainer side** (the maintainer or their agents):
- Fill in the `## QA notes` section of the pull request: what changed for users; the plan or task
  numbers carrying the acceptance criteria; how to exercise it; risk areas; anything that needs live
  credentials, which the tester skips.
- Add `needs-qa`.

**Tester side:**
1. `gh pr list --label needs-qa`
2. `gh pr view N`
3. `gh pr checkout N`
4. `uv sync --frozen --extra dev` (Agent Routing pull requests follow its own `CONTRIBUTING.md`
   setup)
5. Baseline `make test`
6. Each acceptance criterion, with evidence
7. An exploratory pass around the changed area
8. The regression smoke set
9. Draft the review from `templates/qa-report.md`
10. After the tester approves: `gh pr review N --approve`, `--request-changes`, or `--comment`
    with `--body-file`
11. Swap `needs-qa` for `qa-passed` or `qa-failed`
12. File an issue for each defect and link it from the review

**QA report fields:**
- Verdict (pass, fail, or pass with issues)
- Commit tested
- Environment
- Acceptance table: criterion, command, result, evidence
- Exploratory notes
- Regression smoke results
- Issues filed
- Questions for the maintainer
- Anything skipped, and why (for example live-only criteria)

## 12. Templates

| Template | Used by | Fields |
| --- | --- | --- |
| `progress.md` | B0 | Section 7 |
| `bug-report.md` | B6, all tiers | GitHub bug template headings plus Severity, Found during (lesson or mission id, journey id), Evidence (command, exit code, excerpt, commit) |
| `qa-report.md` | T3-01 to T3-03 | Section 11.5 fields |
| `exploratory-session.md` | T2-12 | Charter, setup, notes log, findings, questions, areas not reached, new charter ideas |
| `weekly-checkin.md` | Weekly | Issues filed, QA reports, tests merged, tier progress, questions for the maintainer, blockers, what I learned |
| `tier-check.md` | Tier unlocks | Per-tier question bank referencing concept cards, pass rule (all correct after at most one hint each) |

## 13. Changes outside `qa/`

### 13.1 Documentation defects fixed in this batch

| Id | Where | Defect | Fix |
| --- | --- | --- | --- |
| F1 | `README.md` line 60, "Operator TUI" row | Lists seven views; `src/pitwall/tui/app.py` binds ten (Serve, Pods, and Routes are missing) | List all ten views, in the order of the app's bindings |
| F2 | `docs/operator/user-journey-catalog.md`, J09 row | The expected outcome says "all six screens registered". `j09()` only asserts that the TUI boots without a traceback, and the app has ten views | Change the expected outcome to what `j09()` asserts: "Alive after boot window, no traceback". A stronger harness assertion becomes mission T4-06 |
| F3 | `docs/sdlc/18-cli.md` line 108 (TUI Responsibility paragraph) and lines 645–649 (`cmd_dashboard` read-only paragraph) | Line 108 says the TUI "ships read-only Overview, Providers, Pods / Leases, and Models views, with one guarded mutation". Lines 645–649 name only the model launch and Resources mutations. The app binds ten views, and the personal Serve launch and Pods stop are type-to-confirm guarded (`src/pitwall/tui/personal.py:171`, `:249`, `:332`, `:350`), as is the onboarding apply (exact plan-ID modal) | Name all ten views in both places and list every guarded mutation with a link to its addendum; verify each claim against `src/pitwall/tui/` |
| F4 | `.github/pull_request_template.md` Testing section and checklist | Uses bare `pytest tests/ -v` and `pytest tests/integration/ -v`. These contradict `CONTRIBUTING.md` (`make test`; `make up && make test-int && make down`); `tests/integration/` is not the integration lane (the lane is `-m integration`); bare `pytest` bypasses `uv` | Replace the commands and checklist with the `CONTRIBUTING.md` targets |
| F5 | `docs/operator/troubleshooting.md`, "Registry-backed `serve`/`warm-volume` exits 2 needing `DATABASE_URL`" | The Symptom quotes only the JSON body. Without `--json`, `pitwall warm-volume ... --dry-run` exits 2 with a human panel reading `warm-volume needs DATABASE_URL (registry-backed launch planning)` (verified in an empty `HOME` on `71885b9`) | State the human message first and the `--json` body second |
| F6 | `docs/sdlc/18-cli.md` line 708, exit-code table row for `pitwall config check` | Puts "Settings load error / domain-config errors → `EX_CONFIG`" in the **Exit `1`** column. `cmd_config` returns `os.EX_CONFIG` (78) on both error paths (`src/pitwall/cli.py:1294`, `src/pitwall/cli.py:1301`); verified exit 78 with `DATABASE_URL` unset | Empty the Exit `1` cell and state below the table that `config check` errors exit `78` (`os.EX_CONFIG`), citing both lines |
| F7 | `CONTRIBUTING.md` line 78, Quality Gates "Pattern scrub" row | Uses bare `python tools/guards/repo_text_policy.py <files>`; CI runs `uv run python tools/guards/repo_text_policy.py`, and the repository forbids bare `python` | Change the command to `uv run python tools/guards/repo_text_policy.py <files>` |
| F8 | `docs/sdlc/18-cli.md` exit-code row for `pitwall dashboard` (line 716) and the `cmd_dashboard` component bullets (lines 641–643) | The row puts "Argument parsing" under Exit `1`, but argparse exits `2` (verified: `pitwall dashboard --bogus` exits 2). The component bullets omit that with the personal backend and no credential, the command starts `pitwall setup` in an interactive terminal or exits `2` with the credential message otherwise (`src/pitwall/cli.py:850`–`862`) | Move argument parsing and the non-interactive credential refusal to Exit `2`, keep unhandled app errors under Exit `1`, and add the credential behavior to the component bullets |
| F9 | `packages/agent-routing/README.md` line 397 ("The default doctor is local, read-only") and `packages/agent-routing/docs/doctor.md` line 20 | Doctor writes nothing itself, but it runs each installed harness's local help and version commands. On an empty `HOME` those CLIs created eight first-run, state, and log files (`~/.hermes/SOUL.md`, `~/.hermes/.update_check`, `~/.hermes/logs/*`, `~/.cline/cli-node-extra-ca-certs.pem`, `~/.pi/agent/auth.json`, `~/.pi/agent/models-store.json`, a goose CLI log) | State that doctor changes nothing itself, and that probed harness CLIs may create their own first-run, state, or log files |

### 13.2 Other repository changes

- **`.github/pull_request_template.md`:** add a `## QA notes` section (D10), with prompts for user
  impact, acceptance criteria or plan tasks, how to exercise, risk areas, and live-only items. Add
  one checklist line: "User-facing change: QA notes filled and `needs-qa` label added."
- **`CONTRIBUTING.md`:** add Definition of Done standard 3, QA packet parity (D11). Add a sentence
  under a new `## Testing help` heading that links `qa/README.md`.
- **`README.md`:** one sentence in `## Contributing` pointing testers to `qa/README.md`, plus the F1
  fix.

### 13.3 GitHub changes

- Create the labels in section 11.3 with `gh label create`, with colors and descriptions.
- Adding the tester as a collaborator (Write role) needs the tester's GitHub username. It is a step
  in `qa/maintainer-setup.md` for the maintainer to perform.

## 14. Safety model

Protection comes in layers, strongest first:

1. **No key exists.** The tester's machine never holds a real provider credential before tier 5.
   Every paid path fails at authentication with the placeholder key.
2. **Pitwall's own gates.** Dry-run defaults, budget admission, type-to-confirm dialogs,
   `--force` on `db reset` and the remote-host refusal, the live egress guard in `tests/conftest.py`,
   and loopback bindings.
3. **Agent approval prompts stay on** (R5, `coach/agent-guide.md`).
4. **Written rules** R1–R3, R8, and R13, quoted at the start of every mission's `## Safety` section.
5. **Tier 5 procedure:**
   - a dedicated provider account whose small prepaid balance is the hard ceiling, which the
     maintainer provisions
   - `PITWALL_MONTHLY_BUDGET_USD`
   - per-launch TTL and maximum price
   - mandatory cleanup checks
   - training mode always

## 15. Maintainer setup (`qa/maintainer-setup.md`)

A checklist for onboarding any tester:

1. Invite the tester's GitHub account to `Buckeyes22/pitwall` with the Write role, and confirm
   they accepted.
2. Confirm the QA labels exist (`gh label list`).
3. Agent access:
   - Claude Code and Codex accounts or seats for the tester
   - opencode installed and configured with the maintainer's Qwen endpoint as an OpenAI-compatible
     provider
   - the base URL and key are sent privately, stored only in the tester's opencode config, and
     never committed or pasted into GitHub
4. Send the kickoff message, reproduced in full in the file:
   - accept the invite
   - install the agent CLIs
   - install `gh` and run `gh auth login`
   - `gh repo clone Buckeyes22/pitwall ~/pitwall`
   - `cd ~/pitwall`
   - start the agent there and type `Read qa/START-HERE.md and follow it.`
   - keep approval prompts on
5. Weekly check-in: triage `found-by-qa` issues, answer the "Questions for the maintainer" list, confirm tier
   unlocks.
6. Requesting QA on a pull request: section 11.5, maintainer side.
7. Unlocking tier 5:
   - provision a dedicated provider account with a small prepaid balance and a key
   - hand it over privately
   - agree on `PITWALL_MONTHLY_BUDGET_USD`, TTL, and maximum price
   - confirm T5-00 is complete

## 16. Keeping the packet current

- **D11 (packet parity):** a pull request that changes a surface a lesson uses updates the lesson in
  the same pull request. The internal anchor check catches heading renames. Review catches the rest.
- **New surfaces enter through acceptance.** When a pull request adds a user-facing surface (for
  example the free-tier gateway or the orchestrator channel), its `## QA notes` drive the tester's
  T3-03 run. The same pull request adds any new standing mission or concept card that the surface
  needs.
- **Packet bugs are QA findings.** The tester files issues with `qa-packet` whenever a lesson is
  wrong, unclear, or out of date. Fixes land like any documentation fix.
- **Commands are re-verified** whenever a lesson changes, by running them on a fresh clone the same
  way as S2.

## 17. Verifying the packet

| Check | Command or method | Proves |
| --- | --- | --- |
| V1 Internal links and anchors | `uv run python tools/ci/check_markdown_links.py` | Every `qa/` link and anchor resolves |
| V2 Text policy | `git ls-files -z \| xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py` (the CI command) | No banned literals |
| V3 Secrets | `uv run python tools/security/check_secrets.py` | No new secret-like strings |
| V4 Command verification | Run every command in B1–B6 and T1–T4 on a throwaway clone of the branch against the local test stack; record command, exit code, and excerpt | S2; lessons' **Expected** lines are true |
| V5 Weak-model comprehension | Dispatch the maintainer's Qwen 3.8 27B (route profile `q38-pi`, `qwen3.8-27b-huihui-int8-mtp` through the Pi harness, because `q38-qwen` reported `needs-key` on 2026-09-11 and the maintainer's opencode has no route to this model) from a fresh clone. Give it a fixed questionnaire per file group: first action, forbidden commands, who types in training mode, the lesson's done-when, and what to do on an unexpected result. Compare with the answer key | S3; G4 |
| V6 CI | The pull request's `CI` check | S1 |
| V7 Labels | `gh label list --repo Buckeyes22/pitwall` | S5 |

V4 and V5 evidence is committed as `docs/evidence/<run date>-qa-packet-verification.md`: a table
of lesson or mission id, step, command, exit code, and result, followed by the comprehension
results. Commands that post to GitHub (`gh issue create`, `gh pr review`, `gh pr create`) are
checked against their `--help` and the existing label set, never executed, because verification
must not publish. Tier 5 commands are checked only against `--help` and the docs (D12).
