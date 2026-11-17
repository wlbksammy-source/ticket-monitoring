#!/usr/bin/env bash
# 安装云端适配的运行环境。不启动常驻进程，不采集，不登录。
# Apache-2.0，见仓库 LICENSE。
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

PY_BOOT="${PYTHON:-python3}"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  "$PY_BOOT" -m venv "$ROOT/.venv"
fi
# 健康检查和拒绝采集只需要 PyYAML。完整采集依赖用 --full，且仍不下载浏览器。
"$ROOT/.venv/bin/pip" install --disable-pip-version-check PyYAML
if [ "${1:-}" = "--full" ]; then
  "$ROOT/.venv/bin/pip" install --disable-pip-version-check -r "$ROOT/requirements.txt"
  echo "已安装 requirements.txt。尚未安装 Playwright 浏览器内核。"
fi

mkdir -p "$ROOT/data" "$ROOT/logs" "$ROOT/user_data"
if [ ! -f "$ROOT/config.yaml" ]; then
  cp "$ROOT/deploy/cloud/config.safe.example.yaml" "$ROOT/config.yaml"
  echo "已写入 config.yaml（虚构安全模板）。请在本机修改，不要提交。"
else
  echo "保留已有 config.yaml，未覆盖。"
fi
echo "安装完成。未启动进程，未访问机票网站。"
echo "下一步: bash deploy/cloud/health.sh"
