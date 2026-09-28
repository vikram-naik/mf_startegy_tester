from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.routes.data import (
    get_benchmark_performance,
    get_scheme_performance,
    list_benchmark_series,
    list_classifications,
    list_scheme_distributions,
    screen_funds,
)
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiDistributionRecordSource,
    AmfiFundRecord,
    BenchmarkInstrumentRecord,
    BenchmarkObservationRecord,
    DistributionCoverageAssessmentRecord,
    DistributionCoverageRunRecord,
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionSourceRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    SourceArtifactRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.classification_reference import (
    ClassificationReferenceService,
    classification_definition,
)
from mf_strategy_tester.services.screener_classification_alias import singleton_alias_id


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


def test_screener_uses_current_metadata_and_keeps_pre_inception_exclusions(
    tmp_path: Path,
) -> None:
    session = _database(tmp_path)
    current_metadata = session.get(SchemeMetadataVersionRecord, "m" * 64)
    assert current_metadata is not None
    historical_metadata = SchemeMetadataVersionRecord(
        id="historical-metadata",
        amfi_scheme_code=current_metadata.amfi_scheme_code,
        scheme_name=current_metadata.scheme_name,
        fund_house_name=current_metadata.fund_house_name,
        scheme_classification="Legacy Equity",
        plan_type=current_metadata.plan_type,
        option_type=current_metadata.option_type,
        classification_method=current_metadata.classification_method,
        first_observed_batch_id=current_metadata.first_observed_batch_id,
    )
    session.add(historical_metadata)
    first_nav = session.get(NavRevisionRecord, "nav-1")
    second_nav = session.get(NavRevisionRecord, "nav-2")
    assert first_nav is not None and second_nav is not None
    first_nav.metadata_version_id = historical_metadata.id
    second_nav.metadata_version_id = historical_metadata.id
    ClassificationReferenceService(session).ensure_approved_aliases(("Equity", "Legacy Equity"))
    session.add(
        NavDatasetStatsRecord(
            id=1,
            scheme_options=1,
            valid_current_rows=3,
            error_current_rows=0,
            earliest_valid_nav_date=date(2020, 1, 1),
            latest_valid_nav_date=date(2022, 1, 1),
        )
    )
    session.commit()

    classifications = list_classifications(
        session=session,
        plan_type="direct",
        option_type="growth",
        horizon="1y",
        as_of=date(2021, 1, 1),
        endpoint_tolerance_days=7,
        fund_house=None,
    )
    assert [item.classification_id for item in classifications] == [
        singleton_alias_id(classification_definition("Equity").classification_id)
    ]

    historical_result = screen_funds(
        session=session,
        classification_id=classification_definition("Equity").classification_id,
        classification=None,
        horizon="1y",
        plan_type="direct",
        fund_house=None,
        search=None,
        as_of=date(2021, 1, 1),
        endpoint_tolerance_days=7,
        limit=25,
        offset=0,
        exclusion_limit=25,
        exclusion_offset=0,
    )
    assert historical_result.candidate_options == 1
    assert historical_result.total == 1
    assert historical_result.items[0].scheme_classification == "Equity"

    pre_inception_result = screen_funds(
        session=session,
        classification_id=classification_definition("Equity").classification_id,
        classification=None,
        horizon="1y",
        plan_type="direct",
        fund_house=None,
        search=None,
        as_of=date(2019, 1, 1),
        endpoint_tolerance_days=7,
        limit=25,
        offset=0,
        exclusion_limit=25,
        exclusion_offset=0,
    )
    assert pre_inception_result.candidate_options == 1
    assert pre_inception_result.excluded_stale_endpoint == 1
    assert pre_inception_result.excluded_items[0].latest_nav_date is None
    session.close()


