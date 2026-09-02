#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_directory}"

if [[ ! -x backend/.venv/bin/mfst ]]; then
  echo "Backend environment is missing. Run: uv sync --project backend --extra dev" >&2
  exit 69
fi

mkdir -p data/benchmark-reports
exec 9>data/amfi-sync.lock
if ! flock -n 9; then
  echo "Another data-acquisition process is already running" >&2
  exit 75
fi

backend/.venv/bin/alembic -c backend/alembic.ini upgrade head

run_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
sync_report="data/benchmark-reports/nifty-sync-${run_timestamp}.json"
coverage_report="data/benchmark-reports/coverage-${run_timestamp}.json"
arguments=(
  --mode "${MFST_BENCHMARK_MODE:-full}"
  --start-date "${MFST_NIFTY_START_DATE:-1990-07-03}"
  --end-date "${MFST_NIFTY_END_DATE:-$(date +%F)}"
)

indices="${MFST_NIFTY_INDICES:-Nifty 50,Nifty 500,Nifty Midcap 150,Nifty Smallcap 250,Nifty LargeMidcap 250,Nifty Total Market}"
IFS=',' read -r -a selected_indices <<<"${indices}"
for index_name in "${selected_indices[@]}"; do
  arguments+=(--index "${index_name}")
done

backend/.venv/bin/mfst sync-nifty-benchmarks "${arguments[@]}" >"${sync_report}"
backend/.venv/bin/mfst benchmark-report >"${coverage_report}"

echo "Nifty benchmark sync report: ${sync_report}"
echo "Benchmark coverage report: ${coverage_report}"
if command -v jq >/dev/null 2>&1; then
  jq '.' "${sync_report}"
  jq '.' "${coverage_report}"
fi
