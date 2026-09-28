import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "scripts" / "sync_all_daily.sh"


@dataclass(frozen=True)
class _ScriptResult:
    completed: subprocess.CompletedProcess[str]
    call_log: str
    report_directory: Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_daily_sync(tmp_path: Path, *, failing_command: str = "") -> _ScriptResult:
    project = tmp_path / "project"
    scripts = project / "scripts"
    binaries = project / "backend" / ".venv" / "bin"
    scripts.mkdir(parents=True)
    binaries.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    (scripts / SCRIPT.name).chmod(0o755)
    call_log = project / "calls.log"
    _write_executable(
        binaries / "alembic",
        '#!/usr/bin/env bash\necho "alembic $*" >>"${MFST_TEST_CALL_LOG}"\n',
    )
    _write_executable(
        binaries / "mfst",
        "#!/usr/bin/env bash\n"
        'echo "mfst $*" >>"${MFST_TEST_CALL_LOG}"\n'
        'if [[ "${1:-}" == "${MFST_TEST_FAIL_COMMAND:-}" ]]; then exit 9; fi\n'
        'echo "{\\"command\\":\\"${1:-}\\"}"\n',
    )
    environment = {
        **os.environ,
        "MFST_DAILY_END_DATE": "2026-09-04",
        "MFST_DAILY_OVERLAP_DAYS": "2",
        "MFST_NIFTY_INDICES": "Nifty 50,Nifty 500",
        "MFST_ETF_EXCHANGES": "NSE,BSE",
        "MFST_TEST_CALL_LOG": str(call_log),
        "MFST_TEST_FAIL_COMMAND": failing_command,
    }
    result = subprocess.run(
        [str(scripts / SCRIPT.name)],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return _ScriptResult(
        completed=result,
        call_log=call_log.read_text(),
        report_directory=project / "data" / "daily-sync-reports",
    )


def test_daily_sync_uses_one_bounded_window_for_all_instrument_sources(tmp_path: Path) -> None:
    result = _run_daily_sync(tmp_path)

    assert result.completed.returncode == 0
    calls = result.call_log
    assert "alembic -c backend/alembic.ini upgrade head" in calls
    assert "mfst sync-nav --mode incremental --end-date 2026-09-04 --overlap-days 2" in calls
    assert (
        "mfst sync-nifty-benchmarks --mode refresh --start-date 2026-09-02 "
        "--end-date 2026-09-04 --index Nifty 50 --index Nifty 500" in calls
    )
    assert (
        "mfst sync-etf-prices --mode refresh --start-date 2026-09-02 "
        "--end-date 2026-09-04 --exchange NSE --exchange BSE" in calls
    )
    assert "mfst benchmark-report" in calls
    status = next(result.report_directory.glob("daily-sync-*.status")).read_text()
    assert "exit_status=0" in status
    assert "amfi_status=0" in status
    assert "nifty_status=0" in status
    assert "etf_status=0" in status
    assert "sync_start_date=2026-09-02" in status


def test_daily_sync_continues_independent_sources_and_fails_overall(tmp_path: Path) -> None:
    result = _run_daily_sync(tmp_path, failing_command="sync-nav")

    assert result.completed.returncode == 1
    calls = result.call_log
    assert "mfst sync-nifty-benchmarks" in calls
    assert "mfst sync-etf-prices" in calls
    assert "mfst benchmark-report" in calls
    status = next(result.report_directory.glob("daily-sync-*.status")).read_text()
    assert "exit_status=1" in status
    assert "amfi_status=9" in status
    assert "nifty_status=0" in status
    assert "etf_status=0" in status
