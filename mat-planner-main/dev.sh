#!/bin/bash
# main 分支 — 后端 :8001，前端 :3001

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "=== 启动后端 (port 8001) ==="
cd "$ROOT"
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

echo "=== 启动前端 (port 3001) ==="
cd "$ROOT/frontend"
npm run dev -- -p 3001 &
FRONTEND_PID=$!

echo ""
echo "后端: http://localhost:8001"
echo "前端: http://localhost:3001"
echo ""
echo "按 Ctrl+C 停止所有服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
