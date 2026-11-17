#!/usr/bin/env bash
# 按需单次运行。采集关闭或登录标记不足时只写 JSON 并退出，不访问机票网站。
# 不添加 cron，不发送通知。
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source "$(dirname "$0")/env.sh"
PY="./.venv/bin/python"
if [ ! -x "$PY" ]; then PY="${PYTHON:-python3}"; fi
CFG="${1:-config.yaml}"
exec "$PY" -m core.cloud_adapt once -c "$CFG"
