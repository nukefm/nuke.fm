#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <secret-name>" >&2
    exit 1
fi

if [ -z "${NUKEFM_KEYRING_PASSWORD:-}" ]; then
    echo "NUKEFM_KEYRING_PASSWORD is required." >&2
    exit 1
fi

secret_name="$1"
secret_value="$(cat)"

if [ "${#secret_value}" -ne 64 ]; then
    echo "Expected a 64 character hex seed for '${secret_name}'." >&2
    exit 1
fi

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

secret-tool clear service nuke.fm name "${secret_name}" >/dev/null 2>&1 || true
printf '%s' "${secret_value}" | secret-tool store --label "nuke.fm ${secret_name}" service nuke.fm name "${secret_name}"
