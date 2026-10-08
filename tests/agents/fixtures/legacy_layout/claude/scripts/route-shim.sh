#!/usr/bin/env bash
# route-shim.sh - compatibility wrapper for the shared Python runtime.
exec "$HERE/pitwall-agent-routing" _shim route "$@"
