# Personal serving (no database required)

This is the default way to try Pitwall: no Postgres, no Redis, no
`DATABASE_URL`. Run `pitwall setup` once, then `pitwall serve`, and a RunPod
pod with a self-termination safeguard appears as a named route your Claude
Code plugin can dispatch to. This document covers that path end to end — what `setup`
changes on your machine, the exact `serve` invocation, where state lives,
how cost is bounded, and how to check on or tear down what is running.

If `pitwall.toml` sets `[personal] backend = "registry"`, `pitwall serve`/`status`/`stop`
behave differently — see [When to use the registry backend instead](#when-to-use-the-registry-backend-instead)
below. `DATABASE_URL` alone does not switch backends. For failures, crash recovery, and manual cleanup beyond what this
document covers, see [Troubleshooting and cleanup](troubleshooting.md).

## What `pitwall setup` changes on your machine

Run it once, interactively:

```console
pitwall setup
```

or non-interactively (answers "yes" to every prompt):

```console
pitwall setup --yes
```

In order, it:

1. **Resolves a RunPod credential.** It checks `RUNPOD_API_KEY` in your
   environment first, then `~/.runpod/config.toml` (the file `runpodctl`
   itself writes when you sign in). If neither exists and `runpodctl` is
   installed, it offers to run `runpodctl doctor` to sign you in and then
   re-checks. Pitwall never writes your RunPod key anywhere itself.
2. **Offers to tighten `~/.runpod/config.toml`'s permissions.** If that file
   is readable by other users on the machine, it offers to `chmod 600` it.
3. **Creates a local endpoint key.** A random per-machine secret is written
   to `endpoint.key` in Pitwall's state directory (below), mode `0600`. This
   is a Pitwall-generated secret, unrelated to your RunPod credential — it
   authenticates your routing CLI to the pods Pitwall launches for you.
4. **Offers to add that key to your shell profile.** If it's not already
   exported, it offers to append this to `~/.bashrc` (`~/.zshrc` for zsh,
   `~/.config/fish/config.fish` for fish, chosen from your `$SHELL`):

   ```bash
   # pitwall endpoint key
   export PITWALL_ENDPOINT_KEY="$(cat /path/to/endpoint.key)"
   ```

   Look for the `# pitwall endpoint key` comment to find or remove this
   later. If you decline, it prints the `export` line for you to run by hand
   in shells that talk to Claude Code. Either way, **open a new shell (or
   source the profile) before your first `serve` and route dispatch**, or the
   route it registers won't authenticate from your current shell — see the
   status warning below.
5. **Checks for the routing command.** If `pitwall agents` (or the command in
   `PITWALL_ROUTING_CLI`) isn't on `PATH`, it tells you to install Pitwall
   (install the release wheel, see the [install steps](../../README.md#install-pitwall)) and run `pitwall agents install` so `--route`
   has something to attach to.

It also prints which backend is active (`personal` or `registry`, from
`[personal] backend` in `pitwall.toml`; `personal` when the file or key is absent) so
you can confirm you're on the path this document describes. `pitwall status` prints
the same line.

`pitwall serve` will run this same interactive setup for you automatically
the first time, if no RunPod credential is found and it's running in a
terminal — but running `pitwall setup` first is the more predictable order.

## Serving a model

```console
pitwall serve --model ornith-ai/Ornith-1.5-35B-A3B-GGUF \
  --gpu-class "NVIDIA GeForce RTX 3090" \
  --ttl-minutes 45 --max-usd-per-hour 1.00 --route ornith
```

| Flag | Meaning |
| --- | --- |
| `--model` (required) | Catalogue model ID (`org/model`). |
| `--variant` | Catalogue variant ID; defaults to the model's published variant. |
| `--gpu-class` (required) | Canonical RunPod GPU name. |
| `--gpu-count` | GPUs to request; default `1`. |
| `--cloud` | `secure` or `community`; default `community`. |
| `--ttl-minutes` (alias `--ttl`) | Requested lease lifetime in minutes; default `60`, must be between `5` and `10080` (7 days). It schedules the best-effort cleanup safeguards below; RunPod does not enforce it as a server-side hard ceiling. |
| `--max-usd-per-hour` (required) | Refuses the launch before any pod is created if the GPU's live hourly price exceeds this. |
| `--rate-per-second` | Sets the hourly price yourself (USD/second × 3600), overriding the live price lookup entirely. Required if pricing is unavailable (`unpriced`, see below). |
| `--route` (required) | Agent profile name to register with `pitwall agents` on success. |
| `--json` | Machine-readable output. |

`--max-usd-per-hour` has no default; omitting it exits with an error before
anything is launched.

Before creating a pod, `serve` checks — and refuses for free, with no pod
ever created — six things: the route name isn't already in use
(`route_exists`), the routing command (`pitwall agents`) is on `PATH`
(`routing_cli_missing`), the model fits on the requested GPU (`does_not_fit`),
the TTL outlasts the model's startup budget (`ttl_below_startup`), a price could
be determined (`unpriced`, fixable with `--rate-per-second`), and that price is at
or under your cap (`price_over_cap`). Only after all six
pass does it create the pod, wait for the model to answer
(`GET <endpoint>/v1/models`), attach the route, and probe it. If the pod
never becomes ready in time (`readiness_timeout`) or the route can't be
attached or probed (`route_attach_failed`), Pitwall attempts to terminate the
pod it just created before returning an error. Interrupting `serve` (Ctrl-C)
also attempts that cleanup. Provider/API failures during cleanup are suppressed,
so verify the pod in RunPod when cost certainty matters.

On success:

```text
route ornith is ready: <served-model-id> at https://<pod-id>-8000.proxy.runpod.net/v1
deadline <ISO-8601 timestamp> (self-termination scheduled)
try it: route-shim.sh ornith prompt.md
```

## Where personal state lives on disk

Everything lives under `$XDG_STATE_HOME/pitwall`, or `~/.local/state/pitwall`
if `XDG_STATE_HOME` is unset. The directory is created (and re-tightened on
every write) at mode `0700`; every file in it is `0600`:

- **`leases.json`** — every route you've ever served, written atomically
  (temp file + rename) so a crash mid-write can't corrupt it. Each entry
  records the route name, RunPod pod ID, requested model, the model ID it
  actually serves, engine, image, GPU class/count, cloud, price, endpoint
  URL, when it launched, when its deadline is, and its state:

  ```json
  {
    "route": "ornith",
    "pod_id": "<pod-id>",
    "model": "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
    "served_model_id": "ornith-1.5-35b",
    "engine": "llama.cpp",
    "gpu_class": "NVIDIA GeForce RTX 3090",
    "gpu_count": 1,
    "cloud": "community",
    "price_per_hour_usd": "0.220000",
    "endpoint_url": "https://<pod-id>-8000.proxy.runpod.net/v1",
    "key_env": "PITWALL_ENDPOINT_KEY",
    "launched_at": "...",
    "deadline_at": "...",
    "state": "ready",
    "failure": null,
    "accrued_usd": null
  }
  ```

  `accrued_usd` is filled in once, when the lease ends (stop, failure, a pod
  found gone, or the deadline backstop), with what it cost at its recorded
  price; that is when its cost moves into the ledger.

- **`ledger.json`** — settled spend per month (by launch month), for example
  `{"months": {"2026-09": "3.410000"}}`. `serve` adds the maximum spend of
  leases still running to this total when checking the monthly budget.

- **`audit.jsonl`** — one JSON object per line for every serve, budget
  refusal, stop, and failure: `ts`, `action` (`serve`, `refused`, `stop`,
  `failed`, `gone`), `route`, `pod_id`, `max_spend_usd` (serve and refusals)
  or `accrued_usd` (when a lease ends), and `reason`.

- **`endpoint.key`** — the random secret `pitwall setup` generated (see
  above); its raw contents are the value your shell profile exports as
  `PITWALL_ENDPOINT_KEY`.

Nothing here is required once every lease is stopped: it's safe to
`rm -rf ~/.local/state/pitwall` (or your `$XDG_STATE_HOME/pitwall`) when
`pitwall status` shows nothing running. Doing so with something still
running just orphans the pod's local record — it does not stop the pod;
use `pitwall stop` first.

## How the deadline is enforced (cost safety)

RunPod has no server-side TTL: nothing on RunPod's side guarantees a pod will
stop at a requested time. Pitwall compensates with **two independent best-effort
mechanisms**, deliberately redundant:

1. **A self-destruct timer baked into the pod's own start command.** The
   command Pitwall gives RunPod to run is, in effect:

   ```sh
   ( sleep <ttl-seconds>; echo 'pitwall-deadline: ttl reached' >&2; \
     curl -fsS -X POST -H "Authorization: Bearer $RUNPOD_API_KEY" \
       -H "Content-Type: application/json" -d '{"action":"terminate"}' \
       "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID/action" >&2 \
     || echo 'pitwall-deadline: lifecycle call failed' >&2 ) &
   exec <model-server-and-args> --api-key "$PITWALL_ENDPOINT_KEY"
   ```

   `$RUNPOD_API_KEY` and `$RUNPOD_POD_ID` are not put there by Pitwall — RunPod
   populates both automatically inside every pod's own environment, so the pod
   can identify and terminate itself. The timer's outcome goes to the
   container's log, which the Pods screen of `pitwall dashboard` (`d`) shows when you select the route. A live run on 2026-09-23 saw the
   pod terminate itself at its 35-minute deadline with no Pitwall call
   ([evidence](../evidence/2026-09-23-personal-serve-live.md)). This timer runs **inside the pod**, so it
   attempts cleanup even if your laptop is off, asleep, or the `pitwall` process
   that launched it is long gone. It is the primary safeguard for a forgotten
   pod, but its request can still fail. The model server's own `--api-key`
   value (your `PITWALL_ENDPOINT_KEY`) is substituted from the pod's
   environment at runtime — it never appears as literal text in the launch
   command.
2. **A backstop check on every `status`/`stop` call.** `pitwall status`
   independently checks every lease's deadline against the current time; if
   one has passed and the pod is still recorded as running, Pitwall
   requests termination there too. When that request succeeds, it removes the
   route and records `state: "stopped"`, `failure: "terminated_late"`. If the
   request raises, the lease remains `launching`/`ready` so a later `status` or
   `stop --all` can retry. This exists in case the in-pod timer's own `curl`
   call ever fails (a proxy outage, a `sleep` that never wakes, etc.).

Together, these mechanisms reduce the chance of a forgotten pod running past
its deadline, but they are not a server-side TTL guarantee. A pod can continue
billing if both cleanup requests fail; check the RunPod console and use the
[orphan cleanup procedure](troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner)
when termination must be confirmed.

## Checking status: `pitwall status`

```console
pitwall status
```

```text
ornith               ready      ornith-1.5-35b               NVIDIA GeForce RTX 3090      ends 2026-09-03T23:45:00+00:00
```

(`--json` gives the full lease records shown above.) Lease `state` values:

| State | Meaning |
| --- | --- |
| `launching` | Pod created, not yet verified ready. |
| `ready` | Verified serving and route attached; usable now. |
| `stopped` | Pitwall attempted termination — by your command or the deadline backstop above. This local state is not independent provider-side confirmation. |
| `failed` | Refused to complete; see `failure` for the code (below). |
| `gone` | The RunPod pod no longer exists (deleted outside Pitwall). |

If `PITWALL_ENDPOINT_KEY` isn't set in the shell you're running `status`
from, it prints a warning: *"note: PITWALL_ENDPOINT_KEY is not set; routes
will not authenticate until it is."* This means the route was registered
correctly, but `pitwall agents`/`route-shim.sh` only knows the
*name* of the environment variable that holds the key (`apiKeyEnv`), never
the value — each dispatch reads the value live from its own process
environment. If your current shell never picked up the export
`pitwall setup` added to your profile (new terminal not opened, or you
declined the edit), that dispatch has no key to send and the pod rejects it.
Open a new shell, or run the `export PITWALL_ENDPOINT_KEY=...` line
`pitwall setup` printed, to fix it.

## Stopping

```console
pitwall stop ornith        # stop one route
pitwall stop --all         # stop every launching/ready route
```

Either form requests pod termination, removes the profile from
`pitwall agents`, and marks the lease `stopped` in `leases.json`. Cleanup
errors are suppressed, so confirm in RunPod if the provider result matters. If
you're not sure what's running, `pitwall status` first is always safe — it
never launches or spends anything.

## Refusals and failures you might see

Codes reported by `serve` (as `refused: <code> ...` before any pod is
created, or `failed after launch: <code>; termination attempted for pod <id>`
after one was) are listed below. A suppressed provider/API error can leave the
pod running. When the model never became ready (`readiness_timeout`,
`container_restarting`, `pod_gone`), `serve` also prints the pod's last 40 log
lines, redacted, under `pod log tail:`, when RunPod still returns them. They are
read before the pod is terminated; afterwards they are gone.

| Code | Meaning |
| --- | --- |
| `budget_not_configured` | `PITWALL_MONTHLY_BUDGET_USD` is not set. Export a monthly budget in USD, then retry. |
| `per_request_cap` | This lease's maximum spend (hourly price times `--ttl-minutes`) exceeds `PITWALL_PER_REQUEST_MAX_USD` (default 10). Shorten the TTL or raise the cap. |
| `monthly_budget` | This month's settled spend, plus the maximum spend of leases still running, plus this lease would exceed `PITWALL_MONTHLY_BUDGET_USD`. Stop a lease or raise the budget. |
| `route_exists` | That route name is already in use (or already launching/ready). Choose another `--route` or stop the existing one first. |
| `routing_cli_missing` | `pitwall agents` (or the `PITWALL_ROUTING_CLI` override) isn't on `PATH`, so the route could never attach. Install the release wheel (see the [install steps](../../README.md#install-pitwall)) and run `pitwall agents install`, then retry. |
| `does_not_fit` | The model doesn't fit on the requested `--gpu-class`. |
| `ttl_below_startup` | `--ttl-minutes` is at or within the model's startup budget (its dossier's `startup_min`), so the in-pod deadline would end the pod before the model answers. Choose a longer TTL. |
| `unpriced` | No live price for that GPU/cloud combination; pass `--rate-per-second`. |
| `price_over_cap` | The live (or `--rate-per-second`) price exceeds `--max-usd-per-hour`. |
| `create_failed` | RunPod refused or failed the pod create (no capacity, or a RunPod-side error). Nothing was recorded; check `pitwall runpod pods list` in case RunPod created one anyway, then retry. |
| `readiness_timeout` | The pod launched but the model never answered in time. Pitwall attempted to terminate it. |
| `container_restarting` | The pod's container kept restarting before the model answered (a crash loop: RunPod keeps such a pod `RUNNING`). Pitwall attempted to terminate it. |
| `pod_gone` | The pod disappeared before the model answered (RunPod preempted or removed it). |
| `route_attach_failed` | The pod is healthy, but `pitwall agents profiles add`/`probe` failed (it rejected the route, or could not be run). Pitwall attempted to terminate it. |
| `state_write_failed` | Local state couldn't be written to disk. Pitwall attempted to terminate the pod. |
| `interrupted` | You pressed Ctrl-C (or the task was cancelled) after the pod existed. Pitwall attempted to terminate it and recorded the lease `failed`. |
| `terminated_late` | (Seen in `pitwall status`, not `serve`.) The deadline backstop caught a pod past its TTL — see above. |

For anything not covered here — an interrupted `serve`, an orphaned pod with
no local lease record, or removing everything `pitwall setup` changed — see
[Troubleshooting and cleanup](troubleshooting.md).

## How the served model reaches Claude Code

A successful `serve` runs the equivalent of:

```console
pitwall agents profiles add ornith \
  --base-url https://<pod-id>-8000.proxy.runpod.net/v1 \
  --model ornith-1.5-35b --api-key-env PITWALL_ENDPOINT_KEY --seat local
```

This writes an entry into the `[agents.profiles]` tables of `pitwall.toml` (the same
profiles the `pitwall`/`pitwall-codex`/`pitwall-copilot` plugins read); `--seat local`
just tags it for the routing skill as a local endpoint. From there:

- **From Claude Code**, ask in natural language to route work to it, or use
  the printed shortcut directly: `route-shim.sh ornith prompt.md`. An
  endpoint route like this dispatches through its default harness (Qwen
  Code) unless you `pitwall agents profiles sync` it onto a different one.
- **From a shell**, `route-shim.sh ornith prompt.md` works the same way
  outside Claude Code.

Route mechanics (harnesses, `--seat`, syncing to a specific CLI) are
documented in the agent-routing component's
[route profiles guide](../../docs/agents/routes.md); this is
a distinct, simpler mechanism than the registry backend's `--from-pitwall`
capability handoff described in its
[Pitwall handoff guide](../../docs/agents/pitwall.md) —
personal serving talks straight to the pod's own RunPod proxy URL, with no
Pitwall API in between.

## Cost safety, summarized

- **A price cap you set** (`--max-usd-per-hour`) is checked before any pod
  exists; the launch is refused, not billed, if it's exceeded.
- **A monthly budget** (`PITWALL_MONTHLY_BUDGET_USD`, required) and a
  per-lease cap (`PITWALL_PER_REQUEST_MAX_USD`) are checked before any pod
  exists, counting each running lease at its maximum spend until it ends.
- **A requested deadline** (`--ttl-minutes`, 5 to 10080) schedules an in-pod
  cleanup attempt independent of whether your machine stays online; it is not
  a provider-enforced maximum runtime.
- **A backstop** on every `status`/`stop` call retries cleanup that the in-pod
  timer may have missed.
- **`pitwall stop --all`** is always available as a manual kill-all, and
  `pitwall status` is always safe to run to see what's live.

If your machine goes offline entirely, the in-pod timer still attempts cleanup
without depending on the Pitwall CLI, your shell, or your machine being
reachable. Confirm provider state when an overrun would be unacceptable.

## When to use the registry backend instead

Personal serving is single-machine and file-based: state lives in one
`leases.json` on one machine, and there's no way for a second host or a
teammate to see or manage what's running. Choose the registry backend when you
need shared, multi-host lease state, the API/MCP/TUI surfaces operating on the
same leases, budget and routing automation, or capability-based reuse across
serves: set `DATABASE_URL` (pointing at a Postgres/Redis-backed Pitwall
deployment) and add this to `pitwall.toml` (the file named by `PITWALL_CONFIG_FILE`,
else `./pitwall.toml`):

```toml
[personal]
backend = "registry"
```

`backend` accepts `personal` (the default) or `registry`; any other value is a
configuration error. With the registry backend selected, `pitwall serve`/`status`/`stop`
are the same three verbs but delegate to the registry-backed lease commands instead — see
[Serve a model: operator quickstart](serve-quickstart.md) for that path in
full, including catalogue/fit previews, `--dry-run`/`--plan-only`, and the
REST/MCP equivalents.
