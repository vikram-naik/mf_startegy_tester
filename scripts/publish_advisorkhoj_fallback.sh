#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

mkdir -p data/advisorkhoj-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA/AdvisorKhoj synchronization is already running" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
publication_report="data/advisorkhoj-reports/publication-${run_timestamp}.json"
coverage_report="data/advisorkhoj-reports/coverage-${run_timestamp}.json"
quality_report="data/advisorkhoj-reports/data-quality-${run_timestamp}.json"
publication_arguments=()
if [[ -n "${MFST_ADVISORKHOJ_OPTION_LIMIT:-}" ]]; then
  publication_arguments+=(--option-limit "${MFST_ADVISORKHOJ_OPTION_LIMIT}")
fi

backend/.venv/bin/mfst publish-pending-advisorkhoj-distributions \
  "${publication_arguments[@]}" >"${publication_report}"
backend/.venv/bin/mfst assess-distribution-coverage >"${coverage_report}"
backend/.venv/bin/mfst data-quality-report >"${quality_report}"

echo "AdvisorKhoj fallback publication report: ${publication_report}"
echo "Distribution coverage report: ${coverage_report}"
echo "Data-quality report: ${quality_report}"
if command -v jq >/dev/null 2>&1; then
  jq '.' "${publication_report}"
  jq '.' "${coverage_report}"
fi
