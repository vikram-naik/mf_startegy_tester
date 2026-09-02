#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required for the CAMS browser capture" >&2
  exit 69
fi

mkdir -p data/rta-captures data/rta-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA synchronization is already running" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head || exit 1

capture_mode="${MFST_RTA_CAPTURE_MODE:-full}"
case "${capture_mode}" in
  full)
    capture_suffix="full"
    ;;
  refresh)
    capture_suffix="refresh-$(date -u +%Y%m%dT%H%M%SZ)"
    ;;
  *)
    echo "MFST_RTA_CAPTURE_MODE must be full or refresh" >&2
    exit 64
    ;;
esac

providers="${MFST_RTA_PROVIDERS:-cams,kfintech}"
report_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
cams_file="data/rta-captures/cams-${capture_suffix}.jsonl"
kfintech_file="data/rta-captures/kfintech-${capture_suffix}.jsonl"
cams_report="data/rta-reports/cams-import-${report_timestamp}.json"
kfintech_report="data/rta-reports/kfintech-import-${report_timestamp}.json"
cams_capture_log="data/rta-reports/cams-capture-${report_timestamp}.jsonl"
kfintech_capture_log="data/rta-reports/kfintech-capture-${report_timestamp}.jsonl"
kfintech_fund_error_log="${kfintech_file}.fund-errors.jsonl"
coverage_report="data/rta-reports/coverage-${report_timestamp}.json"
quality_report="data/rta-reports/data-quality-${report_timestamp}.json"
status=0

if [[ ",${providers}," == *",cams,"* ]]; then
  cams_arguments=(
    --output "${cams_file}"
    --max-consecutive-failures "${MFST_CAMS_MAX_CONSECUTIVE_FAILURES:-5}"
  )
  if [[ "${MFST_CAMS_RETRY_RETAINED_FAILURES:-0}" == "1" ]]; then
    cams_arguments+=(--retry-retained-failures)
  fi
  node scripts/capture_cams_idcw.mjs "${cams_arguments[@]}" \
    2> >(tee "${cams_capture_log}" >&2) || status=1
  if [[ -s "${cams_file}" ]]; then
    backend/.venv/bin/mfst import-rta-distributions \
      --capture-file "${cams_file}" >"${cams_report}" || status=1
  fi
fi

if [[ ",${providers}," == *",kfintech,"* ]]; then
  kfintech_arguments=(
    --output "${kfintech_file}"
    --from-date "${MFST_RTA_FROM_DATE:-2000-01-01}"
    --max-consecutive-failures "${MFST_KFINTECH_MAX_CONSECUTIVE_FAILURES:-5}"
  )
  if [[ -n "${MFST_RTA_TO_DATE:-}" ]]; then
    kfintech_arguments+=(--to-date "${MFST_RTA_TO_DATE}")
  fi
  if [[ "${MFST_KFINTECH_RETRY_RETAINED_FAILURES:-0}" == "1" ]]; then
    kfintech_arguments+=(--retry-retained-failures)
  fi
  backend/.venv/bin/python scripts/capture_kfintech_idcw.py "${kfintech_arguments[@]}" \
    2> >(tee "${kfintech_capture_log}" >&2) || status=1
  if [[ -s "${kfintech_file}" ]]; then
    backend/.venv/bin/mfst import-rta-distributions \
      --capture-file "${kfintech_file}" >"${kfintech_report}" || status=1
  fi
fi

backend/.venv/bin/mfst assess-distribution-coverage >"${coverage_report}" || status=1
backend/.venv/bin/mfst data-quality-report >"${quality_report}" || status=1

for report in \
  "${cams_capture_log}" \
  "${kfintech_capture_log}" \
  "${kfintech_fund_error_log}" \
  "${cams_report}" \
  "${kfintech_report}" \
  "${coverage_report}" \
  "${quality_report}"; do
  if [[ -s "${report}" ]]; then
    echo "Report: ${report}"
  fi
done
if command -v jq >/dev/null 2>&1; then
  for report in "${cams_report}" "${kfintech_report}" "${coverage_report}"; do
    if [[ -s "${report}" ]]; then
      jq '.' "${report}"
    fi
  done
fi
exit "${status}"
