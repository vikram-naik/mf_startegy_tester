from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiFundRecord,
    DataQualityIssueRecord,
    DistributionParseIssueRecord,
    IngestionBatchRecord,
    SchemeOptionRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.data_quality import DataQualityReportService


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quality.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return factory()


def test_report_deduplicates_nav_observations_and_lists_unresolved_options(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    first_batch = IngestionBatchRecord(
        provider="amfi",
        source_type="historical_nav",
        source_url="https://example.invalid/nav",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    second_batch = IngestionBatchRecord(
        provider="amfi",
        source_type="historical_nav",
        source_url="https://example.invalid/nav",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add_all([first_batch, second_batch])
    session.flush()
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="Example Mutual Fund",
            catalog_batch_id=first_batch.id,
        )
    )
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="known-option",
            first_observed_nav_date=date(2020, 1, 1),
            last_observed_nav_date=date(2020, 1, 1),
            first_observed_batch_id=first_batch.id,
        )
    )
    session.flush()
    session.add_all(
        [
            DataQualityIssueRecord(
                ingestion_batch_id=batch.id,
                issue_code="NON_POSITIVE_NAV",
                severity="error",
                amfi_scheme_code="known-option",
                nav_date=date(2020, 1, 1),
                details="source value was zero",
            )
            for batch in (first_batch, second_batch)
        ]
    )
    for option_id in ("known-option", "missing-option"):
        session.add(
            AmfiDistributionRecord(
                mutual_fund_id="20",
                source_scheme_id="712",
                source_option_id=option_id,
                scheme_name="Example Fund",
                nav_name="Example IDCW",
                record_date=date(2020, 2, 1),
                raw_source_value="1.5",
                source_value=Decimal("1.5"),
                source_unit="amount",
                content_signature=("a" if option_id == "known-option" else "b") * 64,
                first_observed_batch_id=first_batch.id,
            )
        )
    session.add_all(
        [
            AmfiDistributionRecord(
                mutual_fund_id="20",
                source_scheme_id="712",
                source_option_id="known-option",
                scheme_name="Example Fund",
                nav_name=nav_name,
                record_date=date(2020, 3, day),
                raw_source_value=raw_value,
                source_value=source_value,
                ratio_numerator=ratio_numerator,
                ratio_denominator=ratio_denominator,
                source_unit=source_unit,
                content_signature=signature * 64,
                first_observed_batch_id=first_batch.id,
            )
            for (
                day,
                nav_name,
                raw_value,
                source_value,
                ratio_numerator,
                ratio_denominator,
                source_unit,
                signature,
            ) in (
                (1, "Example Fund - IDCW", "0", Decimal("0"), None, None, "amount", "c"),
                (2, "Example Fund - IDCW", "10%", Decimal("10"), None, None, "percentage", "d"),
                (3, "Example Fund - Bonus", "1:3", None, 1, 3, "ratio", "e"),
                (4, "Example Fund - Bonus", "1", Decimal("1"), None, None, "amount", "f"),
                (5, "Example Fund - Growth", "1", Decimal("1"), None, None, "amount", "1"),
                (
                    6,
                    "Example Fund - Bonus / Dividend",
                    "1",
                    Decimal("1"),
                    None,
                    None,
                    "amount",
                    "2",
                ),
                (7, "Example Fund - Daily", "1", Decimal("1"), None, None, "amount", "3"),
                (
                    8,
                    "Example Fund - Growth IDCW",
                    "1",
                    Decimal("1"),
                    None,
                    None,
                    "amount",
                    "4",
                ),
            )
        ]
    )
    session.commit()

    report = DataQualityReportService(session).build_report()

    assert len(report.nav_issues) == 1
    assert report.nav_issues[0].distinct_observations == 1
    assert report.nav_issues[0].occurrence_count == 2
    assert report.unresolved_distribution_rows == 1
    assert report.non_positive_distribution_value_rows == 1
    assert report.canonical_distribution_events == 0
    assert report.canonical_distribution_revisions == 0
    assert report.canonical_distribution_source_links == 0
    assert report.latest_distribution_normalization_run is None
    gate = {item.category: item for item in report.distribution_normalization_gate}
    assert gate["cash_amount_candidate"].row_count == 1
    assert gate["unmatched_option_identifier"].row_count == 1
    assert gate["non_positive_scalar"].row_count == 1
    assert gate["percentage_source_value"].row_count == 1
    assert gate["ratio_source_value"].row_count == 1
    assert gate["explicit_bonus_option"].row_count == 1
    assert gate["explicit_growth_or_cumulative_option"].row_count == 1
    assert gate["ambiguous_bonus_or_distribution_option"].row_count == 1
    assert gate["unknown_option_label"].row_count == 1
    assert gate["conflicting_option_labels"].row_count == 1
    assert sum(item.row_count for item in report.distribution_normalization_gate) == 10
    assert {item.classification for item in report.distribution_label_review_items} == {
        "bonus",
        "bonus_or_distribution",
        "conflicting",
        "growth_or_cumulative",
        "unknown",
    }
    assert report.distribution_identifier_review_counts["unreviewed"] == 1
    assert [item.source_option_id for item in report.unresolved_distribution_identifiers] == [
        "missing-option"
    ]
    session.close()


def test_report_includes_parse_issue_state_and_running_batches(tmp_path: Path) -> None:
    session = _session(tmp_path)
    batch = IngestionBatchRecord(
        provider="amfi",
        source_type="distributions",
        source_url="https://example.invalid/distributions",
        request_parameters={"mf": "20"},
        parser_version="test",
        status="running",
        started_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    catalog_batch = IngestionBatchRecord(
        provider="amfi",
        source_type="fund_catalog",
        source_url="https://example.invalid/funds",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add_all([batch, catalog_batch])
    session.flush()
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="Example Mutual Fund",
            catalog_batch_id=catalog_batch.id,
        )
    )
    session.flush()
    session.add(
        DistributionParseIssueRecord(
            ingestion_batch_id=batch.id,
            mutual_fund_id="20",
            source_scheme_id="712",
            record_number=1,
            issue_code="INVALID_DISTRIBUTION_VALUE",
            error_details="invalid value",
            raw_record={"Rate_of_div": "bad"},
            source_record_signature="c" * 64,
            status="open",
        )
    )
    session.commit()

    report = DataQualityReportService(session).build_report()

    assert report.distribution_parse_issues_open == 1
    assert report.distribution_parse_issues_resolved == 0
    assert len(report.running_ingestion_batches) == 1
    assert report.running_ingestion_batches[0].batch_id == batch.id
    session.close()
