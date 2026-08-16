#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

mkdir -p data
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "AMFI synchronization is already running; refusing an overlapping run" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head

sync_arguments=(
  sync-nav
  --mode full
  --start-date "${MFST_SYNC_START_DATE:-2006-04-01}"
)
if [[ -n "${MFST_SYNC_END_DATE:-}" ]]; then
  sync_arguments+=(--end-date "${MFST_SYNC_END_DATE}")
fi

exec backend/.venv/bin/mfst "${sync_arguments[@]}"
