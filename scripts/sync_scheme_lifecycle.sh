#!/usr/bin/env bash
set -uo pipefail

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

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
sync_report="data/lifecycle-reports/sync-${run_timestamp}.json"
coverage_report="data/lifecycle-reports/coverage-${run_timestamp}.json"
quality_report="data/lifecycle-reports/data-quality-${run_timestamp}.json"
log_file="data/lifecycle-reports/sync-${run_timestamp}.log"
status_file="data/lifecycle-reports/sync-${run_timestamp}.status"
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

echo "Scheme lifecycle log: ${log_file}"
echo "started_at=${run_timestamp}" >"${status_file}"
exec > >(tee "${log_file}") 2>&1
status=0

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head || status=1
if [[ "${status}" -eq 0 ]]; then
  backend/.venv/bin/mfst sync-scheme-lifecycle "${arguments[@]}" >"${sync_report}" || status=1
fi
if [[ "${status}" -eq 0 ]]; then
  backend/.venv/bin/mfst scheme-lifecycle-report >"${coverage_report}" || status=1
  backend/.venv/bin/mfst data-quality-report >"${quality_report}" || status=1
fi

{
  echo "completed_at=$(date -u +%Y%m%dT%H%M%SZ)"
  echo "exit_status=${status}"
  echo "mode=${MFST_LIFECYCLE_MODE:-full}"
  echo "fund_ids=${MFST_LIFECYCLE_FUND_IDS:-all}"
  echo "log=${log_file}"
} >>"${status_file}"

for report in "${sync_report}" "${coverage_report}" "${quality_report}"; do
  if [[ -s "${report}" ]]; then
    echo "Report: ${report}"
  fi
done
if command -v jq >/dev/null 2>&1; then
  for report in "${sync_report}" "${coverage_report}"; do
    if [[ -s "${report}" ]]; then
      jq '.' "${report}"
    fi
  done
fi
echo "Scheme lifecycle status: ${status_file}"
exit "${status}"
