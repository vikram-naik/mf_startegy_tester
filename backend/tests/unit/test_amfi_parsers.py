import json
from datetime import date
from decimal import Decimal
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
from mf_strategy_tester.ingestion.errors import (
    SourceParseError,
    SourceTemporarilyUnavailableError,
)

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


def test_nav_parser_classifies_known_amfi_application_error_as_transient() -> None:
    payload = b"""<!DOCTYPE html>
<html><head><title>View/Download NAV History</title></head>
<body><span id="ctl00_amfiHomeContent_lblErrMsg" class="labelRed">
Application Error! Please try again later</span></body></html>"""

    with pytest.raises(SourceTemporarilyUnavailableError, match="application-error page"):
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


def test_current_nav_parser_accepts_explicit_plan_and_option_fields() -> None:
    records = AmfiNavParser().parse_records(fixture_bytes("nav_current_with_qualifiers.txt"))

    assert records[0].scheme_name == "Aditya Birla Sun Life Banking & PSU Debt Fund"
    assert records[0].source_plan == "Direct Plan"
    assert records[0].source_option == "IDCW-Re-investment"
    assert records[0].nav == Decimal("106.9996")
    assert records[0].nav_date == date(2026, 8, 20)
    assert records[1].source_plan == "Regular Plan"
    assert records[1].source_option == "GROWTH"


def test_historical_nav_parser_accepts_explicit_plan_and_option_fields() -> None:
    records = AmfiNavParser().parse_records(fixture_bytes("nav_historical_with_qualifiers.txt"))

    assert records[0].scheme_name == "Aditya Birla Sun Life Multi-Cap Fund-Direct Growth"
    assert records[0].source_plan == "Direct Plan"
    assert records[0].source_option == "GROWTH"
    assert records[0].isin_payout_or_growth == "INF209KB1Y49"
    assert records[1].nav == Decimal("19.06")


def test_qualified_nav_parser_fails_loudly_on_column_drift() -> None:
    payload = fixture_bytes("nav_current_with_qualifiers.txt").replace(
        b"106.9996;20-Aug-2026", b"106.9996;unexpected;20-Aug-2026"
    )

    with pytest.raises(SourceParseError, match="expected 8 fields but received 9 at source line 7"):
        AmfiNavParser().parse_records(payload)


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


def test_empty_scheme_list_is_an_explicit_empty_universe() -> None:
    assert AmfiSchemeListParser().parse_records(b"[]") == ()


def test_distribution_parser_preserves_source_identity_date_and_decimal() -> None:
    records = AmfiDistributionParser().parse_records(fixture_bytes("distributions.json"))

    assert len(records) == 3
    assert records[0].mutual_fund_id == "20"
    assert records[0].source_scheme_id == "712"
    assert records[0].source_option_id == "101143"
    assert records[0].record_date == date(2009, 4, 6)
    assert records[0].raw_source_value == "6.00%"
    assert records[0].source_value == Decimal("6.00")
    assert str(records[2].source_value) == "0.1600"


def test_distribution_parser_preserves_explicit_plan_and_option_fields() -> None:
    records = AmfiDistributionParser().parse_records(
        fixture_bytes("distributions_with_qualifiers.json")
    )

    assert records[0].source_plan == "Direct Plan"
    assert records[0].source_option == "IDCW"
    assert records[1].source_plan is None
    assert records[1].source_option is None


def test_distribution_parser_normalizes_blank_explicit_qualifier_to_missing() -> None:
    payload = json.loads(fixture_bytes("distributions_with_qualifiers.json"))
    payload["data"][0]["Option"] = "  "

    record = AmfiDistributionParser().parse_records(json.dumps(payload).encode())[0]

    assert record.source_option is None


@pytest.mark.parametrize("field,value", [("Plan", 1), ("Option", 1)])
def test_distribution_parser_rejects_invalid_explicit_qualifiers(field: str, value: object) -> None:
    payload = json.loads(fixture_bytes("distributions_with_qualifiers.json"))
    payload["data"][0][field] = value

    with pytest.raises(SourceParseError, match=f"invalid {field} at record 1"):
        AmfiDistributionParser().parse_records(json.dumps(payload).encode())


