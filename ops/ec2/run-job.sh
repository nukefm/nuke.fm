#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "run-job.sh requires a nukefm command." >&2
    exit 1
fi

if [ -z "${NUKEFM_KEYRING_PASSWORD:-}" ]; then
    echo "NUKEFM_KEYRING_PASSWORD is required in the service environment." >&2
    exit 1
fi

exec "$(dirname "${BASH_SOURCE[0]}")/run-with-keyring.sh" \
    uv run --env-file .env python -m nukefm "$@"
