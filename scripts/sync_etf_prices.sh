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
sync_report="data/benchmark-reports/etf-sync-${run_timestamp}.json"
coverage_report="data/benchmark-reports/coverage-${run_timestamp}.json"
arguments=(
  --mode "${MFST_BENCHMARK_MODE:-full}"
  --start-date "${MFST_ETF_START_DATE:-2002-01-08}"
  --end-date "${MFST_ETF_END_DATE:-$(date +%F)}"
)

exchanges="${MFST_ETF_EXCHANGES:-NSE,BSE}"
IFS=',' read -r -a selected_exchanges <<<"${exchanges}"
for exchange in "${selected_exchanges[@]}"; do
  arguments+=(--exchange "${exchange}")
done

backend/.venv/bin/mfst sync-etf-prices "${arguments[@]}" >"${sync_report}"
backend/.venv/bin/mfst benchmark-report >"${coverage_report}"

echo "ETF price sync report: ${sync_report}"
echo "Benchmark coverage report: ${coverage_report}"
if command -v jq >/dev/null 2>&1; then
  jq '.' "${sync_report}"
  jq '.' "${coverage_report}"
fi
