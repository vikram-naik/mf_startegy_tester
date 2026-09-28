#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it, then run: uv sync --project backend --extra dev" >&2
  exit 69
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required. Install Node.js and npm before starting the application" >&2
  exit 69
fi
if [[ ! -d web/node_modules ]]; then
  echo "Frontend dependencies are missing. Run: npm --prefix web install" >&2
  exit 69
fi

backend_pid=""
frontend_pid=""

stop_servers() {
  trap - EXIT INT TERM
  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
    kill "${backend_pid}" 2>/dev/null || true
  fi
  if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
    kill "${frontend_pid}" 2>/dev/null || true
  fi
  if [[ -n "${backend_pid}" ]]; then
    wait "${backend_pid}" 2>/dev/null || true
  fi
  if [[ -n "${frontend_pid}" ]]; then
    wait "${frontend_pid}" 2>/dev/null || true
  fi
}

trap stop_servers EXIT INT TERM

echo "Applying database migrations..."
uv run --project backend alembic -c backend/alembic.ini upgrade head

echo "Starting API at http://127.0.0.1:8000"
uv run --project backend uvicorn mf_strategy_tester.api.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload &
backend_pid=$!

echo "Starting web app at http://127.0.0.1:5173"
npm --prefix web run dev -- --host 127.0.0.1 --port 5173 --strictPort &
frontend_pid=$!

echo "Both development servers are running. Press Ctrl+C to stop them."

if wait -n "${backend_pid}" "${frontend_pid}"; then
  exit_status=0
else
  exit_status=$?
fi

echo "A development server stopped; shutting down the other server." >&2
exit "${exit_status}"
