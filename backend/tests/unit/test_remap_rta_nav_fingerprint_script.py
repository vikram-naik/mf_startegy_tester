import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "scripts" / "remap_rta_nav_fingerprint.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run(tmp_path: Path, *, failing_command: str = "") -> tuple[int, list[str], dict[str, str]]:
    project = tmp_path / "project"
    scripts = project / "scripts"
    binaries = project / "backend" / ".venv" / "bin"
    captures = project / "data" / "rta-captures"
    for directory in (scripts, binaries, captures):
        directory.mkdir(parents=True)
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)
    (captures / "kfintech-full.jsonl").write_text("{}\n")
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
        "MFST_TEST_CALL_LOG": str(call_log),
        "MFST_TEST_FAIL_COMMAND": failing_command,
        "PATH": f"{binaries}:{os.environ['PATH']}",
    }
    completed = subprocess.run(
        [str(scripts / SCRIPT.name)],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    status_files = list((project / "data" / "rta-reports").glob("*.status"))
    assert len(status_files) == 1
    status = dict(
        line.split("=", 1) for line in status_files[0].read_text().splitlines() if "=" in line
    )
    return completed.returncode, call_log.read_text().splitlines(), status


def test_remap_script_runs_offline_steps_in_order_and_records_status(tmp_path: Path) -> None:
    exit_code, calls, status = _run(tmp_path)

    assert exit_code == 0
    assert calls == [
        "alembic -c backend/alembic.ini upgrade head",
        "mfst distribution-payout-gap-report --since 2025-01-01",
        "mfst distribution-identity-backlog-report",
        "mfst resume-rta-distribution-import --capture-file data/rta-captures/kfintech-full.jsonl",
        "mfst distribution-identity-backlog-report",
        "mfst assess-distribution-coverage",
        "mfst distribution-payout-gap-report --since 2025-01-01",
    ]
    assert status["exit_status"] == "0"
    assert status["remap_status"] == "0"
    assert status["capture_files_completed"] == "1"


def test_remap_script_failure_is_nonzero_and_still_writes_reports(tmp_path: Path) -> None:
    exit_code, calls, status = _run(tmp_path, failing_command="resume-rta-distribution-import")

    assert exit_code == 1
    assert calls[-1] == "mfst distribution-payout-gap-report --since 2025-01-01"
    assert status["exit_status"] == "1"
    assert status["remap_status"] == "1"
    assert status["capture_files_completed"] == "0"
