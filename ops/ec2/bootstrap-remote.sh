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
refresh_service_name="nukefm-refresh.service"
refresh_timer_name="nukefm-refresh.timer"
market_snapshot_service_name="nukefm-market-snapshots.service"
market_snapshot_timer_name="nukefm-market-snapshots.timer"
chart_service_name="nukefm-market-charts.service"
chart_timer_name="nukefm-market-charts.timer"
seed_service_name="nukefm-seed-weekly.service"
seed_timer_name="nukefm-seed-weekly.timer"

if [ -z "${deploy_home}" ]; then
    echo "User '${deploy_user}' does not exist on the host." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates caddy curl dbus-x11 git gnome-keyring libsecret-tools

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

cat >/etc/caddy/Caddyfile <<'EOF'
nukefm.xyz {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
caddy validate --config /etc/caddy/Caddyfile

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

cat >/etc/systemd/system/${refresh_service_name} <<EOF
[Unit]
Description=Refresh nuke.fm Bags catalog and token metrics
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${deploy_user}
Group=${deploy_user}
WorkingDirectory=${work_tree}
Environment=HOME=${deploy_home}
Environment=PATH=${deploy_home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=${runtime_env}
ExecStart=${work_tree}/ops/ec2/run-job.sh ingest --limit 100
EOF

cat >/etc/systemd/system/${refresh_timer_name} <<EOF
[Unit]
Description=Run nuke.fm catalog and token metric refresh

[Timer]
OnBootSec=2m
OnUnitActiveSec=10m
Persistent=true
RandomizedDelaySec=60s
Unit=${refresh_service_name}

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/${market_snapshot_service_name} <<EOF
[Unit]
Description=Capture nuke.fm hourly 24h-median market snapshots
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${deploy_user}
Group=${deploy_user}
WorkingDirectory=${work_tree}
Environment=HOME=${deploy_home}
Environment=PATH=${deploy_home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=${runtime_env}
ExecStart=${work_tree}/ops/ec2/run-job.sh snapshot-markets
EOF

cat >/etc/systemd/system/${market_snapshot_timer_name} <<EOF
[Unit]
Description=Run nuke.fm hourly 24h-median market snapshots

[Timer]
OnBootSec=4m
OnUnitActiveSec=1h
Persistent=true
RandomizedDelaySec=2m
Unit=${market_snapshot_service_name}

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/${chart_service_name} <<EOF
[Unit]
Description=Capture nuke.fm 5-minute market chart snapshots
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${deploy_user}
Group=${deploy_user}
WorkingDirectory=${work_tree}
Environment=HOME=${deploy_home}
Environment=PATH=${deploy_home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=${runtime_env}
ExecStart=${work_tree}/ops/ec2/run-job.sh snapshot-market-charts
EOF

cat >/etc/systemd/system/${chart_timer_name} <<EOF
[Unit]
Description=Run nuke.fm 5-minute market chart snapshots

[Timer]
OnBootSec=3m
OnUnitActiveSec=5m
Persistent=true
RandomizedDelaySec=30s
Unit=${chart_service_name}

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/${seed_service_name} <<EOF
[Unit]
Description=Weekly nuke.fm top-market auto seed
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${deploy_user}
Group=${deploy_user}
WorkingDirectory=${work_tree}
Environment=HOME=${deploy_home}
Environment=PATH=${deploy_home}/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=${runtime_env}
ExecStart=${work_tree}/ops/ec2/run-job.sh ingest --limit 100
ExecStart=${work_tree}/ops/ec2/run-job.sh seed-weekly-liquidity --top 4 --amount-usdc 1
EOF

cat >/etc/systemd/system/${seed_timer_name} <<EOF
[Unit]
Description=Run weekly nuke.fm top-market auto seed

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=15m
Unit=${seed_service_name}

[Install]
WantedBy=timers.target
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
git --work-tree="\${work_tree}" --git-dir="\${git_dir}" submodule sync --recursive
git --work-tree="\${work_tree}" --git-dir="\${git_dir}" submodule update --init --recursive
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
${deploy_user} ALL=(root) NOPASSWD: /bin/systemctl restart ${service_name}, /bin/systemctl start ${service_name}, /bin/systemctl status ${service_name}, /bin/systemctl start ${refresh_timer_name}, /bin/systemctl start ${market_snapshot_timer_name}, /bin/systemctl start ${chart_timer_name}, /bin/systemctl start ${seed_timer_name}
EOF
chmod 440 /etc/sudoers.d/nukefm-deploy
visudo -cf /etc/sudoers.d/nukefm-deploy

systemctl daemon-reload
systemctl enable ${service_name}
systemctl enable ${refresh_timer_name}
systemctl enable ${market_snapshot_timer_name}
systemctl enable ${chart_timer_name}
systemctl enable ${seed_timer_name}
systemctl enable caddy
systemctl restart caddy
