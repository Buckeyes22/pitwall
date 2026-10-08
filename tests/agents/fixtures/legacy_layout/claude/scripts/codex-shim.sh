#!/usr/bin/env bash
# codex-shim.sh - compatibility wrapper for the shared Python runtime.
exec "$HERE/pitwall-agent-routing" _shim codex "$@"
