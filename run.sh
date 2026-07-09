#!/usr/bin/env bash
# Chat with Data (.NET + Python) — local launcher for macOS / Linux.
# Starts the Python micro-service (port 8000) and the .NET front end (port 5080).
# Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

PY_DIR="python-service"
NET_DIR="dotnet-app"

echo "▶ Setting up Python micro-service..."
if [ ! -d "$PY_DIR/.venv" ]; then
  python3 -m venv "$PY_DIR/.venv"
  "$PY_DIR/.venv/bin/pip" install -q --upgrade pip
  "$PY_DIR/.venv/bin/pip" install -q -r "$PY_DIR/requirements.txt"
fi
if [ ! -f "$PY_DIR/.env" ]; then
  echo "  ⚠ $PY_DIR/.env not found — copying from .env.example."
  echo "    Edit it with your LITELLM_API_KEY / LITELLM_API_BASE for live answers."
  cp "$PY_DIR/.env.example" "$PY_DIR/.env"
fi

echo "▶ Starting Python micro-service on http://localhost:8000 ..."
( cd "$PY_DIR" && SERVICE_PORT=8000 .venv/bin/python app.py ) &
PY_PID=$!

cleanup() { echo; echo "Stopping..."; kill "$PY_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# Wait for Python to be ready
for _ in $(seq 1 20); do
  curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 0.5
done

echo "▶ Starting .NET front end on http://localhost:5080 ..."
echo
echo "  ➜ Open http://localhost:5080 in your browser."
echo
( cd "$NET_DIR" && dotnet run -c Release )
