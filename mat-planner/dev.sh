#!/bin/bash
# zongkong-agent 分支 — 后端 :8000，前端 :3000

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "=== 启动后端 (port 8000) ==="
cd "$ROOT"
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

echo "=== 启动前端 (port 3000) ==="
cd "$ROOT/frontend"
npm run dev -- -p 3000 &
FRONTEND_PID=$!

echo ""
echo "后端: http://localhost:8000"
echo "前端: http://localhost:3000"
echo ""
echo "按 Ctrl+C 停止所有服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
