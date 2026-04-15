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
ssh_key="${NUKEFM_SSH_KEY:-}"
ssh_config_file="${NUKEFM_SSH_CONFIG_FILE:-}"
git_ssh_command="ssh"

if ! command -v git >/dev/null 2>&1; then
    echo "Missing required command: git" >&2
    exit 1
fi

if [ -n "${ssh_config_file}" ]; then
    git_ssh_command="${git_ssh_command} -F ${ssh_config_file}"
fi

if [ -n "${ssh_key}" ]; then
    git_ssh_command="${git_ssh_command} -i ${ssh_key}"
fi

export GIT_SSH_COMMAND="${git_ssh_command}"

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
