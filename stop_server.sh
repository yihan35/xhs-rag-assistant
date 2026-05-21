#!/usr/bin/env bash
# 停止后端（FastAPI）+ 前端（Vite）

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PORT="${PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PID_FILE="$ROOT_DIR/data/server.pid"
FRONTEND_PID_FILE="$ROOT_DIR/data/frontend.pid"

stop_by_pidfile() {
  local label=$1 pidfile=$2
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "▶ 停止${label}（PID: ${pid}）..."
      kill "$pid" 2>/dev/null || true
      for _ in {1..10}; do
        if ! kill -0 "$pid" 2>/dev/null; then break; fi
        sleep 0.3
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      echo "  ${label}已停止"
    else
      echo "  ${label} PID 文件存在但进程已不在"
    fi
    rm -f "$pidfile"
  else
    echo "  未找到${label} PID 文件"
  fi
}

stop_by_pidfile "后端" "$PID_FILE"
stop_by_pidfile "前端" "$FRONTEND_PID_FILE"

# 兜底：确认端口已释放
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  端口 ${port} 仍被占用，强制 kill..."
    lsof -ti TCP:"$port" -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
  fi
done

echo ""
echo "所有服务已停止。"
