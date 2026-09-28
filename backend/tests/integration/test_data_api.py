from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.main import create_app
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    IngestionBatchRecord,
    NavSyncCheckpointRecord,
    NavSyncCoverageRecord,
)
from mf_strategy_tester.db.session import create_database_engine, get_session
from mf_strategy_tester.ingestion.amfi import AmfiNavParser, FundSourceRecord, NavSourceRecord
from mf_strategy_tester.services.classification_reference import classification_definition
from mf_strategy_tester.services.nav_publication import FundCatalogPublisher, NormalizedNavPublisher
from mf_strategy_tester.services.nav_sync import EARLIEST_AMFI_NAV_DATE
from mf_strategy_tester.services.screener_classification_alias import singleton_alias_id

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"


@pytest.fixture
def populated_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'populated.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with session_factory() as session:
        batch = IngestionBatchRecord(
            provider="amfi",
            source_type="current_nav",
            source_url="https://example.invalid/NAVAll.txt",
            request_parameters={},
            parser_version="test",
            status="completed",
        )
        session.add(batch)
        session.commit()
        FundCatalogPublisher(session).publish(
            (FundSourceRecord("3", "Aditya Birla Sun Life Mutual Fund"),),
            batch_id=batch.id,
        )
        parsed_records = AmfiNavParser().parse_records((FIXTURES / "nav_current.txt").read_bytes())
        source_growth = parsed_records[1]
        assert isinstance(source_growth, NavSourceRecord)
        records = (
            *parsed_records,
            NavSourceRecord(
                scheme_code=source_growth.scheme_code,
                scheme_name=source_growth.scheme_name,
                isin_payout_or_growth=source_growth.isin_payout_or_growth,
                isin_reinvestment=source_growth.isin_reinvestment,
                nav=Decimal("350"),
                nav_date=date(2025, 8, 14),
                scheme_classification=source_growth.scheme_classification,
                fund_house=source_growth.fund_house,
            ),
            NavSourceRecord(
                scheme_code="119552",
                scheme_name="Test Banking Fund - Direct Plan-Growth",
                isin_payout_or_growth=None,
                isin_reinvestment=None,
                nav=Decimal("12"),
                nav_date=date(2026, 8, 14),
                scheme_classification=source_growth.scheme_classification,
                fund_house=source_growth.fund_house,
            ),
            NavSourceRecord(
                scheme_code="143212",
                scheme_name="Test Interval Fund - Direct Plan-Growth",
                isin_payout_or_growth=None,
                isin_reinvestment=None,
                nav=Decimal("10.4201"),
                nav_date=date(2019, 10, 11),
                scheme_classification="Interval Fund Schemes ( Growth )",
                fund_house=source_growth.fund_house,
            ),
        )
        NormalizedNavPublisher(session).publish(records, batch_id=batch.id)
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        session.add_all(
            [
                NavSyncCheckpointRecord(
                    mutual_fund_id="3",
                    completed_through=today,
                    latest_nav_date_found=records[0].nav_date,
                    last_batch_id=batch.id,
                ),
                NavSyncCoverageRecord(
                    mutual_fund_id="3",
                    start_date=EARLIEST_AMFI_NAV_DATE,
                    end_date=today,
                    last_batch_id=batch.id,
                ),
            ]
        )
        session.commit()

    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def test_data_coverage_has_explicit_empty_state(client: TestClient) -> None:
    response = client.get("/api/v1/data/coverage")

    assert response.status_code == 200
    assert response.json() == {
        "active_funds": 0,
        "fully_covered_funds": 0,
        "scheme_options": 0,
        "valid_nav_rows": 0,
        "quarantined_nav_rows": 0,
        "earliest_nav_date": None,
        "latest_nav_date": None,
        "latest_sync_run": None,
    }


def test_scheme_browser_filters_latest_amfi_scheme_options(
    populated_client: TestClient,
) -> None:
    client = populated_client
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    fund_houses = client.get("/api/v1/data/fund-houses")
    assert fund_houses.status_code == 200
    assert fund_houses.json() == [
        {
            "mutual_fund_id": "3",
            "name": "Aditya Birla Sun Life Mutual Fund",
            "is_active": True,
            "scheme_options": 4,
            "completed_through": today.isoformat(),
            "latest_nav_date_found": "2026-08-14",
            "fully_covered": True,
        }
    ]

    categories = client.get("/api/v1/data/fund-houses/3/categories")
    assert categories.status_code == 200
    assert categories.json() == [
        {
            "classification_id": singleton_alias_id(
                classification_definition("Interval Fund Schemes ( Growth )").classification_id
            ),
            "classification": "Growth",
            "structure_type": "interval",
            "scheme_options": 1,
        },
        {
            "classification_id": "screener-open-debt-banking-psu",
            "classification": "Banking and PSU Debt",
            "structure_type": "open_ended",
            "scheme_options": 3,
        },
    ]

    schemes = client.get(
        "/api/v1/data/schemes",
        params={"fund_house_id": "3", "plan_type": "direct", "option_type": "growth"},
    )
    assert schemes.status_code == 200
    payload = schemes.json()
    assert payload["total"] == 3
    assert payload["items"][0]["amfi_scheme_code"] == "119550"
    assert payload["items"][0]["latest_nav_value"] == "405.1308"
    assert payload["items"][0]["quality_status"] == "valid"

    no_regular_schemes = client.get(
        "/api/v1/data/schemes",
        params={"fund_house_id": "3", "plan_type": "regular"},
    )
    assert no_regular_schemes.status_code == 200
    assert no_regular_schemes.json()["total"] == 0


