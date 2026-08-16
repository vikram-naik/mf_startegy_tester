from collections.abc import Generator
from datetime import datetime
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
from mf_strategy_tester.ingestion.amfi import AmfiNavParser, FundSourceRecord
from mf_strategy_tester.services.nav_publication import FundCatalogPublisher, NormalizedNavPublisher
from mf_strategy_tester.services.nav_sync import EARLIEST_AMFI_NAV_DATE

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
        records = AmfiNavParser().parse_records((FIXTURES / "nav_current.txt").read_bytes())
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
            "scheme_options": 2,
            "completed_through": today.isoformat(),
            "latest_nav_date_found": "2026-08-14",
            "fully_covered": True,
        }
    ]

    categories = client.get("/api/v1/data/fund-houses/3/categories")
    assert categories.status_code == 200
    assert categories.json() == [
        {
            "classification": "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)",
            "scheme_options": 2,
        }
    ]

    schemes = client.get(
        "/api/v1/data/schemes",
        params={"fund_house_id": "3", "plan_type": "direct", "option_type": "growth"},
    )
    assert schemes.status_code == 200
    payload = schemes.json()
    assert payload["total"] == 1
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
