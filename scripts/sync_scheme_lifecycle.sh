#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

mkdir -p data/lifecycle-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA/lifecycle synchronization is already running" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
sync_report="data/lifecycle-reports/sync-${run_timestamp}.json"
coverage_report="data/lifecycle-reports/coverage-${run_timestamp}.json"
quality_report="data/lifecycle-reports/data-quality-${run_timestamp}.json"
arguments=(
  --mode "${MFST_LIFECYCLE_MODE:-full}"
  --delay-seconds "${MFST_LIFECYCLE_DELAY_SECONDS:-0.1}"
)

if [[ -n "${MFST_LIFECYCLE_FUND_IDS:-}" ]]; then
  IFS=',' read -r -a selected_funds <<<"${MFST_LIFECYCLE_FUND_IDS}"
  for fund_id in "${selected_funds[@]}"; do
    arguments+=(--fund-id "${fund_id}")
  done
fi

backend/.venv/bin/mfst sync-scheme-lifecycle "${arguments[@]}" >"${sync_report}"
backend/.venv/bin/mfst scheme-lifecycle-report >"${coverage_report}"
backend/.venv/bin/mfst data-quality-report >"${quality_report}"

echo "Scheme lifecycle sync report: ${sync_report}"
echo "Scheme lifecycle coverage report: ${coverage_report}"
echo "Data-quality report: ${quality_report}"
if command -v jq >/dev/null 2>&1; then
  jq '.' "${sync_report}"
  jq '.' "${coverage_report}"
fi
