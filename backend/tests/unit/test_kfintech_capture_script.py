import hashlib
import json
import runpy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mf_strategy_tester.ingestion.rta import RtaCaptureParser


def _script_namespace() -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[3]
    return runpy.run_path(str(repository_root / "scripts/capture_kfintech_idcw.py"))


def test_kfintech_capture_recovers_latest_retained_response_without_mutating_error(
    tmp_path: Path,
) -> None:
    output = tmp_path / "kfintech.jsonl"
    error_path = tmp_path / "kfintech.jsonl.errors.jsonl"
    source_payload = """
    <html><table>
      <tr><th>Recorded Date</th><th>Dividend Rate - Individual</th>
          <th>Dividend Rate - Non Individual</th><th>Ex. NAV</th><th>Cum. NAV</th></tr>
      <tr><td>20/Jan/2026</td><td>0.15</td><td>0</td><td>10.25</td><td>10.40</td></tr>
    </table></html>\r\n
    """
    failed_capture = {
        "schema_version": 1,
        "provider": "kfintech",
        "captured_at": "2026-08-21T08:00:00+00:00",
        "fund": {"code": "128", "name": "Axis Mutual Fund"},
        "scheme": {"code": "AF#D1", "name": "Axis Arbitrage Fund - Direct IDCW"},
        "source_url": "https://mfs.kfintech.com/mfs/NAVDividend/Dividend.aspx?Fund=128",
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
        "failure_type": "SchemeExtractionError",
        "error": "parser contract changed",
    }
    error_path.write_text(json.dumps(failed_capture) + "\n", encoding="utf-8")
    original_error_bytes = error_path.read_bytes()
    completed: set[tuple[str, str]] = set()

    recovered, retained_failures = _script_namespace()["_recover_failed_captures"](
        output, completed
    )

    assert recovered == 1
    assert retained_failures == frozenset()
    assert completed == {("128", "AF#D1")}
    assert error_path.read_bytes() == original_error_bytes
    parsed = RtaCaptureParser().parse_records(output.read_bytes())
    assert parsed[0].source_payload == source_payload
    assert parsed[0].source_payload_sha256 == failed_capture["source_payload_sha256"]
    assert parsed[0].rows[0].raw_non_individual_amount == "0"


def test_kfintech_capture_defers_checksum_verified_structural_failure(
    tmp_path: Path,
) -> None:
    output = tmp_path / "kfintech.jsonl"
    error_path = tmp_path / "kfintech.jsonl.errors.jsonl"
    source_payload = "<html><body>postback did not retain the selected scheme</body></html>"
    failed_capture = {
        "schema_version": 1,
        "provider": "kfintech",
        "captured_at": "2026-08-21T08:00:00+00:00",
        "fund": {"code": "128", "name": "Axis Mutual Fund"},
        "scheme": {"code": "EA#D1", "name": "Axis Arbitrage Fund - Direct IDCW"},
        "source_url": "https://mfs.kfintech.com/mfs/NAVDividend/Dividend.aspx?Fund=128",
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
        "failure_type": "SchemeExtractionError",
        "error": "strict parser rejected response",
    }
    error_path.write_text(json.dumps(failed_capture) + "\n", encoding="utf-8")

    recovered, retained_failures = _script_namespace()["_recover_failed_captures"](output, set())

    assert recovered == 0
    assert retained_failures == frozenset({("128", "EA#D1")})
    assert not output.exists()


def test_kfintech_fund_inventory_failure_retains_exact_source_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _script_namespace()
    payload = "<html><body>No schemes are currently listed</body></html>"
    monkeypatch.setitem(namespace["_schemes"].__globals__, "_get", lambda *_args: payload)
    fund = namespace["KfintechFund"](code="999", name="Example Mutual Fund")

    with pytest.raises(namespace["FundSchemeInventoryError"]) as captured:
        namespace["_schemes"](object(), fund, SimpleNamespace())

    error = captured.value
    assert error.source_payload == payload
    assert error.source_url.endswith("Dividend.aspx?Fund=999")

    output = tmp_path / "kfintech.jsonl"
    namespace["_append_fund_error"](output, fund, error)
    retained = json.loads(
        (tmp_path / "kfintech.jsonl.fund-errors.jsonl").read_text(encoding="utf-8")
    )
    assert retained["fund"] == {"code": "999", "name": "Example Mutual Fund"}
    assert retained["source_payload"] == payload
    assert retained["source_payload_sha256"] == hashlib.sha256(payload.encode()).hexdigest()