def test_idcw_screener_ranks_trailing_payout_yield_and_exposes_nav_cagr(
    tmp_path: Path,
) -> None:
    session = _database(tmp_path)
    metadata = session.get(SchemeMetadataVersionRecord, "m" * 64)
    prior_revision = session.get(DistributionEventRevisionRecord, "event-revision")
    normalization_run = session.get(DistributionNormalizationRunRecord, "normalization-run")
    batch = session.query(IngestionBatchRecord).one()
    assert metadata is not None and prior_revision is not None and normalization_run is not None
    metadata.scheme_name = "Example Fund - Direct IDCW Payout"
    metadata.option_type = "idcw"
    metadata.isin_payout_or_growth = "INF000000001"
    prior_revision.is_current = False
    session.flush()
    session.add(
        DistributionEventRevisionRecord(
            id="event-revision-current",
            distribution_event_id="event",
            amount_per_unit_inr=Decimal("3"),
            revision_number=2,
            content_signature="d" * 64,
            normalization_version="test-normalizer",
            normalization_run_id=normalization_run.id,
            is_current=True,
        )
    )

    boundary_event = DistributionEventRecord(
        id="boundary-event",
        amfi_scheme_code="123456",
        record_date=date(2021, 1, 1),
        event_type="idcw_cash",
    )
    session.add(boundary_event)
    session.flush()
    session.add(
        DistributionEventRevisionRecord(
            id="boundary-revision",
            distribution_event_id=boundary_event.id,
            amount_per_unit_inr=Decimal("100"),
            revision_number=1,
            content_signature="e" * 64,
            normalization_version="test-normalizer",
            normalization_run_id=normalization_run.id,
            is_current=True,
        )
    )

    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="654321",
            first_observed_nav_date=date(2021, 1, 1),
            last_observed_nav_date=date(2022, 1, 1),
            first_observed_batch_id=batch.id,
        )
    )
    session.flush()
    lower_yield_metadata = SchemeMetadataVersionRecord(
        id="idcw-no-payout-metadata",
        amfi_scheme_code="654321",
        scheme_name="Lower Yield Fund - Direct IDCW Payout",
        fund_house_name="Example Mutual Fund",
        scheme_classification="Equity",
        plan_type="direct",
        option_type="idcw",
        isin_payout_or_growth="INF000000002",
        classification_method="test",
        first_observed_batch_id=batch.id,
    )
    session.add(lower_yield_metadata)
    session.flush()
    for revision_id, nav_date, nav_value in (
        ("no-payout-nav-start", date(2021, 1, 1), Decimal("50")),
        ("no-payout-nav-end", date(2022, 1, 1), Decimal("55")),
    ):
        session.add(
            NavRevisionRecord(
                id=revision_id,
                amfi_scheme_code="654321",
                nav_date=nav_date,
                nav_value=nav_value,
                metadata_version_id=lower_yield_metadata.id,
                revision_number=1,
                content_signature=revision_id.ljust(64, "0"),
                quality_status="valid",
                is_current=True,
                first_observed_batch_id=batch.id,
            )
        )
    lower_yield_event = DistributionEventRecord(
        id="lower-yield-event",
        amfi_scheme_code="654321",
        record_date=date(2021, 12, 1),
        event_type="idcw_cash",
    )
    session.add(lower_yield_event)
    session.flush()
    session.add(
        DistributionEventRevisionRecord(
            id="lower-yield-revision",
            distribution_event_id=lower_yield_event.id,
            amount_per_unit_inr=Decimal("0.5"),
            revision_number=1,
            content_signature="f" * 64,
            normalization_version="test-normalizer",
            normalization_run_id=normalization_run.id,
            is_current=True,
        )
    )

    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="777777",
            first_observed_nav_date=date(2021, 1, 1),
            last_observed_nav_date=date(2022, 1, 1),
            first_observed_batch_id=batch.id,
        )
    )
    session.flush()
    no_payout_metadata = SchemeMetadataVersionRecord(
        id="idcw-no-payout-metadata-2",
        amfi_scheme_code="777777",
        scheme_name="No Payout Fund - Direct IDCW Payout",
        fund_house_name="Example Mutual Fund",
        scheme_classification="Equity",
        plan_type="direct",
        option_type="idcw",
        isin_payout_or_growth="INF000000003",
        classification_method="test",
        first_observed_batch_id=batch.id,
    )
    session.add(no_payout_metadata)
    session.flush()
    for revision_id, nav_date, nav_value in (
        ("missing-payout-nav-start", date(2021, 1, 1), Decimal("25")),
        ("missing-payout-nav-end", date(2022, 1, 1), Decimal("27.5")),
    ):
        session.add(
            NavRevisionRecord(
                id=revision_id,
                amfi_scheme_code="777777",
                nav_date=nav_date,
                nav_value=nav_value,
                metadata_version_id=no_payout_metadata.id,
                revision_number=1,
                content_signature=revision_id.ljust(64, "0"),
                quality_status="valid",
                is_current=True,
                first_observed_batch_id=batch.id,
            )
        )
    session.add(
        NavDatasetStatsRecord(
            id=1,
            scheme_options=3,
            valid_current_rows=7,
            error_current_rows=0,
            earliest_valid_nav_date=date(2020, 1, 1),
            latest_valid_nav_date=date(2022, 1, 1),
        )
    )
    ClassificationReferenceService(session).ensure_approved_aliases(("Equity",))
    session.commit()

    classifications = list_classifications(
        session=session,
        plan_type="direct",
        option_type="idcw",
        horizon="1y",
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
        fund_house=None,
    )
    assert len(classifications) == 1
    assert classifications[0].candidate_options == 3
    assert classifications[0].eligible_options == 2

    result = screen_funds(
        session=session,
        classification_id=classification_definition("Equity").classification_id,
        classification=None,
        horizon="1y",
        plan_type="direct",
        option_type="idcw",
        fund_house=None,
        search=None,
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
        limit=25,
        offset=0,
        exclusion_limit=25,
        exclusion_offset=0,
    )

    assert result.ranking_metric == "payout_yield_frequency_score"
    assert result.ranking_method is not None
    assert result.distribution_treatment == "record_date_payout_yield"
    assert result.total == 2
    assert [item.amfi_scheme_code for item in result.items] == ["123456", "654321"]
    assert result.items[0].payout_amount_per_unit_inr == Decimal("3")
    assert result.items[0].payout_yield_pct == Decimal("300") / Decimal("121")
    assert result.items[0].annualized_return_pct == Decimal("10.0")
    assert result.excluded_no_payout_events == 1
    assert result.items[0].payout_event_count == 1
    assert result.items[0].payout_yield_rank == 1
    assert result.items[0].payout_frequency_rank == 1
    assert result.items[0].idcw_rank_score == Decimal("100")
    assert result.items[0].latest_payout_record_date == date(2021, 6, 1)
    assert result.excluded_items[0].amfi_scheme_code == "777777"
    assert result.excluded_items[0].reason == "no_payout_events"

    with pytest.raises(
        HTTPException,
        match="IDCW payout-yield screening supports only the trailing 12-month horizon",
    ):
        screen_funds(
            session=session,
            classification_id=classification_definition("Equity").classification_id,
            classification=None,
            horizon="3y",
            plan_type="direct",
            option_type="idcw",
            fund_house=None,
            search=None,
            as_of=date(2022, 1, 1),
            endpoint_tolerance_days=7,
            limit=25,
            offset=0,
            exclusion_limit=25,
            exclusion_offset=0,
        )
    session.close()


