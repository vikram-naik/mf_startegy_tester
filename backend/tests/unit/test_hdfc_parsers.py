from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from mf_strategy_tester.ingestion.errors import SourceParseError
from mf_strategy_tester.ingestion.hdfc import (
    HdfcDistributionNoticeParser,
    HdfcSchemeSummaryParser,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "hdfc"


def test_notice_parser_applies_an_explicit_row_spanning_amount_to_both_plans() -> None:
    text = (FIXTURES / "distribution_notice_extracted.txt").read_text()

    records = HdfcDistributionNoticeParser().parse_extracted_text(text)

    assert [(record.plan_type, record.amount_per_unit_inr) for record in records] == [
        ("regular", Decimal("0.250")),
        ("direct", Decimal("0.250")),
    ]
    assert {record.scheme_name for record in records} == {"HDFC Balanced Advantage Fund"}
    assert {record.record_date for record in records} == {date(2026, 2, 25)}


def test_notice_parser_rejects_multiple_unassigned_distribution_amounts() -> None:
    text = (
        (FIXTURES / "distribution_notice_extracted.txt")
        .read_text()
        .replace("0.250 10.00", "0.250 0.300 10.00")
    )

    with pytest.raises(SourceParseError, match="exactly one row-spanning"):
        HdfcDistributionNoticeParser().parse_extracted_text(text)


def test_notice_parser_rejects_missing_record_date() -> None:
    text = (
        (FIXTURES / "distribution_notice_extracted.txt")
        .read_text()
        .replace("Wednesday, February 25, 2026", "an unspecified date")
    )

    with pytest.raises(SourceParseError, match="record date"):
        HdfcDistributionNoticeParser().parse_extracted_text(text)


def test_scheme_summary_parser_extracts_exact_plan_and_option_code_evidence() -> None:
    text = (FIXTURES / "scheme_summary_extracted.txt").read_text()

    records = HdfcSchemeSummaryParser().parse_extracted_text(text)

    assert [
        (record.amfi_scheme_code, record.plan_type, record.option_type) for record in records
    ] == [
        ("100120", "regular", "idcw"),
        ("118969", "direct", "idcw"),
        ("100119", "regular", "growth"),
        ("118968", "direct", "growth"),
    ]


def test_pdf_parser_rejects_non_pdf_payload() -> None:
    with pytest.raises(SourceParseError, match="not a PDF"):
        HdfcDistributionNoticeParser().parse_records(b"not a PDF")
