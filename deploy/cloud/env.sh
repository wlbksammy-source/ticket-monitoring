#!/usr/bin/env bash
# 被同目录脚本 source。收起会让进程去监听或弹浏览器的环境值。
# 不读取、不导出任何账号秘密。
unset TM_WEB_HOST || true
unset DISPLAY || true
unset XAUTHORITY || true
unset WAYLAND_DISPLAY || true
export PYTHONUNBUFFERED=1
export BROWSER=/bin/true
