#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

capture_file="${1:-data/rta-captures/kfintech-full.jsonl}"
if [[ ! -s "${capture_file}" ]]; then
  echo "RTA capture file is missing or empty: ${capture_file}" >&2
  exit 66
fi

import_mode="${MFST_RTA_IMPORT_MODE:-resume}"
case "${import_mode}" in
  resume)
    import_command="resume-rta-distribution-import"
    report_prefix="resume-import"
    ;;
  reparse)
    import_command="import-rta-distributions"
    report_prefix="reparse-import"
    ;;
  *)
    echo "MFST_RTA_IMPORT_MODE must be resume or reparse" >&2
    exit 64
    ;;
esac

mkdir -p data/rta-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA synchronization is already running" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head || exit 1

report_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
resume_report="data/rta-reports/${report_prefix}-${report_timestamp}.json"
coverage_report="data/rta-reports/coverage-${report_timestamp}.json"
quality_report="data/rta-reports/data-quality-${report_timestamp}.json"
status=0

backend/.venv/bin/mfst "${import_command}" \
  --capture-file "${capture_file}" >"${resume_report}" || status=1
backend/.venv/bin/mfst assess-distribution-coverage >"${coverage_report}" || status=1
backend/.venv/bin/mfst data-quality-report >"${quality_report}" || status=1

for report in "${resume_report}" "${coverage_report}" "${quality_report}"; do
  if [[ -s "${report}" ]]; then
    echo "Report: ${report}"
  fi
done
if command -v jq >/dev/null 2>&1; then
  for report in "${resume_report}" "${coverage_report}"; do
    if [[ -s "${report}" ]]; then
      jq '.' "${report}"
    fi
  done
fi
exit "${status}"
