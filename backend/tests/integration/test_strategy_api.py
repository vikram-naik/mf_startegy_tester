from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient


def test_health_checks_database(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}


def test_ingestion_batch_list_is_paginated_and_empty_initially(client: TestClient) -> None:
    response = client.get("/api/v1/ingestion-batches?limit=10&offset=0")

    assert response.status_code == 200
    assert response.json() == []


def test_strategy_revisions_are_immutable(
    client: TestClient, strategy_document: dict[str, Any]
) -> None:
    create_response = client.post("/api/v1/strategies", json={"definition": strategy_document})

    assert create_response.status_code == 201
    created = create_response.json()
    strategy_id = created["id"]
    assert created["latest_version"] == 1
    created_at = datetime.fromisoformat(created["created_at"].replace("Z", "+00:00"))
    assert created_at.utcoffset() == timedelta(0)
    assert created["versions"][0]["definition"]["name"] == "Direct growth momentum"

    revision = deepcopy(strategy_document)
    revision["name"] = "Direct growth momentum v2"
    revision["selection"]["maximum_holdings"] = 5
    revise_response = client.post(
        f"/api/v1/strategies/{strategy_id}/versions", json={"definition": revision}
    )

    assert revise_response.status_code == 201
    assert revise_response.json()["version"] == 2

    detail_response = client.get(f"/api/v1/strategies/{strategy_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["latest_version"] == 2
    assert detail["name"] == "Direct growth momentum v2"
    assert detail["versions"][0]["definition"]["selection"]["maximum_holdings"] == 3
    assert detail["versions"][1]["definition"]["selection"]["maximum_holdings"] == 5


def test_invalid_strategy_returns_validation_details(
    client: TestClient, strategy_document: dict[str, Any]
) -> None:
    strategy_document["date_range"]["end"] = "2019-12-31"

    response = client.post("/api/v1/strategies", json={"definition": strategy_document})

    assert response.status_code == 422
    assert "end date must be after start date" in response.text


def test_missing_strategy_returns_404(
    client: TestClient, strategy_document: dict[str, Any]
) -> None:
    response = client.post(
        "/api/v1/strategies/not-present/versions", json={"definition": strategy_document}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Strategy not found"}
