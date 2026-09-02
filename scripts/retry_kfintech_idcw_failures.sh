#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p data/rta-reports
log_file="data/rta-reports/kfintech-retry-${run_timestamp}.log"
status_file="data/rta-reports/kfintech-retry-${run_timestamp}.status"

echo "KFintech retry log: ${log_file}"
echo "started_at=${run_timestamp}" >"${status_file}"

MFST_RTA_PROVIDERS=kfintech \
MFST_KFINTECH_RETRY_RETAINED_FAILURES=1 \
MFST_KFINTECH_MAX_CONSECUTIVE_FAILURES=100 \
  scripts/sync_rta_distributions.sh 2>&1 | tee "${log_file}"
status="${PIPESTATUS[0]}"

{
  echo "completed_at=$(date -u +%Y%m%dT%H%M%SZ)"
  echo "exit_status=${status}"
  echo "log=${log_file}"
} >>"${status_file}"

echo "KFintech retry status: ${status_file}"
exit "${status}"
