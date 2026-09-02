import json
import subprocess
from pathlib import Path


def test_cams_capture_inspection_exposes_fund_scoped_scheme_circuit(
    tmp_path: Path,
) -> None:
    project_directory = Path(__file__).parents[3]
    result = subprocess.run(
        [
            "node",
            str(project_directory / "scripts" / "capture_cams_idcw.mjs"),
            "--output",
            str(tmp_path / "cams.jsonl"),
            "--inspect-state",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    state = json.loads(result.stdout)

    assert state == {
        "event": "cams_capture_state",
        "completed": 0,
        "retained_failures": 0,
        "scheme_failure_action": "skip_fund",
        "fund_failure_action": "stop_capture",
    }
