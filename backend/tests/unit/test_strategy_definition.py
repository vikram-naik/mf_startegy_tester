from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from mf_strategy_tester.domain.strategy import StrategyDefinition


def test_valid_strategy_document_round_trips(strategy_document: dict[str, Any]) -> None:
    definition = StrategyDefinition.model_validate(strategy_document)

    assert definition.selection.rank_by == ("momentum_12m",)
    assert definition.cash_flows.initial_investment.as_tuple().exponent == 0
    assert definition.model_dump(mode="json")["signals"][0]["weight"] == "1"


def test_selection_must_reference_a_declared_signal(strategy_document: dict[str, Any]) -> None:
    strategy_document["selection"]["rank_by"] = ["future_signal"]

    with pytest.raises(ValidationError, match="unknown signals"):
        StrategyDefinition.model_validate(strategy_document)


def test_fixed_weights_must_sum_exactly_to_one(strategy_document: dict[str, Any]) -> None:
    strategy_document["allocation"] = {
        "method": "fixed_weight",
        "maximum_weight": "1",
        "fixed_weights": {"scheme-1": "0.50", "scheme-2": "0.49"},
    }

    with pytest.raises(ValidationError, match="sum exactly to 1"):
        StrategyDefinition.model_validate(strategy_document)


def test_contribution_amount_requires_a_frequency(strategy_document: dict[str, Any]) -> None:
    strategy_document["cash_flows"]["contribution_frequency"] = "none"

    with pytest.raises(ValidationError, match="must either both be set"):
        StrategyDefinition.model_validate(strategy_document)


def test_fixed_portfolio_uses_stable_scheme_codes(strategy_document: dict[str, Any]) -> None:
    strategy_document["universe"]["scheme_codes"] = ["100001", "100002"]
    strategy_document["signals"] = []
    strategy_document["selection"]["rank_by"] = []
    strategy_document["selection"]["maximum_holdings"] = 2
    strategy_document["allocation"] = {
        "method": "fixed_weight",
        "maximum_weight": "0.60",
        "fixed_weights": {"100001": "0.60", "100002": "0.40"},
    }

    definition = StrategyDefinition.model_validate(strategy_document)

    assert definition.allocation.fixed_weights["100001"] == Decimal("0.60")