def test_distribution_parser_accepts_amfi_historical_percent_suffix() -> None:
    payload = {
        "data": [
            {
                "MF_ID": "13",
                "SD_ID": 100171,
                "scheme_id": "78",
                "Scheme_Name": "quant Tax Plan",
                "Nav_name": "quant Tax Plan - Dividend",
                "Div_year": "2006-03-24T00:00:00.000Z",
                "year": "2006",
                "Rate_of_div": "0.35%",
            }
        ]
    }

    record = AmfiDistributionParser().parse_records(json.dumps(payload).encode())[0]

    assert record.raw_source_value == "0.35%"
    assert record.source_value == Decimal("0.35")


def test_distribution_parser_preserves_amfi_ratio_value() -> None:
    record = AmfiDistributionParser().parse_records(fixture_bytes("distribution_ratio.json"))[0]

    assert record.raw_source_value == "1:3"
    assert record.source_value is None
    assert record.ratio_numerator == 1
    assert record.ratio_denominator == 3


def test_distribution_parser_preserves_percentage_amount_annotation() -> None:
    record = AmfiDistributionParser().parse_records(
        fixture_bytes("distribution_composite_value.json")
    )[0]

    assert record.raw_source_value == "20% (Rs 2/- Per Unit"
    assert record.source_value == Decimal("20")
    assert record.ratio_numerator is None
    assert record.ratio_denominator is None
    assert record.annotated_amount_per_unit_inr == Decimal("2")


def test_distribution_parser_accepts_composite_annotation_case_variants() -> None:
    payload = json.loads(fixture_bytes("distribution_composite_value.json"))
    payload["data"][0]["Rate_of_div"] = "30% (Rs 3/- per Unit"

    record = AmfiDistributionParser().parse_records(json.dumps(payload).encode())[0]

    assert record.source_value == Decimal("30")
    assert record.annotated_amount_per_unit_inr == Decimal("3")


def test_distribution_parser_preserves_dash_form_amount_annotation() -> None:
    record = AmfiDistributionParser().parse_records(
        fixture_bytes("distribution_composite_dash_value.json")
    )[0]

    assert record.raw_source_value == "15%-Rs 1.50 Per Unit"
    assert record.source_value == Decimal("15")
    assert record.annotated_amount_per_unit_inr == Decimal("1.50")


def test_distribution_parser_can_quarantine_only_invalid_records() -> None:
    payload = json.loads(fixture_bytes("distributions.json"))
    invalid = dict(payload["data"][0])
    invalid["SD_ID"] = 999999
    invalid["Div_year"] = "2008-01-01T00:00:00.000Z"
    invalid["year"] = "2008"
    invalid["Rate_of_div"] = "not disclosed"
    payload["data"].append(invalid)
    encoded = json.dumps(payload).encode()

    parsed = AmfiDistributionParser().parse_with_issues(encoded)

    assert parsed.rows_received == 4
    assert len(parsed.records) == 3
    assert len(parsed.issues) == 1
    assert parsed.issues[0].record_number == 4
    assert parsed.issues[0].raw_record == invalid
    assert parsed.issues[0].error_details == "invalid distribution value at record 4"
    with pytest.raises(SourceParseError, match="invalid distribution value at record 4"):
        AmfiDistributionParser().parse_records(encoded)


@pytest.mark.parametrize(
    "source_value",
    [
        "20% (INR 2/- Per Unit",
        "20% (Rs 2 Per Unit",
        "20% (Rs /- Per Unit",
        "20% (Rs 0/- Per Unit",
    ],
)
def test_distribution_parser_rejects_invalid_amount_annotations(
    source_value: str,
) -> None:
    payload = json.loads(fixture_bytes("distribution_composite_value.json"))
    payload["data"][0]["Rate_of_div"] = source_value

    with pytest.raises(SourceParseError, match="invalid distribution value"):
        AmfiDistributionParser().parse_records(json.dumps(payload).encode())


@pytest.mark.parametrize("source_value", ["0:3", "1:0", "1::3", "1:3%"])
def test_distribution_parser_rejects_invalid_ratio_values(source_value: str) -> None:
    payload = json.loads(fixture_bytes("distribution_ratio.json"))
    payload["data"][0]["Rate_of_div"] = source_value

    with pytest.raises(SourceParseError, match="invalid distribution value"):
        AmfiDistributionParser().parse_records(json.dumps(payload).encode())


def test_distribution_parser_rejects_duplicate_option_date() -> None:
    payload = json.loads(fixture_bytes("distributions.json"))
    payload["data"].append(payload["data"][0])

    with pytest.raises(SourceParseError, match="duplicate distribution option/date"):
        AmfiDistributionParser().parse_records(json.dumps(payload).encode())


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
