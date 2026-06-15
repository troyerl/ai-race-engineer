#!/usr/bin/env bash
# Delegate to scripts/build_mac.sh (run from project root).
exec "$(dirname "$0")/scripts/build_mac.sh" "$@"
