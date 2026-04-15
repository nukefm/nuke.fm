#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <host> [user]" >&2
    exit 1
fi

host="$1"
user="${2:-ubuntu}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
remote="${user}@${host}"
remote_script="/tmp/nukefm-bootstrap-remote.sh"

for required_command in scp ssh; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        echo "Missing required command: ${required_command}" >&2
        exit 1
    fi
done

scp "${script_dir}/bootstrap-remote.sh" "${remote}:${remote_script}"
ssh "${remote}" "chmod +x ${remote_script} && sudo DEPLOY_USER='${user}' ${remote_script} && rm -f ${remote_script}"