def test_benchmark_series_and_performance_use_latest_immutable_revision(
    tmp_path: Path,
) -> None:
    session = _database(tmp_path)
    batch = session.query(IngestionBatchRecord).one()
    instrument = BenchmarkInstrumentRecord(
        id="nifty-50-gtr",
        provider="nifty_indices",
        instrument_type="gross_total_return_index",
        source_identifier="NIFTY 50",
        display_name="Nifty 50 TRI",
        benchmark_family="Nifty 50",
        currency="INR",
        first_observed_batch_id=batch.id,
    )
    session.add(instrument)
    for identifier, observation_date, value, observed_at in (
        ("benchmark-old-start", date(2021, 1, 1), Decimal("99"), datetime(2021, 1, 2, tzinfo=UTC)),
        ("benchmark-new-start", date(2021, 1, 1), Decimal("100"), datetime(2021, 1, 3, tzinfo=UTC)),
        ("benchmark-end", date(2022, 1, 1), Decimal("110"), datetime(2022, 1, 2, tzinfo=UTC)),
    ):
        session.add(
            BenchmarkObservationRecord(
                id=identifier,
                benchmark_instrument_id=instrument.id,
                exchange=None,
                observation_date=observation_date,
                close_value=value,
                identity_status="official",
                content_signature=identifier.ljust(64, "0"),
                first_observed_batch_id=batch.id,
                observed_at=observed_at,
            )
        )
    session.commit()

    series = list_benchmark_series(session)
    assert len(series) == 1
    assert series[0].return_basis == "gross_total_return"
    assert series[0].observation_count == 2

    performance = get_benchmark_performance(
        instrument_id=instrument.id,
        session=session,
        as_of=date(2022, 1, 1),
        horizon="1y",
        endpoint_tolerance_days=7,
    )
    assert performance.status == "available"
    assert performance.start_value == Decimal("100")
    assert performance.end_value == Decimal("110")
    assert performance.total_return_pct == Decimal("10.0")
    assert performance.annualized_return_pct == Decimal("10.0")

    stale = get_benchmark_performance(
        instrument_id=instrument.id,
        session=session,
        as_of=date(2022, 1, 10),
        horizon="1y",
        endpoint_tolerance_days=7,
    )
    assert stale.status == "stale_endpoint"
    assert stale.endpoint_staleness_days == 9
    assert stale.total_return_pct is None
    session.close()
