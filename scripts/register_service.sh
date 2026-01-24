#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <service-name> <command...>"
  echo "Example: $0 ocr-qa \"python /home/tylor/cor/agents/ocr-qa/auto_worklog.py --daemon\""
  exit 1
fi

service_name="$1"
shift
command="$*"

log_dir="/home/tylor/logs"
log_file="${log_dir}/${service_name}.log"
unit_file="/etc/systemd/system/${service_name}.service"

sudo mkdir -p "${log_dir}"
sudo chown tylor:tylor "${log_dir}"

sudo tee "${unit_file}" >/dev/null <<EOF
[Unit]
Description=Managed service: ${service_name}
After=network.target

[Service]
Type=simple
User=tylor
WorkingDirectory=/home/tylor
ExecStart=/bin/bash -lc '${command}'
Restart=always
RestartSec=5
StandardOutput=append:${log_file}
StandardError=append:${log_file}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${service_name}.service"

echo "Started ${service_name}.service"
echo "Logs: ${log_file}"
echo "Stop: sudo systemctl stop ${service_name}.service"
echo "Disable: sudo systemctl disable ${service_name}.service"
