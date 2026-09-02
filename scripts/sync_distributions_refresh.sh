#!/usr/bin/env bash
set -uo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

required_scripts=(
  scripts/sync_amfi_distributions.sh
  scripts/sync_rta_distributions.sh
  scripts/sync_advisorkhoj_distributions.sh
)
for required_script in "${required_scripts[@]}"; do
  if [[ ! -x "${required_script}" ]]; then
    echo "Required executable is missing: ${required_script}" >&2
    exit 69
  fi
done
if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

report_directory="data/distribution-refresh-reports"
mkdir -p "${report_directory}"
report_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="${report_directory}/distribution-refresh-${report_timestamp}.log"
status_file="${report_directory}/distribution-refresh-${report_timestamp}.status"

exec 8>data/distribution-refresh.lock
if ! flock -n 8; then
  echo "A complete distribution refresh is already running" >&2
  exit 75
fi

exec > >(tee -a "${log_file}") 2>&1

amfi_status="not_run"
rta_status="not_run"
advisorkhoj_status="not_run"
coverage_status="not_run"
overall_status=0

write_status() {
  exit_status=$?
  {
    echo "exit_status=${exit_status}"
    echo "amfi_status=${amfi_status}"
    echo "rta_status=${rta_status}"
    echo "advisorkhoj_status=${advisorkhoj_status}"
    echo "coverage_status=${coverage_status}"
    echo "log_file=${log_file}"
  } >"${status_file}"
  echo "Distribution refresh status: ${status_file}"
}
trap write_status EXIT

run_step() {
  step_name="$1"
  shift
  echo "Starting ${step_name} distribution refresh"
  if "$@"; then
    echo "Completed ${step_name} distribution refresh"
    return 0
  else
    step_status=$?
    echo "Failed ${step_name} distribution refresh with exit status ${step_status}" >&2
    return "${step_status}"
  fi
}

run_step "AMFI" env MFST_DISTRIBUTION_SYNC_MODE=refresh \
  scripts/sync_amfi_distributions.sh
amfi_status=$?
if [[ "${amfi_status}" -ne 0 ]]; then
  overall_status=1
fi

run_step "CAMS/KFintech" env MFST_RTA_CAPTURE_MODE=refresh \
  scripts/sync_rta_distributions.sh
rta_status=$?
if [[ "${rta_status}" -ne 0 ]]; then
  overall_status=1
fi

run_step "AdvisorKhoj" scripts/sync_advisorkhoj_distributions.sh
advisorkhoj_status=$?
if [[ "${advisorkhoj_status}" -ne 0 ]]; then
  overall_status=1
fi

run_step "combined coverage assessment" backend/.venv/bin/mfst \
  assess-distribution-coverage
coverage_status=$?
if [[ "${coverage_status}" -ne 0 ]]; then
  overall_status=1
fi

exit "${overall_status}"
