# Troubleshooting and cleanup

This page covers what to do when something goes wrong, and how to remove
Pitwall from your machine afterward. It focuses on the no-database personal
workflow (`pitwall setup` / `serve` / `status` / `stop`), since that is the
path that creates local state, a shell-profile edit, and paid RunPod pods on
your own account. For the everyday mechanics of that workflow — what each
flag does, the full refusal/failure code table, and where local state lives —
see [Personal serving](personal-serving.md); this page only covers what that
one explicitly hands off here: an interrupted or crashed `serve`, a pod that
outlived its owner, and removing everything Pitwall left behind. For the
database-backed path, see [Serve a model: operator quickstart](serve-quickstart.md).

Find your symptom below.

## "no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY"

**Symptom:** any personal-backend command (`pitwall serve`, `status`, `stop`)
exits 2 with this exact message.

**Cause:** Pitwall looked for `RUNPOD_API_KEY` in your environment, then in
`~/.runpod/config.toml` (the file `runpodctl` writes on sign-in), and found
neither. If you're at an interactive terminal, `pitwall serve`/`status`/`stop`
already tried to run `pitwall setup` for you once before printing this — so
seeing the message means setup either wasn't offered (non-interactive shell,
e.g. a script or CI) or you declined it.

**Fix:** run `pitwall setup` (or `pitwall setup --yes` non-interactively), or
export `RUNPOD_API_KEY` yourself. See
[What `pitwall setup` changes on your machine](personal-serving.md#what-pitwall-setup-changes-on-your-machine)
for the full credential-resolution order.

This is a different credential from the one behind the next symptom — this
one is your RunPod account key; the other is Pitwall's own per-machine
endpoint secret.

## "note: PITWALL_ENDPOINT_KEY is not set" / a route won't authenticate

This is explained in full, including the fix, under
[Checking status: `pitwall status`](personal-serving.md#checking-status-pitwall-status)
in Personal serving — it means the shell you're running from never picked up
the `export PITWALL_ENDPOINT_KEY=...` line `pitwall setup` added to your
profile.

## `pitwall serve` refused the launch or failed after launching

Every `refused: <code>` (before a pod exists) and `failed after launch:
<code>` (pod cleanup attempted) code — `route_exists`, `routing_cli_missing`,
`does_not_fit`, `ttl_below_startup`, `unpriced`, `price_over_cap`, `create_failed`, `readiness_timeout`,
`container_restarting`, `pod_gone`, `route_attach_failed`, `state_write_failed`, `interrupted` — is documented with its meaning and fix in
[Refusals and failures you might see](personal-serving.md#refusals-and-failures-you-might-see).
Two things that table doesn't say:

- **`route_attach_failed` never shows you the underlying reason.** The
  routing CLI's actual error text (for example, the reason it rejected the
  route) is captured internally but not printed — `pitwall serve` only prints
  the code and the pod ID. A CLI missing from `PATH` is refused before launch
  as `routing_cli_missing` instead. To see the
  real error, rerun the equivalent command yourself, for example:

  ```bash
  pitwall agents profiles add <route> --base-url <endpoint> \
    --model <served-model-id> --api-key-env PITWALL_ENDPOINT_KEY --seat local
  ```

  or confirm `pitwall` resolves at all with `which pitwall`.

- **`state_write_failed` and a timing-related `readiness_failed`** (a
  variant of `readiness_timeout` caused by an unexpected exception rather
  than a clean timeout) don't always reach you as a clean `refused:`/`failed
  after launch:` line — depending on exactly where they occur, Pitwall
  attempts to terminate the pod and records the failure first, then re-raises the
  original Python exception instead of wrapping it, so you may see a raw
  traceback. Run `pitwall status` afterward, check the `failure` field for the
  recorded code, and confirm the provider-side pod state.

**If `pitwall serve` is interrupted (Ctrl-C):** it prints "interrupted;
cleanup was attempted for any pod created" and exits 130. Confirm with
`pitwall status` or the RunPod console.

**Pod cleanup itself is silent on failure.** If terminating the pod after a
refusal/failure/interrupt fails (for example, a transient RunPod API error),
Pitwall does not report that failure — it just leaves the lease recorded as
`failed`/`stopped` locally while the pod may still be running. Don't take a
clean-looking error message as proof the pod is gone; when in doubt, check
[Orphaned pods](#orphaned-pods-a-pod-with-no-working-owner) below.

## Orphaned pods (a pod with no working owner)

RunPod has no server-side TTL of its own — a pod only stops because
something tells it to. Personal serving builds in two independent
mechanisms for this, described in
[How the deadline is enforced (cost safety)](personal-serving.md#how-the-deadline-is-enforced-cost-safety):
a self-destruct timer baked into the pod's own start command (runs inside
the pod, so it fires even if your machine is gone), plus a backstop check
every `pitwall status`/`stop` call performs. A pod only becomes a true
orphan if **both** miss it — for example, the in-pod timer's own `curl`
call fails (network blip, RunPod outage) *and* nobody runs `pitwall status`
again afterward to trigger the backstop, or your machine is gone entirely
and took `leases.json` with it.

**If you still have a local lease record** (`pitwall status` shows it, in
any state), you don't need anything below — just:

```bash
pitwall stop <route>      # or
pitwall stop --all
```

**If there is no local record** (fresh machine, deleted state directory, or
a pod launched from a machine that's gone for good), find and remove it
directly through RunPod:

```bash
pitwall runpod pods list                  # or: pitwall runpod pods list --json
```

Pods `pitwall serve` creates are named `pitwall-<route>`, which is how you
recognize yours among everything else on the account. Once you have its pod
ID:

```bash
pitwall terminate-pod --pod-id <pod-id>
```

This is the recommended way to remove it: it verifies the pod actually
reaches `EXITED`/`TERMINATED` afterward (`--verify-timeout-s` to change the
wait, `--no-verify` to skip it, `--json` for machine-readable output). The
raw equivalent, if you need the guarded-mutation audit trail instead, is:

```bash
pitwall runpod pods terminate <pod-id> --idempotency-key <key> --confirm <pod-id>
```

(`--dry-run` previews it first; `--confirm` must repeat the pod ID exactly.)

The orphan-recovery commands above use `RUNPOD_API_KEY` when it is set and
otherwise read `runpodctl`'s saved key from `~/.runpod/config.toml`; you do not
need to export the saved key after `runpodctl doctor`. This fallback is not a
blanket guarantee for unrelated commands: if another command explicitly
requires `RUNPOD_API_KEY` in the environment, export it for that command.

When in doubt, the RunPod console itself is always an authoritative
belt-and-suspenders check.

## A route points at a dead pod

**Symptom:** `pitwall serve --route <name> ...` refuses with `route_exists`,
but `pitwall status` shows nothing running for `<name>` — or a previously
working route now fails every request.

**Cause:** routes live in the `[agents.profiles]` tables of `pitwall.toml`,
separate from Pitwall's `leases.json`. `serve`'s
`route_exists` check looks at both. A route can survive in the routing
CLI's records after its pod is gone — a crash before `pitwall stop` ran, or
the pod's deadline passed and the route was never explicitly removed.
Because there's no local Pitwall lease for it, `pitwall stop <name>` won't
help (it only knows about leases it recorded itself).

**Fix:** use the routing CLI directly:

```bash
pitwall agents profiles show <name>     # see what it's pointing at
pitwall agents profiles probe <name>    # confirm it's actually dead
pitwall agents profiles remove <name>   # remove it
```

Then retry `pitwall serve --route <name> ...`. `pitwall agents profiles
list` shows every profile currently registered if you need to find the name.

## Registry-backed `serve`/`warm-volume` exits 2 needing `DATABASE_URL`

**Symptom:** `pitwall warm-volume ...` exits 2 with the message
`warm-volume needs DATABASE_URL (registry-backed launch planning)` (with `--json`:
`{"error": "missing_database_url", "detail": "warm-volume needs DATABASE_URL
(registry-backed launch planning)"}`); or a `pitwall serve` invocation using
registry-only flags (`--capability`, `--dry-run`, `--image`, and similar)
fails with `unrecognized arguments` or the analogous message `serve needs
DATABASE_URL (registry-backed dry run); run \`pitwall init\` or export
DATABASE_URL`.

**Cause:** `pitwall serve`/`status`/`stop` pick their backend from
`[personal] backend` in `pitwall.toml`, not from `DATABASE_URL`. Registry-only
flags exist only on the registry-backed parser, which is only reachable once
`backend = "registry"` is set; that registry code path also independently
refuses with the message above (exit 2, no database pool ever opened) if it's
ever entered without a `DATABASE_URL` — the same check `pitwall warm-volume`
performs up front, which you can reproduce directly by running it without
`DATABASE_URL` set.

**Fix:** add `[personal]` with `backend = "registry"` to `pitwall.toml`, and export
`DATABASE_URL=postgresql://...` (or run `pitwall init` for guided local
onboarding), before using any registry-only flag. See
[With a database configured](serve-quickstart.md#with-a-database-configured)
for the full registry-backed flow, including catalogue-only `--plan-only`
(which needs neither `--capability` nor `DATABASE_URL`).

## Removing local state and uninstalling

Nothing below touches your RunPod account or credential — Pitwall never
writes `RUNPOD_API_KEY` anywhere itself, and none of this cancels a RunPod
resource on its own. Do the pod/lease cleanup above first.

1. **Stop everything and confirm.**

   ```bash
   pitwall stop --all
   pitwall status   # should print "nothing running"
   ```

2. **Check for orphans on the RunPod account** with `pitwall runpod pods
   list` (see [Orphaned pods](#orphaned-pods-a-pod-with-no-working-owner)
   above) — Pitwall's own pods are named `pitwall-<route>`.

3. **Remove local state.** Everything Pitwall keeps on disk —
   `leases.json` and the generated `endpoint.key` — lives under
   `$XDG_STATE_HOME/pitwall` (default `~/.local/state/pitwall`); see
   [Where personal state lives on disk](personal-serving.md#where-personal-state-lives-on-disk)
   for exactly what's in it. Once step 1 confirms nothing is running, it's
   safe to remove:

   ```bash
   rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/pitwall"
   ```

4. **Remove the shell-profile line, if `pitwall setup` added one.** Look for
   the `# pitwall endpoint key` comment in whichever profile it edited
   (`~/.bashrc`, `~/.zshrc`, or `~/.config/fish/config.fish` — see
   [What `pitwall setup` changes on your machine](personal-serving.md#what-pitwall-setup-changes-on-your-machine))
   and delete that comment and the `export PITWALL_ENDPOINT_KEY=...` line
   below it.

5. **Remove any routes left behind.** `pitwall stop`/`--all` already removes
   the routes it knows about; for anything else (see
   [A route points at a dead pod](#a-route-points-at-a-dead-pod) above):

   ```bash
   pitwall agents profiles list
   pitwall agents profiles remove <name>
   ```

6. **Uninstall the routing plugin and shims** — the Claude Code
   plugin, shell shims, marketplace entries, and the channel MCP
   registration. Run `pitwall agents uninstall`; it reads the install
   manifest and removes exactly what `pitwall agents install` wrote,
   deliberately preserving everything else (profiles, run history,
   harness configuration). To remove Pitwall itself, run
   `uv tool uninstall pitwall`.
