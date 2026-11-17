#!/usr/bin/env bash
# 把当前 HEAD 打成父级可解包的源码包。不包含 .git、虚拟环境、本地配置和数据。
set -euo pipefail
cd "$(dirname "$0")/../.."
out="${1:-/opt/cursor/artifacts/ticket-monitoring-cloud-bundle.tar.gz}"
mkdir -p "$(dirname "$out")"
git archive --format=tar.gz --prefix=ticket-monitoring/ -o "$out" HEAD
sha256sum "$out" | tee "${out}.sha256"
echo "wrote $out"
