#!/bin/bash
# Stop RAG backend + frontend processes

set -e

API_PORT=${API_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-5173}

if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
  API_PORT=${API_PORT:-8000}
  FRONTEND_PORT=${FRONTEND_PORT:-5173}
fi

echo "Stopping RAG services..."

pkill -f "uvicorn backend.api:app" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
pkill -f "streamlit run frontend/app.py" 2>/dev/null || true

for port in "$API_PORT" "$FRONTEND_PORT" 8501; do
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
  fi
done

sleep 1

for port in "$API_PORT" "$FRONTEND_PORT"; do
  if lsof -ti :"$port" > /dev/null 2>&1; then
    echo "Port $port still in use — run: lsof -i :$port"
    exit 1
  fi
done

echo "Stopped. Ports $API_PORT and $FRONTEND_PORT are free."
