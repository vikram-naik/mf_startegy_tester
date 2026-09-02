#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

mkdir -p data/advisorkhoj-captures data/advisorkhoj-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA/Advisorkhoj synchronization is already running" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head || exit 1

capture_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
catalog_file="data/advisorkhoj-captures/catalog-${capture_timestamp}.json"
capture_file="data/advisorkhoj-captures/full-${capture_timestamp}.jsonl"
report_file="data/advisorkhoj-reports/full-${capture_timestamp}.json"
publication_file="data/advisorkhoj-reports/publication-${capture_timestamp}.json"
coverage_file="data/advisorkhoj-reports/coverage-${capture_timestamp}.json"
status=0

catalog_arguments=(
  --output "${catalog_file}"
  --delay-seconds "${MFST_ADVISORKHOJ_CATALOG_DELAY_SECONDS:-0.1}"
)
backend/.venv/bin/python scripts/discover_advisorkhoj_idcw.py "${catalog_arguments[@]}" || status=1

capture_arguments=(
  --output "${capture_file}"
  --catalog-file "${catalog_file}"
  --delay-seconds "${MFST_ADVISORKHOJ_DELAY_SECONDS:-0.1}"
  --workers "${MFST_ADVISORKHOJ_WORKERS:-6}"
)
if [[ "${status}" -eq 0 ]]; then
  backend/.venv/bin/python scripts/capture_advisorkhoj_idcw.py "${capture_arguments[@]}" || status=1
fi

if [[ "${status}" -eq 0 && -s "${catalog_file}" && -s "${capture_file}" ]]; then
  if ! backend/.venv/bin/mfst acquire-advisorkhoj-distributions \
    --catalog-file "${catalog_file}" \
    --capture-file "${capture_file}" >"${report_file}"; then
    status=1
  elif ! backend/.venv/bin/mfst publish-pending-advisorkhoj-distributions \
    >"${publication_file}"; then
    status=1
  elif ! backend/.venv/bin/mfst assess-distribution-coverage >"${coverage_file}"; then
    status=1
  elif ! command -v jq >/dev/null 2>&1; then
    echo "AdvisorKhoj acquisition report written to ${report_file}"
    echo "AdvisorKhoj publication report written to ${publication_file}"
    echo "Distribution coverage report written to ${coverage_file}"
  else
    jq '{
      catalog_ingestion_batch_id,
      capture_ingestion_batch_id,
      catalog_artifact_sha256,
      capture_artifact_sha256,
      catalog_amcs,
      catalog_category_queries,
      catalog_schemes,
      captures_imported,
      source_rows_received,
      source_rows_inserted,
      positive_source_rows,
      zero_source_rows,
      negative_source_rows,
      zero_reference_nav_rows,
      implausible_historical_date_rows,
      future_dated_source_rows,
      mapped_captures,
      unresolved_captures,
      ambiguous_captures,
      canonical_events_published
    }' "${report_file}"
    jq '.' "${publication_file}"
    jq '.' "${coverage_file}"
  fi
else
  echo "AdvisorKhoj acquisition did not produce a complete catalog/capture pair" >&2
  status=1
fi

exit "${status}"
