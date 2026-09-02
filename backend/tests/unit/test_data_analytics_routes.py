from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.routes.data import (
    get_scheme_performance,
    list_scheme_distributions,
)
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiDistributionRecordSource,
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
    SourceArtifactRecord,
)
from mf_strategy_tester.db.session import create_database_engine


def _database(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'analytics-routes.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    artifact = SourceArtifactRecord(
        sha256="a" * 64,
        byte_size=100,
        media_type="application/json",
        storage_path="sha256/aa/artifact",
    )
    session.add(artifact)
    session.flush()
    batch = IngestionBatchRecord(
        provider="amfi",
        source_type="distributions",
        source_url="https://example.invalid/distributions",
        final_url="https://example.invalid/final",
        request_parameters={},
        parser_version="test-parser",
        status="completed",
        artifact_id=artifact.id,
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
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="123456",
            first_observed_nav_date=date(2020, 1, 1),
            last_observed_nav_date=date(2022, 1, 1),
            first_observed_batch_id=batch.id,
        )
    )
    session.flush()
    metadata = SchemeMetadataVersionRecord(
        id="m" * 64,
        amfi_scheme_code="123456",
        scheme_name="Example Fund - Direct Growth",
        fund_house_name="Example Mutual Fund",
        scheme_classification="Equity",
        plan_type="direct",
        option_type="growth",
        classification_method="test",
        first_observed_batch_id=batch.id,
    )
    session.add(metadata)
    session.flush()
    for revision_id, nav_date, nav_value in (
        ("nav-1", date(2020, 1, 1), Decimal("100")),
        ("nav-2", date(2021, 1, 1), Decimal("110")),
        ("nav-3", date(2022, 1, 1), Decimal("121")),
    ):
        session.add(
            NavRevisionRecord(
                id=revision_id,
                amfi_scheme_code="123456",
                nav_date=nav_date,
                nav_value=nav_value,
                metadata_version_id=metadata.id,
                revision_number=1,
                content_signature=revision_id.ljust(64, "0"),
                quality_status="valid",
                is_current=True,
                first_observed_batch_id=batch.id,
            )
        )

    source = AmfiDistributionRecord(
        id="source-row",
        mutual_fund_id="20",
        source_scheme_id="712",
        source_option_id="123456",
        scheme_name="Example Fund",
        nav_name="Example Fund - IDCW",
        record_date=date(2021, 6, 1),
        raw_source_value="1.50",
        source_value=Decimal("1.50"),
        source_unit="amount",
        content_signature="b" * 64,
        first_observed_batch_id=batch.id,
    )
    session.add(source)
    session.flush()
    session.add(
        AmfiDistributionRecordSource(
            distribution_record_id=source.id,
            ingestion_batch_id=batch.id,
        )
    )
    run = DistributionNormalizationRunRecord(
        id="normalization-run",
        status="completed",
        normalization_version="test-normalizer",
        source_rows_examined=1,
        candidate_rows=1,
        blocked_rows=0,
        events_inserted=1,
        revisions_inserted=1,
        rows_unchanged=0,
    )
    event = DistributionEventRecord(
        id="event",
        amfi_scheme_code="123456",
        record_date=date(2021, 6, 1),
        event_type="idcw_cash",
    )
    session.add_all([run, event])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id="event-revision",
        distribution_event_id=event.id,
        amount_per_unit_inr=Decimal("1.50"),
        revision_number=1,
        content_signature="c" * 64,
        normalization_version="test-normalizer",
        normalization_run_id=run.id,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    session.add(
        DistributionEventRevisionSourceRecord(
            distribution_event_revision_id=revision.id,
            source_distribution_record_id=source.id,
        )
    )
    coverage_run = DistributionCoverageRunRecord(
        id="coverage-run",
        status="completed",
        assessment_version="test-coverage",
        options_examined=1,
        events_present_options=1,
        blocked_source_options=0,
        unverified_empty_options=0,
    )
    session.add(coverage_run)
    session.flush()
    session.add(
        DistributionCoverageAssessmentRecord(
            coverage_run_id=coverage_run.id,
            amfi_scheme_code="123456",
            metadata_version_id=metadata.id,
            coverage_status="events_present",
            source_row_count=1,
            canonical_source_row_count=1,
            blocked_source_row_count=0,
            canonical_event_count=1,
            first_source_record_date=date(2021, 6, 1),
            last_source_record_date=date(2021, 6, 1),
        )
    )
    session.commit()
    return session


def test_scheme_performance_route_returns_explicit_nav_only_assumptions(tmp_path: Path) -> None:
    session = _database(tmp_path)

    response = get_scheme_performance("123456", session)

    assert response.return_basis == "nav_only"
    assert response.distribution_treatment == "excluded"
    assert response.day_count_convention == "actual/365"
    assert response.observation_count == 3
    assert response.since_inception.total_return_pct == Decimal("21.00")
    assert [item.window_years for item in response.rolling_returns] == [1, 3, 5, 10]
    session.close()


def test_distribution_route_drills_to_exact_artifact_and_parser(tmp_path: Path) -> None:
    session = _database(tmp_path)

    response = list_scheme_distributions("123456", session, limit=50, offset=0)

    assert response.total == 1
    assert response.coverage is not None
    assert response.coverage.coverage_status == "events_present"
    assert response.coverage.source_row_count == 1
    assert response.coverage.canonical_event_count == 1
    event = response.items[0]
    assert event.record_date == date(2021, 6, 1)
    assert event.event_type == "idcw_cash"
    revision = event.revisions[0]
    assert revision.amount_per_unit_inr == Decimal("1.50")
    assert revision.is_current is True
    source = revision.sources[0]
    assert source.source_record_id == "source-row"
    assert source.raw_source_value == "1.50"
    assert source.parser_version == "test-parser"
    assert source.artifact_sha256 == "a" * 64
    assert source.source_url == "https://example.invalid/final"
    session.close()
