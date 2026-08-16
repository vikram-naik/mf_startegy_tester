#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

mkdir -p data
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "AMFI synchronization is already running; refusing an overlapping run" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
exec backend/.venv/bin/mfst sync-nav \
  --mode incremental \
  --overlap-days "${MFST_SYNC_OVERLAP_DAYS:-7}"
