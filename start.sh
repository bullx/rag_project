#!/bin/bash
# RAG Project Startup Script
# Launches FastAPI backend + React (Vite) frontend

set -e

echo "Starting RAG — Retrieval-Augmented Generation System"
echo "======================================================="

# Load env vars
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
  echo "Loaded .env"
else
  echo "No .env found — copy .env.example to .env"
  exit 1
fi

# Create data dirs (use local_data/ — avoid root-owned ./data/ from prior sudo runs)
DATA_DIR=${DATA_DIR:-./local_data}
mkdir -p "$DATA_DIR/uploads" "$DATA_DIR/chroma_db" "${LOG_DIR:-./local_data/logs}"
if [ ! -w "$DATA_DIR/chroma_db" ]; then
  echo "Cannot write to $DATA_DIR/chroma_db — fix permissions or set CHROMA_DB_PATH in .env"
  exit 1
fi

API_PORT=${API_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-5173}
API_HOST=${API_HOST:-127.0.0.1}

stop_existing() {
  echo "Stopping any existing RAG processes..."
  pkill -f "uvicorn backend.api:app" 2>/dev/null || true
  pkill -f "vite" 2>/dev/null || true
  pkill -f "streamlit run frontend/app.py" 2>/dev/null || true

  for port in "$API_PORT" "$FRONTEND_PORT"; do
    for _ in $(seq 1 10); do
      if ! lsof -ti :"$port" > /dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
  done
}

wait_for_port() {
  local port=$1
  local name=$2
  if lsof -ti :"$port" > /dev/null 2>&1; then
    echo "Port $port is still in use ($name)."
    echo "   Run: ./stop.sh"
    echo "   Or:  lsof -i :$port   then   kill <PID>"
    exit 1
  fi
}

stop_existing
wait_for_port "$API_PORT" "API"
wait_for_port "$FRONTEND_PORT" "Frontend"

echo ""
echo "Starting FastAPI backend on http://localhost:$API_PORT"
echo "   API Docs: http://localhost:$API_PORT/docs"
uvicorn backend.api:app --host "$API_HOST" --port "$API_PORT" --reload &
API_PID=$!

# Wait for backend to be ready
echo "Waiting for backend..."
for i in $(seq 1 20); do
  if curl -sf "http://localhost:$API_PORT/health" > /dev/null 2>&1; then
    echo "Backend is ready!"
    break
  fi
  sleep 1
done

echo ""
echo "Starting React frontend on http://localhost:$FRONTEND_PORT"
(
  cd frontend
  npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT"
) &
UI_PID=$!

# Wait for frontend to be ready
echo "Waiting for frontend..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:$FRONTEND_PORT" > /dev/null 2>&1; then
    echo "Frontend is ready!"
    break
  fi
  sleep 1
done

echo ""
echo "======================================================="
echo "RAG System is running!"
echo "   Frontend:  http://localhost:$FRONTEND_PORT"
echo "   API:       http://localhost:$API_PORT"
echo "   API Docs:  http://localhost:$API_PORT/docs"
echo ""
echo "   Use http:// (not https://) when opening in your browser."
echo "   Logs:      ./local_data/logs/rag.log"
echo ""
echo "Press Ctrl+C to stop all services."

# Wait and cleanup on exit
trap "echo ''; echo 'Stopping...'; kill $API_PID $UI_PID 2>/dev/null; exit" INT TERM
wait
