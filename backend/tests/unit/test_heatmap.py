from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from mf_strategy_tester.api.routes.data import get_heatmap
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    BenchmarkInstrumentRecord,
    BenchmarkObservationRecord,
    IngestionBatchRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    SourceArtifactRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.classification_reference import ClassificationReferenceService
from mf_strategy_tester.services.heatmap import HeatmapService


def _database(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'heatmap.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
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
        source_type="nav",
        source_url="https://example.invalid/nav",
        final_url="https://example.invalid/nav",
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
    classification = "Open Ended Schemes ( Equity Scheme - Large Cap Fund )"
    for scheme_code, end_value in (("100001", "110"), ("100002", "120")):
        session.add(
            SchemeOptionRecord(
                amfi_scheme_code=scheme_code,
                first_observed_nav_date=date(2021, 1, 1),
                last_observed_nav_date=date(2022, 1, 1),
                first_observed_batch_id=batch.id,
            )
        )
        session.flush()
        metadata = SchemeMetadataVersionRecord(
            id=scheme_code.ljust(64, "m"),
            amfi_scheme_code=scheme_code,
            scheme_name=f"Example {scheme_code} Direct Growth",
            fund_house_name="Example Mutual Fund",
            scheme_classification=classification,
            plan_type="direct",
            option_type="growth",
            classification_method="test",
            first_observed_batch_id=batch.id,
        )
        session.add(metadata)
        session.flush()
        for sequence, (nav_date, nav_value) in enumerate(
            ((date(2021, 1, 1), "100"), (date(2022, 1, 1), end_value)), start=1
        ):
            identifier = f"{scheme_code}-nav-{sequence}"
            session.add(
                NavRevisionRecord(
                    id=identifier,
                    amfi_scheme_code=scheme_code,
                    nav_date=nav_date,
                    nav_value=Decimal(nav_value),
                    metadata_version_id=metadata.id,
                    revision_number=1,
                    content_signature=identifier.ljust(64, "0"),
                    quality_status="valid",
                    is_current=True,
                    first_observed_batch_id=batch.id,
                )
            )
    session.add(
        NavDatasetStatsRecord(
            id=1,
            scheme_options=2,
            valid_current_rows=4,
            error_current_rows=0,
            earliest_valid_nav_date=date(2021, 1, 1),
            latest_valid_nav_date=date(2022, 1, 1),
        )
    )
    ClassificationReferenceService(session).ensure_approved_aliases((classification,))
    for instrument_type, suffix, end_close in (
        ("gross_total_return_index", "gtr", "110"),
        ("net_total_return_index", "ntr", "105"),
        ("price_index", "price", "110"),
    ):
        instrument = BenchmarkInstrumentRecord(
            id=f"nifty-50-{suffix}",
            provider="nifty_indices",
            instrument_type=instrument_type,
            source_identifier="NIFTY 50",
            display_name={
                "gtr": "Nifty 50 TRI",
                "ntr": "Nifty 50 NTR",
                "price": "Nifty 50",
            }[suffix],
            benchmark_family="Nifty 50",
            currency="INR",
            first_observed_batch_id=batch.id,
        )
        session.add(instrument)
        for sequence, (observation_date, close) in enumerate(
            ((date(2021, 1, 1), "100"), (date(2022, 1, 1), end_close)), start=1
        ):
            identifier = f"{suffix}-observation-{sequence}"
            session.add(
                BenchmarkObservationRecord(
                    id=identifier,
                    benchmark_instrument_id=instrument.id,
                    exchange=None,
                    observation_date=observation_date,
                    close_value=Decimal(close),
                    identity_status="official",
                    content_signature=identifier.ljust(64, "0"),
                    first_observed_batch_id=batch.id,
                    observed_at=datetime(2022, 1, sequence, tzinfo=UTC),
                )
            )
    session.commit()
    return session


def test_fund_trailing_heatmap_uses_median_direct_growth_return(tmp_path: Path) -> None:
    session = _database(tmp_path)

    result = HeatmapService(session).calculate(
        universe="funds",
        period="1y",
        plan_type="direct",
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
    )

    assert result.mode == "trailing"
    assert result.requested_as_of_date == date(2022, 1, 1)
    assert result.target_start_date == date(2021, 1, 1)
    assert result.metric == "median_constituent_return_pct"
    assert len(result.tiles) == 1
    tile = result.tiles[0]
    assert tile.label == "Large Cap Fund"
    assert tile.value_pct == Decimal("15.0")
    assert tile.minimum_constituent_pct == Decimal("10.0")
    assert tile.maximum_constituent_pct == Decimal("20.0")
    assert tile.candidate_count == tile.constituent_count == tile.sample_count == 2
    assert tile.excluded_count == 0
    assert tile.excluded_stale_endpoint == 0
    assert tile.excluded_insufficient_history == 0
    assert tile.return_basis == "nav_only"
    session.close()


def test_fund_rolling_heatmap_medians_constituent_medians_and_counts_samples(
    tmp_path: Path,
) -> None:
    session = _database(tmp_path)

    result = HeatmapService(session).calculate(
        universe="funds",
        period="rolling_1y",
        plan_type="direct",
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
    )

    assert result.mode == "rolling"
    assert result.target_start_date is None
    assert result.metric == "median_rolling_annualized_return_pct"
    assert result.observation_frequency == "last valid NAV observation in each calendar month"
    tile = result.tiles[0]
    assert tile.value_pct == Decimal("15.0")
    assert tile.constituent_count == 2
    assert tile.sample_count == 2
    assert tile.period_start_date_min == tile.period_start_date_max == date(2021, 1, 1)
    assert tile.period_end_date_min == tile.period_end_date_max == date(2022, 1, 1)
    session.close()


def test_benchmark_and_index_heatmaps_keep_total_and_price_return_bases_separate(
    tmp_path: Path,
) -> None:
    session = _database(tmp_path)

    benchmark = HeatmapService(session).calculate(
        universe="benchmarks",
        period="1y",
        plan_type="direct",
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
    )
    index = HeatmapService(session).calculate(
        universe="indices",
        period="rolling_1y",
        plan_type="direct",
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
    )

    assert [tile.return_basis for tile in benchmark.tiles] == [
        "gross_total_return",
        "net_total_return",
    ]
    assert benchmark.tiles[0].value_pct == Decimal("10.0")
    assert benchmark.tiles[1].value_pct == Decimal("5.00")
    assert [tile.return_basis for tile in index.tiles] == ["price"]
    assert index.tiles[0].value_pct == Decimal("10.0")
    assert index.tiles[0].sample_count == 1
    session.close()


def test_heatmap_route_serializes_typed_calculation_metadata(tmp_path: Path) -> None:
    session = _database(tmp_path)

    response = get_heatmap(
        session=session,
        universe="funds",
        period="1m",
        plan_type="direct",
        as_of=date(2022, 1, 1),
        endpoint_tolerance_days=7,
    )

    assert response.universe == "funds"
    assert response.day_count_convention == "actual/365"
    assert response.distribution_treatment.startswith("fund IDCW excluded")
    assert response.tiles[0].classification_mapping_version is not None
    session.close()
