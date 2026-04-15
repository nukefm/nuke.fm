#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <host> [user]" >&2
    exit 1
fi

host="$1"
user="${2:-ubuntu}"
remote_name="${NUKEFM_PRODUCTION_REMOTE:-production}"
remote_url="${user}@${host}:/srv/nukefm/git/nuke.fm.git"

if ! command -v git >/dev/null 2>&1; then
    echo "Missing required command: git" >&2
    exit 1
fi

if git remote get-url "${remote_name}" >/dev/null 2>&1; then
    existing_remote="$(git remote get-url "${remote_name}")"
    if [ "${existing_remote}" != "${remote_url}" ]; then
        echo "Remote '${remote_name}' already points to '${existing_remote}'." >&2
        exit 1
    fi
else
    git remote add "${remote_name}" "${remote_url}"
fi

git push "${remote_name}" HEAD:main
