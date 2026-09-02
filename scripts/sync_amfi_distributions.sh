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

sync_arguments=(--mode "${MFST_DISTRIBUTION_SYNC_MODE:-full}")
if [[ "${MFST_DISTRIBUTION_QUARANTINE_RECORD_ERRORS:-0}" == "1" ]]; then
  sync_arguments+=(--quarantine-record-errors)
fi
if [[ "${MFST_DISTRIBUTION_RETRY_QUARANTINED:-0}" == "1" ]]; then
  sync_arguments+=(--retry-quarantined)
fi

backend/.venv/bin/mfst sync-distributions "${sync_arguments[@]}"
backend/.venv/bin/mfst normalize-distributions
backend/.venv/bin/mfst assess-distribution-coverage
