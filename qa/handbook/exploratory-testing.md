# Exploratory testing

Exploratory testing is learning, designing, and testing at the same time. You don't follow a
script. You follow a charter and your curiosity, and you write down what you see.

## What a charter is

A charter is one sentence: **Explore** (area) **with** (resources or technique) **to discover**
(kind of problem). It keeps a session focused without scripting it. Example: "Explore the CLI
with missing environment variables to discover unclear error messages."

## Running a session

1. Pick a charter from the [Pitwall charter list](#pitwall-charter-list) or write your own.
2. Copy `qa/templates/exploratory-session.md` to `qa/.work/notes/explore-<date>-<topic>.md`.
3. Set up: the commit you are testing, the test stack if needed, the starting state.
4. Explore. Write every notable thing in the notes log as you go, including what worked.
5. Stop at the end of the session, even if you are not done. Write down the areas not reached.
6. Turn findings into issues ([bug reports](bug-reports.md)) and questions into "Questions for the
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
