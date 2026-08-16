from datetime import date
from pathlib import Path

import pytest

from mf_strategy_tester.ingestion.amfi import (
    AmfiDistributionParser,
    AmfiFundListParser,
    AmfiNavParser,
    AmfiSchemeDetailsParser,
    AmfiSchemeListParser,
    InvalidNavSourceRecord,
    SchemeType,
    historical_nav_request,
)
from mf_strategy_tester.ingestion.errors import SourceParseError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"


def test_fund_catalog_parser_rejects_truncated_catalog() -> None:
    with pytest.raises(SourceParseError, match="structure changed"):
        AmfiFundListParser().parse_records(
            b'{\\"mf_id\\":\\"3\\",\\"mf_name\\":\\"Example\\",\\"amc_name\\":}'
        )


def test_historical_no_data_report_is_an_explicit_empty_result() -> None:
    payload = b"<html>No data found on the basis of selected parameters for this report</html>"
    assert AmfiNavParser(allow_empty_report=True).parse_records(payload) == ()
    with pytest.raises(SourceParseError, match="no-data"):
        AmfiNavParser().parse_records(payload)


def test_invalid_source_nav_value_is_a_quarantinable_record() -> None:
    payload = (
        b"Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
        b"Net Asset Value;Repurchase Price;Sale Price;Date\n"
        b"Open Ended Schemes ( Equity Scheme - Large Cap Fund )\n"
        b"Example Mutual Fund\n"
        b"103063;Example Growth;INF109K01AS1;;#DIV/0!;#DIV/0!;#DIV/0!;02-Apr-2006\n"
    )

    record = AmfiNavParser(allow_empty_report=True).parse_records(payload)[0]

    assert isinstance(record, InvalidNavSourceRecord)
    assert record.raw_nav_value == "#DIV/0!"
    assert record.nav_date == date(2006, 4, 2)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_current_nav_parser_preserves_decimal_date_and_provenance_context() -> None:
    records = AmfiNavParser().parse_records(fixture_bytes("nav_current.txt"))

    assert len(records) == 2
    assert records[0].scheme_code == "119551"
    assert str(records[0].nav) == "107.2744"
    assert records[0].nav_date == date(2026, 8, 14)
    assert records[0].fund_house == "Aditya Birla Sun Life Mutual Fund"
    assert records[1].isin_reinvestment is None


def test_historical_nav_parser_accepts_eight_field_official_format() -> None:
    records = AmfiNavParser().parse_records(fixture_bytes("nav_historical.txt"))

    assert [record.nav_date for record in records] == [date(2026, 8, 3), date(2026, 8, 14)]


def test_nav_parser_fails_loudly_on_column_drift() -> None:
    payload = fixture_bytes("nav_current.txt").replace(
        b"107.2744;14-Aug-2026", b"107.2744;unexpected;14-Aug-2026"
    )

    with pytest.raises(SourceParseError, match="expected 6 fields but received 7 at source line 7"):
        AmfiNavParser().parse_records(payload)


def test_nav_parser_rejects_duplicate_scheme_date() -> None:
    payload = fixture_bytes("nav_current.txt")
    duplicate = payload + payload.splitlines(keepends=True)[6]

    with pytest.raises(SourceParseError, match="duplicate scheme/date"):
        AmfiNavParser().parse_records(duplicate)


def test_amfi_json_parsers_validate_expected_envelopes() -> None:
    schemes = AmfiSchemeListParser().parse_records(fixture_bytes("scheme_list.json"))
    details = AmfiSchemeDetailsParser().parse_records(fixture_bytes("scheme_details.json"))
    distributions = AmfiDistributionParser().parse_records(
        fixture_bytes("distributions_empty.json")
    )

    assert schemes[0].scheme_id == "12233"
    assert details[0].launch_date.isoformat() == "2021-04-19T00:00:00+05:30"
    assert distributions == ()


def test_historical_request_enforces_official_ninety_day_limit() -> None:
    with pytest.raises(ValueError, match="cannot exceed 90"):
        historical_nav_request(
            mutual_fund_id="all",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 4, 1),
            scheme_type=SchemeType.ALL,
        )


def test_historical_request_encodes_explicit_source_parameters() -> None:
    request = historical_nav_request(
        mutual_fund_id="3",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 14),
        scheme_type=SchemeType.OPEN_ENDED,
    )

    assert request.parameters == {
        "mf": "3",
        "frmdt": "01-Aug-2026",
        "todt": "14-Aug-2026",
        "tp": "1",
    }
    assert "frmdt=01-Aug-2026" in request.url
