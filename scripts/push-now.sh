#!/bin/sh
set -eu
# Push current OpenCode Go quota to this TRMNL webhook plugin.
# Override TRMNL_WEBHOOK_URL to target a different plugin.
export TRMNL_WEBHOOK_URL="${TRMNL_WEBHOOK_URL:-https://trmnl.com/api/custom_plugins/4b640bba-bb39-4d09-968d-e750a17fcf3a}"
exec python3 "$(dirname "$0")/sync.py" "$@"
