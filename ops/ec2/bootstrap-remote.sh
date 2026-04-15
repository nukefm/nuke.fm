#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "bootstrap-remote.sh must run as root." >&2
    exit 1
fi

deploy_user="${DEPLOY_USER:-ubuntu}"
deploy_home="$(getent passwd "${deploy_user}" | cut -d: -f6)"
app_root="/srv/nukefm"
git_dir="${app_root}/git/nuke.fm.git"
work_tree="${app_root}/current"
shared_dir="${app_root}/shared"
runtime_env="${shared_dir}/runtime.env"
service_name="nukefm.service"

if [ -z "${deploy_home}" ]; then
    echo "User '${deploy_user}' does not exist on the host." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl dbus-x11 git gnome-keyring libsecret-tools

install -d -o "${deploy_user}" -g "${deploy_user}" -m 755 "${app_root}" "${app_root}/git" "${work_tree}" "${shared_dir}"

if [ ! -d "${git_dir}" ]; then
    su - "${deploy_user}" -c "git init --bare --initial-branch=main '${git_dir}'"
fi

if [ ! -x "${deploy_home}/.local/bin/uv" ]; then
    su - "${deploy_user}" -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if [ ! -f "${runtime_env}" ]; then
    cat >"${runtime_env}" <<'EOF'
PORT=8000
NUKEFM_KEYRING_PASSWORD=
EOF
    chown "${deploy_user}:${deploy_user}" "${runtime_env}"
    chmod 600 "${runtime_env}"
fi

cat >/etc/systemd/system/${service_name} <<EOF
[Unit]
Description=nuke.fm FastAPI service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${deploy_user}
Group=${deploy_user}
WorkingDirectory=${work_tree}
Environment=HOME=${deploy_home}
Environment=PATH=${deploy_home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=${runtime_env}
ExecStart=${work_tree}/ops/ec2/run-service.sh
Restart=always
RestartSec=5
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF

cat >"${git_dir}/hooks/post-receive" <<EOF
#!/usr/bin/env bash
set -euo pipefail

git_dir="${git_dir}"
work_tree="${work_tree}"
runtime_env="${runtime_env}"
deploy_home="${deploy_home}"

export HOME="\${deploy_home}"
export PATH="\${deploy_home}/.local/bin:/usr/local/bin:/usr/bin:/bin"

git --work-tree="\${work_tree}" --git-dir="\${git_dir}" checkout -f main
cd "\${work_tree}"
uv sync --frozen

if [ ! -f "\${work_tree}/.env" ]; then
    echo "Skipping restart until \${work_tree}/.env exists." >&2
    exit 0
fi

if ! grep -Eq '^NUKEFM_KEYRING_PASSWORD=.+$' "\${runtime_env}"; then
    echo "Skipping restart until \${runtime_env} contains NUKEFM_KEYRING_PASSWORD." >&2
    exit 0
fi

sudo systemctl restart ${service_name}
EOF
chmod 755 "${git_dir}/hooks/post-receive"
chown "${deploy_user}:${deploy_user}" "${git_dir}/hooks/post-receive"

cat >/etc/sudoers.d/nukefm-deploy <<EOF
${deploy_user} ALL=(root) NOPASSWD: /bin/systemctl restart ${service_name}, /bin/systemctl start ${service_name}, /bin/systemctl status ${service_name}
EOF
chmod 440 /etc/sudoers.d/nukefm-deploy
visudo -cf /etc/sudoers.d/nukefm-deploy

systemctl daemon-reload
systemctl enable ${service_name}
