#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <host> [user]" >&2
    exit 1
fi

host="$1"
user="${2:-ubuntu}"
remote="${user}@${host}"
remote_root="/srv/nukefm/current"
remote_runtime="/srv/nukefm/shared/runtime.env"
ssh_key="${NUKEFM_SSH_KEY:-}"
ssh_config_file="${NUKEFM_SSH_CONFIG_FILE:-}"
ssh_args=()

for required_command in scp secret-tool ssh; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        echo "Missing required command: ${required_command}" >&2
        exit 1
    fi
done

if [ ! -f ".env" ]; then
    echo "Missing local .env file." >&2
    exit 1
fi

if [ -n "${ssh_key}" ]; then
    ssh_args=(-i "${ssh_key}")
fi

if [ -n "${ssh_config_file}" ]; then
    ssh_args=(-F "${ssh_config_file}" "${ssh_args[@]}")
fi

ssh "${ssh_args[@]}" "${remote}" "install -d '${remote_root}' '${remote_root}/data'"
scp "${ssh_args[@]}" .env "${remote}:${remote_root}/.env"

if [ -f "data/nukefm.sqlite3" ]; then
    scp "${ssh_args[@]}" data/nukefm.sqlite3 "${remote}:${remote_root}/data/nukefm.sqlite3"
fi

copy_secret() {
    local secret_name="$1"
    local secret_value

    if ! secret_value="$(secret-tool lookup service nuke.fm name "${secret_name}")"; then
        echo "Failed to read local secret-tool entry for '${secret_name}'." >&2
        exit 1
    fi

    if [ -z "${secret_value}" ]; then
        echo "Local secret-tool entry for '${secret_name}' was empty." >&2
        exit 1
    fi

    printf '%s' "${secret_value}" | ssh "${ssh_args[@]}" "${remote}" "bash -lc 'source ${remote_runtime} && ${remote_root}/ops/ec2/store-secret.sh ${secret_name}'"
}

copy_secret deposit-master-seed
copy_secret treasury-seed

ssh "${ssh_args[@]}" "${remote}" "sudo systemctl restart nukefm.service && sudo systemctl status --no-pager nukefm.service"
