#!/usr/bin/env bash
# 启动后端（FastAPI）+ 前端（Vite）
# 若端口已被占用，自动 kill 后重启

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PORT="${PORT:-8000}"
BACKEND_HOST="${HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

PID_FILE="$ROOT_DIR/data/server.pid"
FRONTEND_PID_FILE="$ROOT_DIR/data/frontend.pid"
LOG_FILE="$ROOT_DIR/data/server.log"
FRONTEND_LOG_FILE="$ROOT_DIR/data/frontend.log"

mkdir -p "$ROOT_DIR/data"

# ── 工具函数 ─────────────────────────────────────────────────────

kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti TCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "  端口 ${port} 被占用（PID: ${pids}），正在 kill..."
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 0.5
  fi
}

kill_pid_file() {
  local pidfile=$1
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.3
    fi
    rm -f "$pidfile"
  fi
}

# ── 停止旧进程 ────────────────────────────────────────────────────

echo "▶ 清理旧进程..."
kill_pid_file "$PID_FILE"
kill_pid_file "$FRONTEND_PID_FILE"
kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"

# ── 启动后端 ──────────────────────────────────────────────────────

echo "▶ 启动后端（FastAPI）..."
nohup python -m uvicorn main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT" \
  >"$LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$PID_FILE"

sleep 1
if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  echo "✗ 后端启动失败，查看日志：$LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi
echo "  后端已启动（PID: ${BACKEND_PID}）"

# ── 启动前端 ──────────────────────────────────────────────────────

echo "▶ 启动前端（Vite）..."
cd "$ROOT_DIR/frontend"
nohup npm run dev >"$FRONTEND_LOG_FILE" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$FRONTEND_PID_FILE"
cd "$ROOT_DIR"

# 等前端起来（最多 10 秒）
for i in $(seq 1 10); do
  if lsof -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! lsof -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✗ 前端启动超时，查看日志：$FRONTEND_LOG_FILE"
  exit 1
fi
echo "  前端已启动（PID: ${FRONTEND_PID}）"

# ── 打印访问地址 ──────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        拾光智行 · 启动成功               ║"
echo "╠══════════════════════════════════════════╣"
echo "║  🌐 前端页面   http://localhost:${FRONTEND_PORT}      ║"
echo "║  🔌 后端 API   http://${BACKEND_HOST}:${BACKEND_PORT}      ║"
echo "║  📄 后端日志   data/server.log           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "停止服务：./stop_server.sh"
