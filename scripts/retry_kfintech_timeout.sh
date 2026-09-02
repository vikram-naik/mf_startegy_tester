#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

fund_code="${1:-176}"
scheme_code="${2:-TO#RD}"
if [[ -z "${fund_code}" || -z "${scheme_code}" ]]; then
  echo "Usage: $0 [fund-code scheme-code]" >&2
  exit 64
fi
if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

mkdir -p data/rta-captures data/rta-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA synchronization is already running" >&2
  exit 75
fi

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
safe_target="$(printf '%s-%s' "${fund_code}" "${scheme_code}" | tr -c '[:alnum:]_-' '_')"
capture_file="data/rta-captures/kfintech-targeted-${safe_target}-${run_timestamp}.jsonl"
capture_log="data/rta-reports/kfintech-targeted-capture-${run_timestamp}.jsonl"
import_report="data/rta-reports/kfintech-targeted-import-${run_timestamp}.json"
coverage_report="data/rta-reports/coverage-${run_timestamp}.json"
quality_report="data/rta-reports/data-quality-${run_timestamp}.json"
log_file="data/rta-reports/kfintech-targeted-retry-${run_timestamp}.log"
status_file="data/rta-reports/kfintech-targeted-retry-${run_timestamp}.status"

echo "KFintech targeted retry log: ${log_file}"
echo "started_at=${run_timestamp}" >"${status_file}"
exec > >(tee "${log_file}") 2>&1
status=0

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head || status=1
if [[ "${status}" -eq 0 ]]; then
  backend/.venv/bin/python scripts/capture_kfintech_idcw.py \
    --output "${capture_file}" \
    --from-date "${MFST_RTA_FROM_DATE:-2000-01-01}" \
    --fund-code "${fund_code}" \
    --scheme-code "${scheme_code}" \
    --max-consecutive-failures 1 \
    2> >(tee "${capture_log}" >&2) || status=1
fi
if [[ -s "${capture_file}" ]]; then
  backend/.venv/bin/mfst import-rta-distributions \
    --capture-file "${capture_file}" >"${import_report}" || status=1
else
  echo "Targeted capture produced no importable document" >&2
  status=1
fi
backend/.venv/bin/mfst assess-distribution-coverage >"${coverage_report}" || status=1
backend/.venv/bin/mfst data-quality-report >"${quality_report}" || status=1

{
  echo "completed_at=$(date -u +%Y%m%dT%H%M%SZ)"
  echo "exit_status=${status}"
  echo "fund_code=${fund_code}"
  echo "scheme_code=${scheme_code}"
  echo "log=${log_file}"
} >>"${status_file}"

for report in \
  "${capture_file}" \
  "${capture_log}" \
  "${import_report}" \
  "${coverage_report}" \
  "${quality_report}"; do
  if [[ -s "${report}" ]]; then
    echo "Report: ${report}"
  fi
done
if command -v jq >/dev/null 2>&1; then
  for report in "${import_report}" "${coverage_report}"; do
    if [[ -s "${report}" ]]; then
      jq '.' "${report}"
    fi
  done
fi
echo "KFintech targeted retry status: ${status_file}"
exit "${status}"
