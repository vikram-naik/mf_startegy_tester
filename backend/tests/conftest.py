from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.main import create_app
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.session import create_database_engine, get_session


@pytest.fixture
def strategy_document() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "name": "Direct growth momentum",
        "description": "Monthly top-three momentum research strategy.",
        "date_range": {"start": "2020-01-01", "end": "2025-12-31"},
        "universe": {
            "categories": ["Equity: Flexi Cap"],
            "plan": "direct",
            "option": "growth",
            "require_active_at_decision_time": True,
            "minimum_history_periods": 252,
        },
        "signals": [
            {
                "id": "momentum_12m",
                "metric": "trailing_return",
                "window_periods": 252,
                "direction": "descending",
                "weight": "1",
            }
        ],
        "selection": {"rank_by": ["momentum_12m"], "maximum_holdings": 3},
        "allocation": {"method": "equal_weight", "maximum_weight": "0.40"},
        "rebalance": {"frequency": "monthly", "calendar_day": 1},
        "execution": {
            "decision_time": "after_cutoff",
            "nav_lag_valuation_days": 1,
            "missing_nav_policy": "next_available",
            "maximum_nav_wait_days": 7,
        },
        "cash_flows": {
            "initial_investment": "100000",
            "contribution_amount": "10000",
            "contribution_frequency": "monthly",
        },
        "costs": {
            "exit_load_rate": "0",
            "transaction_cost_rate": "0",
            "taxes_included": False,
            "expense_ratio_already_reflected_in_nav": True,
        },
        "benchmark": {
            "identifier": "NIFTY_500_TRI",
            "series_type": "total_return",
            "currency": "INR",
        },
    }


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_path = tmp_path / "test.db"
    test_engine = create_database_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False, class_=Session)

    def override_session() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    test_engine.dispose()
