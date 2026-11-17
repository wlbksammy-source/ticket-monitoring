#!/usr/bin/env bash
# 写 data/cloud-health.json。不监听端口，不采集。
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source "$(dirname "$0")/env.sh"
PY="./.venv/bin/python"
if [ ! -x "$PY" ]; then PY="${PYTHON:-python3}"; fi
CFG="${1:-config.yaml}"
exec "$PY" -m core.cloud_adapt health -c "$CFG"
