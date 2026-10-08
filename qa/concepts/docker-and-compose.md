# Docker and Docker Compose

## In one sentence

Docker runs programs in containers, and Docker Compose starts a set of containers from one file.

## Why it matters when testing Pitwall

The local test database and Redis run in containers from `docker-compose.testinfra.yml`. Start
them with
`docker compose -f docker-compose.testinfra.yml up -d --wait` and stop them with `make down`.
Only one test stack runs at a time ([rule R13](../coach/rules.md#safety)).

## Try it

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
```

Expected: the running containers with their ports, including `5444` and `6380` when the test
stack is up. An empty list means the stack is not running.

## Common confusions

- An image is the recipe. A container is a running copy. You can run many containers from the
  same image, but only one test stack here.
- `make down` removes the test containers but keeps their data: Postgres and Redis store it in
  named Docker volumes. `docker compose -f docker-compose.testinfra.yml down -v` also deletes the
  volumes. That is fine because it is test data; `db migrate` and `init` rebuild it.
- `--wait` blocks until the database and Redis are healthy. Without it, the next command may
  race the database startup.
- `docker compose` is the modern subcommand. The older `docker-compose` (with a hyphen) is a
  separate, legacy binary.

## Check yourself

1. Why does starting the stack in a second clone fail while the first clone's stack runs?

<details><summary>Answer</summary>

Both need ports 5444 and 6380. The second stack cannot bind them, so it fails. Stop the first
stack with `make down`, or work in only one clone at a time.

</details>

## Go deeper

- [Testing strategy — local infrastructure](../../docs/sdlc/17-testing-strategy.md#3-local-infrastructure)