def test_scheme_browser_rejects_unknown_fund_house(client: TestClient) -> None:
    response = client.get("/api/v1/data/schemes", params={"fund_house_id": "999"})

    assert response.status_code == 404
    assert response.json() == {"detail": "AMFI fund house not found"}


def test_screener_exposes_cross_fund_house_classifications_and_history_exclusions(
    populated_client: TestClient,
) -> None:
    client = populated_client
    source_classification = "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)"
    display_classification = "Banking and PSU Debt"
    alias_id = "screener-open-debt-banking-psu"

    classifications = client.get(
        "/api/v1/data/classifications",
        params={"plan_type": "direct", "option_type": "growth"},
    )
    assert classifications.status_code == 200
    assert classifications.json() == [
        {
            "classification_id": alias_id,
            "classification": display_classification,
            "structure_type": "open_ended",
            "product_type": "mutual_fund",
            "candidate_options": 2,
            "eligible_options": 1,
            "excluded_options": 1,
            "mapping_version": f"{alias_id}:v1",
        }
    ]
    no_matching_fund_house = client.get(
        "/api/v1/data/classifications",
        params={
            "plan_type": "direct",
            "option_type": "growth",
            "fund_house": "No Such Fund House",
        },
    )
    assert no_matching_fund_house.status_code == 200
    assert no_matching_fund_house.json() == []

    response = client.get(
        "/api/v1/data/screener",
        params={"classification": source_classification, "horizon": "1y"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification_id"] == alias_id
    assert payload["classification"] == display_classification
    assert payload["classification_mapping_version"] == f"{alias_id}:v1"
    assert [item["amfi_scheme_code"] for item in payload["items"]] == ["119550"]
    assert payload["candidate_options"] == 2
    assert payload["excluded_insufficient_history"] == 1
    assert payload["excluded_stale_endpoint"] == 0
    assert payload["exclusion_limit"] == 25
    assert payload["exclusion_offset"] == 0
    assert payload["excluded_items"] == [
        {
            "amfi_scheme_code": "119552",
            "scheme_name": "Test Banking Fund - Direct Plan-Growth",
            "fund_house_name": "Aditya Birla Sun Life Mutual Fund",
            "scheme_classification": display_classification,
            "plan_type": "direct",
            "option_type": "growth",
            "isin": None,
            "reason": "insufficient_history",
            "reason_detail": ("No valid start NAV exists from 2025-08-14 through 2025-08-21"),
            "first_nav_date": "2026-08-14",
            "latest_nav_date": "2026-08-14",
            "latest_nav": "12",
            "endpoint_staleness_days": 0,
        }
    ]
    assert payload["return_basis"] == "nav_only"
    assert payload["distribution_treatment"] == "excluded"

    beyond_exclusions = client.get(
        "/api/v1/data/screener",
        params={
            "classification": source_classification,
            "horizon": "1y",
            "exclusion_limit": 1,
            "exclusion_offset": 1,
        },
    )
    assert beyond_exclusions.status_code == 200
    assert beyond_exclusions.json()["excluded_items"] == []

    before_second_option_inception = client.get(
        "/api/v1/data/screener",
        params={
            "classification": source_classification,
            "horizon": "1y",
            "as_of": "2025-08-14",
        },
    )
    assert before_second_option_inception.status_code == 200
    historical_payload = before_second_option_inception.json()
    assert historical_payload["candidate_options"] == 2
    assert historical_payload["excluded_stale_endpoint"] == 1
    stale_option = next(
        item
        for item in historical_payload["excluded_items"]
        if item["amfi_scheme_code"] == "119552"
    )
    assert stale_option["reason"] == "stale_endpoint"
    assert stale_option["latest_nav_date"] is None
    assert stale_option["latest_nav"] is None


def test_heatmap_api_exposes_trailing_and_rolling_classification_evidence(
    populated_client: TestClient,
) -> None:
    trailing = populated_client.get(
        "/api/v1/data/heatmap",
        params={
            "universe": "funds",
            "period": "1y",
            "plan_type": "direct",
            "as_of": "2026-08-14",
        },
    )

    assert trailing.status_code == 200
    payload = trailing.json()
    assert payload["mode"] == "trailing"
    assert payload["metric"] == "median_constituent_return_pct"
    assert payload["requested_as_of_date"] == "2026-08-14"
    assert payload["target_start_date"] == "2025-08-14"
    tile = payload["tiles"][0]
    assert tile["tile_id"] == "screener-open-debt-banking-psu"
    assert tile["return_basis"] == "nav_only"
    assert tile["candidate_count"] == 2
    assert tile["constituent_count"] == 1
    assert tile["excluded_count"] == 1
    assert tile["excluded_stale_endpoint"] == 0
    assert tile["excluded_insufficient_history"] == 1
    assert tile["period_start_date_min"] == "2025-08-14"
    assert tile["period_end_date_max"] == "2026-08-14"

    rolling = populated_client.get(
        "/api/v1/data/heatmap",
        params={
            "universe": "funds",
            "period": "rolling_1y",
            "as_of": "2026-08-14",
        },
    )
    assert rolling.status_code == 200
    rolling_payload = rolling.json()
    assert rolling_payload["mode"] == "rolling"
    assert rolling_payload["target_start_date"] is None
    assert rolling_payload["tiles"][0]["sample_count"] == 1
    assert "calendar anniversary" in rolling_payload["rolling_start_rule"]


def test_heatmap_api_validates_period_and_reports_missing_data(client: TestClient) -> None:
    invalid = client.get("/api/v1/data/heatmap", params={"period": "2y"})
    assert invalid.status_code == 422

    empty = client.get("/api/v1/data/heatmap")
    assert empty.status_code == 404
    assert empty.json() == {"detail": "No valid NAV dataset is available"}


def test_classification_alias_management_is_versioned_and_retains_amfi_labels(
    populated_client: TestClient,
) -> None:
    alias_id = "screener-open-debt-banking-psu"
    source_label = "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)"
    classification_id = classification_definition(source_label).classification_id

    initial = populated_client.get("/api/v1/data/classification-aliases")
    assert initial.status_code == 200
    payload = initial.json()
    managed_alias = next(item for item in payload["aliases"] if item["id"] == alias_id)
    assert managed_alias == {
        "id": alias_id,
        "name": "Banking and PSU Debt",
        "structure_type": "open_ended",
        "status": "active",
        "version": 1,
        "classification_ids": [classification_id],
        "updated_at": managed_alias["updated_at"],
    }
    source = next(
        item
        for item in payload["source_classifications"]
        if item["classification_id"] == classification_id
    )
    assert source["raw_labels"] == [source_label]
    assert source["alias_id"] == alias_id
    initial_revision = next(
        item
        for item in payload["revisions"]
        if item["alias_id"] == alias_id and item["version"] == 1
    )
    assert initial_revision["reason"] == ("Default reviewed AMFI predecessor/successor mapping")

    request = {
        "name": "Banking and PSU",
        "structure_type": "open_ended",
        "status": "active",
        "classification_ids": [classification_id],
        "reason": "Use a shorter local screener label",
        "expected_version": 1,
    }
    updated = populated_client.put(f"/api/v1/data/classification-aliases/{alias_id}", json=request)
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["name"] == "Banking and PSU"

    stale = populated_client.put(f"/api/v1/data/classification-aliases/{alias_id}", json=request)
    assert stale.status_code == 409
    assert "changed from version 1 to 2" in stale.json()["detail"]

    deactivated = populated_client.put(
        f"/api/v1/data/classification-aliases/{alias_id}",
        json={
            **request,
            "status": "inactive",
            "classification_ids": [],
            "reason": "Release this classification for a replacement alias",
            "expected_version": 2,
        },
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert deactivated.json()["classification_ids"] == []
    assert deactivated.json()["version"] == 3

    released = populated_client.get("/api/v1/data/classification-aliases")
    assert released.status_code == 200
    released_payload = released.json()
    released_source = next(
        item
        for item in released_payload["source_classifications"]
        if item["classification_id"] == classification_id
    )
    assert released_source["alias_id"] is None
    released_revision = next(
        item
        for item in released_payload["revisions"]
        if item["alias_id"] == alias_id and item["version"] == 3
    )
    assert released_revision["status"] == "inactive"
    assert released_revision["classification_ids"] == []

    replacement = populated_client.post(
        "/api/v1/data/classification-aliases",
        json={
            "name": "Banking and PSU replacement",
            "structure_type": "open_ended",
            "status": "active",
            "classification_ids": [classification_id],
            "reason": "Assign the released classification after local review",
        },
    )
    assert replacement.status_code == 201
    assert replacement.json()["classification_ids"] == [classification_id]

    blank_reason = populated_client.put(
        f"/api/v1/data/classification-aliases/{replacement.json()['id']}",
        json={
            "name": "Banking and PSU replacement",
            "structure_type": "open_ended",
            "status": "active",
            "classification_ids": [classification_id],
            "reason": "   ",
            "expected_version": 1,
        },
    )
    assert blank_reason.status_code == 422
    assert "change reason must contain at least 3 characters" in blank_reason.json()["detail"]


def test_fund_comparison_normalizes_nav_without_inventing_distribution_cash_flows(
    populated_client: TestClient,
) -> None:
    response = populated_client.get(
        "/api/v1/data/fund-comparison",
        params={
            "scheme_code": "119550",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["normalization_base"] == "first_observation_100"
    assert payload["distribution_markers"] == "record_date"
    assert payload["series"][0]["points"] == [
        {
            "nav_date": "2026-08-14",
            "nav_value": "405.1308",
            "normalized_value": "100",
        }
    ]
    assert payload["series"][0]["distributions"] == []
