# Processes, ports, and localhost

## In one sentence

A running program is a process; a server process listens on a port; and `127.0.0.1` (localhost)
means this machine only.

## Why it matters when testing Pitwall

The API listens on port 8080, the test database on 5444, and Redis on 6380. A busy port stops a
server from starting, and the journey harness refuses to run when its ports are busy. Loopback
(`127.0.0.1`) cannot be reached from other machines, by design, so a server that works on your
laptop does not work for a teammate until you give them a real address.

## Try it

```bash
ss -tln | grep -E ':(5444|6380|8080) '
```

Expected: one line per port that is in use now, and nothing otherwise.

## Common confusions

- Ctrl-C stops the process in that terminal.
- Closing a terminal can leave background processes running. The API or stack may still hold
  their ports even though the window is gone.
- `127.0.0.1` and `localhost` both mean this machine. Other people on the network cannot reach
  it, and that is a feature.
- A port can be busy because a previous run crashed. Stop the old process before you start a
  new one.

## Check yourself

1. The API says `address already in use`. What do you check?

<details><summary>Answer</summary>

Whether another API is still on 8080. Run `ss -tln`, find the process holding the port, and stop
it.

</details>

## Go deeper

- [Security and Trust Model](../../README.md#security-and-trust-model)
