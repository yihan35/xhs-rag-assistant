#!/usr/bin/env bash

set -o pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR" || exit 1

START_EPOCH="$(date +%s)"
START_TIME="$(date '+%Y-%m-%d %H:%M:%S')"

echo "========================================================"
echo "小红书收藏同步开始"
echo "开始时间：${START_TIME}"
echo "工作目录：${ROOT_DIR}"
if [[ -n "${XHS_USER_ID:-}" ]]; then
  echo "用户 ID：使用环境变量 XHS_USER_ID=${XHS_USER_ID}"
else
  echo "用户 ID：自动检测当前登录用户"
fi
echo "========================================================"

python -m crawler.ingest
STATUS=$?

echo
echo "========================================================"
echo "导出开发调试页面"
python tools/export_notes_debug.py
DEBUG_STATUS=$?
if [[ "$DEBUG_STATUS" -eq 0 ]]; then
  echo "调试页面：${ROOT_DIR}/data/notes_debug.html"
else
  echo "调试页面导出失败，退出码：${DEBUG_STATUS}"
fi
echo "========================================================"

END_EPOCH="$(date +%s)"
ELAPSED=$((END_EPOCH - START_EPOCH))
END_TIME="$(date '+%Y-%m-%d %H:%M:%S')"

echo
echo "========================================================"
if [[ "$STATUS" -eq 0 ]]; then
  echo "爬取成功"
else
  echo "爬取失败，退出码：${STATUS}"
fi
echo "结束时间：${END_TIME}"
echo "总耗时：${ELAPSED}s"
echo "========================================================"

exit "$STATUS"
