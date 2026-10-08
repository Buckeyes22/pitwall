# MCP and agent clients

## In one sentence

MCP (Model Context Protocol) lets an AI agent call Pitwall's tools directly, and Pitwall's MCP
server runs only over local stdio.

## Why it matters when testing Pitwall

AI agents are a real kind of Pitwall user (J10), and a network MCP transport must be refused
(J11).

## Try it

```bash
uv run pitwall mcp serve --help
```

Expected: a usage message; nothing starts.

## Common confusions

- Stdio means the agent starts the server as a child process.
- MCP tools go through the same gates as the API.
- Network MCP is unsupported on purpose.
- Pitwall speaks both MCP eras: newer clients send the protocol version on every request (2026-07-28), and older ones start with an `initialize` handshake. Both work.

## Check yourself

1. Why is network MCP refused?

<details><summary>Answer</summary>

It would expose the tools without authentication.

</details>

## Go deeper

- [MCP server](../../docs/sdlc/03-mcp-server.md)
