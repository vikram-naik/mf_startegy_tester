#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

report_directory="data/daily-sync-reports"
mkdir -p "${report_directory}"
run_timestamp="$(date -u +%Y%m%dT%H%M%S%NZ)"
log_file="${report_directory}/daily-sync-${run_timestamp}.log"
status_file="${report_directory}/daily-sync-${run_timestamp}.status"
amfi_report="${report_directory}/amfi-nav-${run_timestamp}.json"
nifty_report="${report_directory}/nifty-indices-${run_timestamp}.json"
etf_report="${report_directory}/exchange-etfs-${run_timestamp}.json"
benchmark_report="${report_directory}/benchmark-coverage-${run_timestamp}.json"

migration_status="not_run"
amfi_status="not_run"
nifty_status="not_run"
etf_status="not_run"
benchmark_report_status="not_run"
lock_status="not_attempted"
overall_status=0
sync_end_date="${MFST_DAILY_END_DATE:-$(TZ=Asia/Kolkata date +%F)}"
overlap_days="${MFST_DAILY_OVERLAP_DAYS:-7}"
sync_start_date=""

write_status() {
  exit_status=$?
  {
    echo "started_at=${run_timestamp}"
    echo "completed_at=$(date -u +%Y%m%dT%H%M%SZ)"
    echo "exit_status=${exit_status}"
    echo "lock_status=${lock_status}"
    echo "migration_status=${migration_status}"
    echo "amfi_status=${amfi_status}"
    echo "nifty_status=${nifty_status}"
    echo "etf_status=${etf_status}"
    echo "benchmark_report_status=${benchmark_report_status}"
    echo "sync_start_date=${sync_start_date}"
    echo "sync_end_date=${sync_end_date}"
    echo "overlap_days=${overlap_days}"
    echo "log_file=${log_file}"
    echo "amfi_report=${amfi_report}"
    echo "nifty_report=${nifty_report}"
    echo "etf_report=${etf_report}"
    echo "benchmark_report=${benchmark_report}"
  } >"${status_file}"
  echo "Daily synchronization status: ${status_file}"
}
trap write_status EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

exec > >(tee -a "${log_file}") 2>&1

if [[ ! -x backend/.venv/bin/mfst || ! -x backend/.venv/bin/alembic ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi
if ! [[ "${sync_end_date}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
  || ! TZ=Asia/Kolkata date -d "${sync_end_date}" +%F >/dev/null 2>&1; then
  echo "MFST_DAILY_END_DATE must be a valid ISO date" >&2
  exit 64
fi
if ! [[ "${overlap_days}" =~ ^[0-9]{1,2}$ ]] || (( 10#${overlap_days} > 31 )); then
  echo "MFST_DAILY_OVERLAP_DAYS must be an integer from 0 through 31" >&2
  exit 64
fi
sync_start_date="${MFST_DAILY_START_DATE:-$(TZ=Asia/Kolkata date -d "${sync_end_date} - ${overlap_days} days" +%F)}"
if ! [[ "${sync_start_date}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
  || ! TZ=Asia/Kolkata date -d "${sync_start_date}" +%F >/dev/null 2>&1 \
  || [[ "${sync_start_date}" > "${sync_end_date}" ]]; then
  echo "MFST_DAILY_START_DATE must be a valid ISO date on or before the end date" >&2
  exit 64
fi

exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  lock_status=75
  echo "Another data-acquisition process is already running" >&2
  exit 75
fi
lock_status=0

run_step() {
  step_name="$1"
  shift
  echo "Starting ${step_name}"
  if "$@"; then
    echo "Completed ${step_name}"
    return 0
  else
    step_status=$?
    echo "Failed ${step_name} with exit status ${step_status}" >&2
    return "${step_status}"
  fi
}

run_json_step() {
  step_name="$1"
  report_file="$2"
  shift 2
  echo "Starting ${step_name}"
  if "$@" >"${report_file}"; then
    echo "Completed ${step_name}; report: ${report_file}"
    return 0
  else
    step_status=$?
    echo "Failed ${step_name} with exit status ${step_status}; report: ${report_file}" >&2
    return "${step_status}"
  fi
}

run_step "database migrations" backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
migration_status=$?
if [[ "${migration_status}" -ne 0 ]]; then
  exit 1
fi

run_json_step "AMFI catalog and NAV refresh" "${amfi_report}" \
  backend/.venv/bin/mfst sync-nav \
  --mode incremental \
  --end-date "${sync_end_date}" \
  --overlap-days "${overlap_days}"
amfi_status=$?
if [[ "${amfi_status}" -ne 0 ]]; then
  overall_status=1
fi

nifty_arguments=(
  --mode refresh
  --start-date "${sync_start_date}"
  --end-date "${sync_end_date}"
)
indices="${MFST_NIFTY_INDICES:-Nifty 50,Nifty 500,Nifty Midcap 150,Nifty Smallcap 250,Nifty LargeMidcap 250,Nifty Total Market}"
if [[ -z "${indices}" || "${indices}" == ,* || "${indices}" == *, || "${indices}" == *,,* ]]; then
  echo "MFST_NIFTY_INDICES must be a comma-separated list without empty names" >&2
  nifty_status=64
  overall_status=1
fi
IFS=',' read -r -a selected_indices <<<"${indices}"
if [[ "${nifty_status}" == "not_run" ]]; then
  for index_name in "${selected_indices[@]}"; do
    nifty_arguments+=(--index "${index_name}")
  done
  run_json_step "official Nifty index refresh" "${nifty_report}" \
    backend/.venv/bin/mfst sync-nifty-benchmarks "${nifty_arguments[@]}"
  nifty_status=$?
  if [[ "${nifty_status}" -ne 0 ]]; then
    overall_status=1
  fi
fi

etf_arguments=(
  --mode refresh
  --start-date "${sync_start_date}"
  --end-date "${sync_end_date}"
)
exchanges="${MFST_ETF_EXCHANGES:-NSE,BSE}"
if ! [[ "${exchanges}" =~ ^(NSE|BSE)(,(NSE|BSE))*$ ]]; then
  echo "MFST_ETF_EXCHANGES must be a comma-separated list containing only NSE and/or BSE" >&2
  etf_status=64
  overall_status=1
fi
IFS=',' read -r -a selected_exchanges <<<"${exchanges}"
if [[ "${etf_status}" == "not_run" ]]; then
  for exchange in "${selected_exchanges[@]}"; do
    etf_arguments+=(--exchange "${exchange}")
  done
  run_json_step "NSE/BSE ETF price refresh" "${etf_report}" \
    backend/.venv/bin/mfst sync-etf-prices "${etf_arguments[@]}"
  etf_status=$?
  if [[ "${etf_status}" -ne 0 ]]; then
    overall_status=1
  fi
fi

run_json_step "combined benchmark coverage report" "${benchmark_report}" \
  backend/.venv/bin/mfst benchmark-report
benchmark_report_status=$?
if [[ "${benchmark_report_status}" -ne 0 ]]; then
  overall_status=1
fi

exit "${overall_status}"