def test_kfintech_inventory_selects_idcw_alias_when_primary_label_is_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _script_namespace()
    payload = """
    <select name="ctl00$ContentPlaceHolder1$SelScheme">
      <option value="MP#ZM">Principal Corporate Bond Fund - Direct Plan Monthly Growth</option>
      <option value="MP#ZM">Principal Corporate Bond Fund - Direct Plan Monthly IDCW</option>
    </select>
    """
    monkeypatch.setitem(namespace["_schemes"].__globals__, "_get", lambda *_args: payload)
    fund = namespace["KfintechFund"](code="176", name="Sundaram Mutual Fund")

    schemes = namespace["_schemes"](object(), fund, SimpleNamespace())

    assert len(schemes) == 1
    assert schemes[0].code == "MP#ZM"
    assert schemes[0].aliases == ("Principal Corporate Bond Fund - Direct Plan Monthly IDCW",)


def test_kfintech_capture_filters_one_scheme_within_a_fund() -> None:
    namespace = _script_namespace()
    schemes = (
        namespace["KfintechScheme"](code="TO#RD", name="Target Regular IDCW"),
        namespace["KfintechScheme"](code="OTHER", name="Other Regular IDCW"),
    )

    selected = namespace["_filter_schemes"](schemes, ["TO#RD"])

    assert selected == (schemes[0],)


def test_kfintech_capture_rejects_unknown_target_scheme() -> None:
    namespace = _script_namespace()
    schemes = (namespace["KfintechScheme"](code="KNOWN", name="Known IDCW"),)

    with pytest.raises(SystemExit, match="unknown KFintech scheme code"):
        namespace["_filter_schemes"](schemes, ["MISSING"])


def test_kfintech_capture_continues_after_fund_inventory_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = _script_namespace()
    globals_ = namespace["main"].__globals__
    output = tmp_path / "kfintech.jsonl"
    arguments = SimpleNamespace(
        output=output,
        from_date=date(2000, 1, 1),
        to_date=date(2026, 8, 27),
        max_consecutive_failures=5,
        fund_code=None,
        scheme_code=None,
        retry_retained_failures=False,
        max_schemes=None,
        delay_seconds=0,
    )
    funds = (
        namespace["KfintechFund"](code="bad", name="Bad Fund"),
        namespace["KfintechFund"](code="good", name="Good Fund"),
    )
    visited: list[str] = []

    class _ArgumentsParser:
        @staticmethod
        def parse_args() -> SimpleNamespace:
            return arguments

    def schemes(_opener: object, fund: object, _arguments: object) -> tuple[object, ...]:
        visited.append(fund.code)
        if fund.code == "bad":
            raise namespace["FundSchemeInventoryError"](
                "bad inventory",
                source_url="https://example.invalid/bad",
                source_payload="<html>bad inventory</html>",
            )
        return ()

    monkeypatch.setitem(globals_, "_parser", lambda: _ArgumentsParser())
    monkeypatch.setitem(globals_, "_completed_keys", lambda _path: set())
    monkeypatch.setitem(globals_, "_recover_failed_captures", lambda *_args: (0, frozenset()))
    monkeypatch.setattr(globals_["urllib"].request, "build_opener", lambda *_args: object())
    monkeypatch.setitem(globals_, "_get", lambda *_args: "landing page")
    monkeypatch.setitem(globals_, "parse_funds", lambda _payload: funds)
    monkeypatch.setitem(globals_, "_schemes", schemes)

    assert namespace["main"]() == 1
    assert visited == ["bad", "good"]
    messages = capsys.readouterr().err
    assert '"event": "kfintech_fund_inventory_failed"' in messages
    assert '"funds_failed": 1' in messages
