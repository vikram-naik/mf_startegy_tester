#!/usr/bin/env bash
# Re-evaluate already imported CAMS/KFintech captures with the NAV-fingerprint identity rule
# and publish newly mapped declared payouts. No RTA website is contacted. Safe to rerun:
# mapping reviews are append-only and deduplicated, and linked source rows are skipped.
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

if [[ "$#" -gt 0 ]]; then
  capture_files=("$@")
else
  capture_files=(data/rta-captures/kfintech-full.jsonl)
fi
for capture_file in "${capture_files[@]}"; do
  if [[ ! -s "${capture_file}" ]]; then
    echo "RTA capture file is missing or empty: ${capture_file}" >&2
    exit 66
  fi
done
since="${MFST_PAYOUT_GAP_SINCE:-2025-01-01}"

mkdir -p data/rta-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another AMFI/RTA synchronization is already running" >&2
  exit 75
fi

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
prefix="data/rta-reports/nav-fingerprint-remap-${run_timestamp}"
log_file="${prefix}.log"
status_file="${prefix}.status"
gap_before="${prefix}-gap-before.json"
gap_after="${prefix}-gap-after.json"
backlog_before="${prefix}-backlog-before.json"
backlog_after="${prefix}-backlog-after.json"
coverage_report="${prefix}-coverage.json"

migration_status="not_run"
remap_status="not_run"
report_status="not_run"
completed_files=0

write_status() {
  exit_status=$?
  {
    echo "exit_status=${exit_status}"
    echo "started_at=${run_timestamp}"
    echo "finished_at=$(date -u +%Y%m%dT%H%M%SZ)"
    echo "migration_status=${migration_status}"
    echo "remap_status=${remap_status}"
    echo "report_status=${report_status}"
    echo "capture_files_total=${#capture_files[@]}"
    echo "capture_files_completed=${completed_files}"
    echo "since=${since}"
    echo "log_file=${log_file}"
  } >"${status_file}"
  echo "NAV-fingerprint remap status: ${status_file}"
}
trap write_status EXIT

echo "NAV-fingerprint remap log: ${log_file}"
exec > >(tee -a "${log_file}") 2>&1
status=0

if backend/.venv/bin/alembic -c backend/alembic.ini upgrade head; then
  migration_status=0
else
  migration_status=1
  exit 1
fi

report_status=0
backend/.venv/bin/mfst distribution-payout-gap-report --since "${since}" \
  >"${gap_before}" || report_status=1
backend/.venv/bin/mfst distribution-identity-backlog-report \
  >"${backlog_before}" || report_status=1

remap_status=0
for capture_file in "${capture_files[@]}"; do
  safe_name="$(basename "${capture_file}" .jsonl | tr -c '[:alnum:]_-' '_')"
  resume_report="${prefix}-${safe_name}.json"
  echo "Re-evaluating identities and publishing: ${capture_file}"
  if backend/.venv/bin/mfst resume-rta-distribution-import \
    --capture-file "${capture_file}" >"${resume_report}"; then
    completed_files=$((completed_files + 1))
    echo "Report: ${resume_report}"
  else
    remap_status=1
    status=1
  fi
done

backend/.venv/bin/mfst distribution-identity-backlog-report \
  >"${backlog_after}" || report_status=1
backend/.venv/bin/mfst assess-distribution-coverage >"${coverage_report}" || report_status=1
backend/.venv/bin/mfst distribution-payout-gap-report --since "${since}" \
  >"${gap_after}" || report_status=1
if [[ "${report_status}" -ne 0 ]]; then
  status=1
fi

for report in "${gap_before}" "${backlog_before}" "${backlog_after}" "${coverage_report}" \
  "${gap_after}"; do
  if [[ -s "${report}" ]]; then
    echo "Report: ${report}"
  fi
done
if command -v jq >/dev/null 2>&1 && [[ -s "${gap_before}" && -s "${gap_after}" ]]; then
  jq -n --slurpfile before "${gap_before}" --slurpfile after "${gap_after}" '{
    since: $after[0].since,
    live_idcw_options: $after[0].live_idcw_options,
    options_with_events_before: $before[0].options_with_events,
    options_with_events_after: $after[0].options_with_events,
    events_before: $before[0].events,
    events_after: $after[0].events,
    events_by_source_after: $after[0].events_by_source
  }'
fi
exit "${status}"
