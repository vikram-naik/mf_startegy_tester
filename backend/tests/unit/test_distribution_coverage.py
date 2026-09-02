from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSchemeRecord,
    AdvisorkhojCatalogSnapshotRecord,
    AdvisorkhojDistributionRecord,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    AmfiDistributionRecord,
    AmfiFundRecord,
    DistributionCoverageAssessmentRecord,
    DistributionCoverageRunRecord,
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionSourceRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.distribution_coverage import (
    DistributionCoverageAssessmentService,
)


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'distribution-coverage.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    batch = IngestionBatchRecord(
        id="batch",
        provider="amfi",
        source_type="distributions",
        source_url="https://example.invalid/distributions",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add(batch)
    session.flush()
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="Example Mutual Fund",
            catalog_batch_id=batch.id,
        )
    )
    for index, (scheme_code, option_type) in enumerate(
        (
            ("unverified", "idcw"),
            ("blocked", "idcw"),
            ("present", "idcw"),
            ("growth", "growth"),
        ),
        start=1,
    ):
        session.add(
            SchemeOptionRecord(
                amfi_scheme_code=scheme_code,
                first_observed_nav_date=date(2020, 1, 1),
                last_observed_nav_date=date(2026, 8, 14),
                first_observed_batch_id=batch.id,
            )
        )
        session.flush()
        metadata = SchemeMetadataVersionRecord(
            id=str(index) * 64,
            amfi_scheme_code=scheme_code,
            scheme_name=f"Example {scheme_code}",
            fund_house_name="Example Mutual Fund",
            scheme_classification="Hybrid",
            plan_type="direct",
            option_type=option_type,
            classification_method="test",
            first_observed_batch_id=batch.id,
        )
        session.add(metadata)
        session.flush()
        session.add(
            NavRevisionRecord(
                id=f"nav-{scheme_code}",
                amfi_scheme_code=scheme_code,
                nav_date=date(2026, 8, 14),
                nav_value=Decimal("10"),
                metadata_version_id=metadata.id,
                revision_number=1,
                content_signature=str(index) * 63 + "n",
                quality_status="valid",
                is_current=True,
                first_observed_batch_id=batch.id,
            )
        )

    blocked_source = AmfiDistributionRecord(
        id="blocked-source",
        mutual_fund_id="20",
        source_scheme_id="100",
        source_option_id="blocked",
        scheme_name="Example blocked",
        nav_name="Example blocked - IDCW",
        record_date=date(2021, 1, 1),
        raw_source_value="0",
        source_value=Decimal("0"),
        source_unit="amount",
        content_signature="a" * 64,
        first_observed_batch_id=batch.id,
    )
    present_source = AmfiDistributionRecord(
        id="present-source",
        mutual_fund_id="20",
        source_scheme_id="101",
        source_option_id="present",
        scheme_name="Example present",
        nav_name="Example present - IDCW",
        record_date=date(2022, 1, 1),
        raw_source_value="1.5",
        source_value=Decimal("1.5"),
        source_unit="amount",
        content_signature="b" * 64,
        first_observed_batch_id=batch.id,
    )
    normalization_run = DistributionNormalizationRunRecord(
        id="normalization-run",
        status="completed",
        normalization_version="test",
        source_rows_examined=1,
        candidate_rows=1,
        blocked_rows=0,
        events_inserted=1,
        revisions_inserted=1,
        rows_unchanged=0,
    )
    event = DistributionEventRecord(
        id="event",
        amfi_scheme_code="present",
        record_date=date(2022, 1, 1),
        event_type="idcw_cash",
    )
    session.add_all([blocked_source, present_source, normalization_run, event])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id="revision",
        distribution_event_id=event.id,
        amount_per_unit_inr=Decimal("1.5"),
        revision_number=1,
        content_signature="c" * 64,
        normalization_version="test",
        normalization_run_id=normalization_run.id,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    session.add(
        DistributionEventRevisionSourceRecord(
            distribution_event_revision_id=revision.id,
            source_distribution_record_id=present_source.id,
        )
    )
    session.commit()
    return session


def test_coverage_assessment_distinguishes_present_blocked_and_unverified(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)

    result = DistributionCoverageAssessmentService(session).assess()

    assert result.status == "completed"
    assert result.options_examined == 3
    assert result.events_present_options == 1
    assert result.blocked_source_options == 1
    assert result.unverified_empty_options == 1
    assessments = {
        item.amfi_scheme_code: item
        for item in session.scalars(
            select(DistributionCoverageAssessmentRecord).where(
                DistributionCoverageAssessmentRecord.coverage_run_id == result.run_id
            )
        )
    }
    assert assessments["present"].coverage_status == "events_present"
    assert assessments["present"].canonical_event_count == 1
    assert assessments["present"].canonical_source_row_count == 1
    assert assessments["blocked"].coverage_status == "blocked_source_rows"
    assert assessments["blocked"].blocked_source_row_count == 1
    assert assessments["unverified"].coverage_status == "unverified_empty"
    assert assessments["unverified"].source_row_count == 0
    assert "growth" not in assessments
    session.close()


