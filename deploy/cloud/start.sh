#!/usr/bin/env bash
# 确认调度已关闭并写 data/cloud-idle.json。不留下常驻进程。
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source "$(dirname "$0")/env.sh"
PY="./.venv/bin/python"
if [ ! -x "$PY" ]; then PY="${PYTHON:-python3}"; fi
CFG="${1:-config.yaml}"
"$PY" -m core.cloud_adapt idle -c "$CFG"
echo "没有启动 TicketMonitor，也没有添加 cron。"
