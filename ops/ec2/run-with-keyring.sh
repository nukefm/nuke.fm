#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <command> [args...]" >&2
    exit 1
fi

if [ -z "${NUKEFM_KEYRING_PASSWORD:-}" ]; then
    echo "NUKEFM_KEYRING_PASSWORD is required in the environment." >&2
    exit 1
fi

for required_command in dbus-launch gnome-keyring-daemon secret-tool; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        echo "Missing required command: ${required_command}" >&2
        exit 1
    fi
done

cleanup() {
    if [ -n "${GNOME_KEYRING_PID:-}" ]; then
        kill "${GNOME_KEYRING_PID}" >/dev/null 2>&1 || true
    fi
    if [ -n "${DBUS_SESSION_BUS_PID:-}" ]; then
        kill "${DBUS_SESSION_BUS_PID}" >/dev/null 2>&1 || true
    fi
}

eval "$(dbus-launch --sh-syntax)"
trap cleanup EXIT
eval "$(printf '%s' "${NUKEFM_KEYRING_PASSWORD}" | gnome-keyring-daemon --unlock --components=secrets)"

exec "$@"