def test_blocker_report_reuses_the_normalization_gate_without_mutating_evidence(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    service = DistributionCoverageAssessmentService(session)
    coverage = service.assess()

    report = service.blocked_source_report()

    assert report.coverage_run_id == coverage.run_id
    assert report.blocked_options == 1
    assert report.blocked_source_rows == 1
    assert report.amfi_source_rows == 1
    assert report.other_accepted_source_rows == 0
    gate = {item.category: item for item in report.amfi_gate}
    assert gate["non_positive_scalar"].row_count == 1
    assert gate["non_positive_scalar"].option_count == 1
    assert sum(item.row_count for item in report.amfi_gate) == 1
    assert report.options[0].amfi_scheme_code == "blocked"
    assert report.options[0].amfi_gate_counts == {"non_positive_scalar": 1}
    assert session.scalar(select(func.count()).select_from(DistributionCoverageRunRecord)) == 1
    session.close()


def test_blocker_report_requires_a_completed_coverage_snapshot(tmp_path: Path) -> None:
    session = _session(tmp_path)

    with pytest.raises(LookupError, match="no completed distribution coverage assessment"):
        DistributionCoverageAssessmentService(session).blocked_source_report()

    session.close()


def test_coverage_assessments_are_append_only_snapshots(tmp_path: Path) -> None:
    session = _session(tmp_path)
    service = DistributionCoverageAssessmentService(session)

    first = service.assess()
    second = service.assess()

    assert first.run_id != second.run_id
    assert session.scalar(select(func.count()).select_from(DistributionCoverageRunRecord)) == 2
    assert (
        session.scalar(select(func.count()).select_from(DistributionCoverageAssessmentRecord)) == 6
    )
    session.close()


def test_coverage_counts_mapped_advisorkhoj_rows_as_blocked_secondary_evidence(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    batch = session.get(IngestionBatchRecord, "batch")
    assert batch is not None
    catalog = AdvisorkhojCatalogSnapshotRecord(
        id="ak-catalog",
        ingestion_batch_id=batch.id,
        catalog_sha256="d" * 64,
        amc_count=1,
        category_query_count=1,
        scheme_count=1,
        captured_at=batch.started_at,
    )
    session.add(catalog)
    session.flush()
    catalog_scheme = AdvisorkhojCatalogSchemeRecord(
        id="ak-catalog-scheme",
        catalog_snapshot_id=catalog.id,
        amc_name="Example Mutual Fund",
        category="Hybrid",
        scheme_name="Example unverified IDCW",
        content_signature="e" * 64,
    )
    session.add(catalog_scheme)
    session.flush()
    capture = AdvisorkhojSchemeCaptureRecord(
        id="ak-capture",
        catalog_scheme_id=catalog_scheme.id,
        source_url=("https://www.advisorkhoj.com/mutual-funds-research/getSchemeDividendDetails"),
        source_payload_sha256="f" * 64,
        plan_type="direct",
        option_variant="payout",
        source_frequency="unknown",
        source_row_count=1,
        capture_signature="1" * 64,
        ingestion_batch_id=batch.id,
        captured_at=batch.started_at,
    )
    session.add(capture)
    session.flush()
    review = AdvisorkhojSchemeMappingReviewRecord(
        id="ak-review",
        scheme_capture_id=capture.id,
        status="mapped",
        amfi_scheme_code="unverified",
        mapping_method="nav_fingerprint",
        evidence_details="test",
        review_signature="2" * 64,
    )
    source = AdvisorkhojDistributionRecord(
        id="ak-source",
        scheme_capture_id=capture.id,
        record_date=date(2023, 1, 1),
        raw_amount_per_unit_inr="1",
        amount_per_unit_inr=Decimal("1"),
        raw_reference_nav="10",
        reference_nav=Decimal("10"),
        raw_yield_percent="10",
        yield_percent=Decimal("10"),
        is_positive_cash_distribution=True,
        quality_status="valid",
        content_signature="3" * 64,
        first_observed_batch_id=batch.id,
    )
    session.add_all([review, source])
    session.commit()

    result = DistributionCoverageAssessmentService(session).assess()

    assert result.events_present_options == 1
    assert result.blocked_source_options == 2
    assert result.unverified_empty_options == 0
    blocker_report = DistributionCoverageAssessmentService(session).blocked_source_report()
    assert blocker_report.amfi_source_rows == 1
    assert blocker_report.other_accepted_source_rows == 1
    assert {
        item.amfi_scheme_code: item.other_accepted_source_row_count
        for item in blocker_report.options
    } == {"blocked": 0, "unverified": 1}
    session.close()


def test_coverage_assessment_closes_an_interrupted_prior_run(tmp_path: Path) -> None:
    session = _session(tmp_path)
    interrupted = DistributionCoverageRunRecord(
        id="interrupted-run",
        status="running",
        assessment_version="test",
    )
    session.add(interrupted)
    session.commit()

    DistributionCoverageAssessmentService(session).assess()

    session.refresh(interrupted)
    assert interrupted.status == "failed"
    assert interrupted.completed_at is not None
    assert interrupted.error_details is not None
    assert "superseded" in interrupted.error_details
    session.close()
