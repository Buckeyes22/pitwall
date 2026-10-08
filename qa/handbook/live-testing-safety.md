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
  [what `pitwall setup` changes on your machine](../../docs/operator/personal-serving.md#what-pitwall-setup-changes-on-your-machine)
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
   [orphaned pods](../../docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner).

## Surprise charges

A charge you did not expect is severity 1. Stop. Do not delete things blindly. Write down what is
running and what the console shows, file the issue with the tester, and message the maintainer
directly.
