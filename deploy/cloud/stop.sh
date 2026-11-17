#!/usr/bin/env bash
# 只停止本目录脚本记录过、且命令行属于本安装的 main.py。
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
PID_FILE="$ROOT/data/cloud-run.pid"
if [ ! -f "$PID_FILE" ]; then
  echo "没有由 deploy/cloud 启动的常驻进程。start.sh 在调度关闭时不会驻留。"
  exit 0
fi
pid="$(tr -d '[:space:]' < "$PID_FILE")"
if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "pid 文件已过期，进程不在。"
  exit 0
fi
cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
case "$cmd" in
  *"$ROOT"*"main.py"*)
    kill "$pid"
    rm -f "$PID_FILE"
    echo "已停止 PID $pid"
    ;;
  *)
    echo "PID $pid 的命令行不属于本安装的 main.py，未发送信号。"
    exit 1
    ;;
esac
