#!/usr/bin/env bash
set -euo pipefail

if [ -z "${NUKEFM_KEYRING_PASSWORD:-}" ]; then
    echo "NUKEFM_KEYRING_PASSWORD is required in the service environment." >&2
    exit 1
fi

for required_command in uv; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        echo "Missing required command: ${required_command}" >&2
        exit 1
    fi
done

exec "$(dirname "${BASH_SOURCE[0]}")/run-with-keyring.sh" \
    uv run --env-file .env python -m nukefm serve --host 127.0.0.1 --port "${PORT:-8000}"
