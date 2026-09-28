from datetime import date
from decimal import Decimal

import pytest

from mf_strategy_tester.services.classification_reference import (
    canonical_scheme_classification,
    classification_definition,
    scheme_classification_product_type,
)
from mf_strategy_tester.services.fund_screener import (
    ScreenerHorizon,
    calculate_trailing_return,
    horizon_start_date,
    is_annualized_horizon,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Open Ended Schemes ( Equity Schemes - Large & Mid Cap Fund )",
            "Open Ended Schemes ( Equity Scheme - Large & Mid Cap Fund )",
        ),
        (
            "Open Ended Schemes(Hybrid Scheme - Aggressive Hybrid Fund)",
            "Open Ended Schemes ( Hybrid Scheme - Aggressive Hybrid Fund )",
        ),
        (
            "Open Ended Schemes ( Other Scheme - Other  ETFs )",
            "Open Ended Schemes ( Other Scheme - Other ETFs )",
        ),
    ],
)
def test_canonical_scheme_classification_collapses_presentation_drift(
    raw: str, expected: str
) -> None:
    assert canonical_scheme_classification(raw) == expected


def test_classification_reference_unites_aliases_without_merging_categories() -> None:
    singular = classification_definition(
        "Open Ended Schemes ( Equity Scheme - Large & Mid Cap Fund )"
    )
    plural = classification_definition(
        "Open Ended Schemes ( Equity Schemes - Large & Mid Cap Fund )"
    )
    large_cap = classification_definition("Open Ended Schemes ( Equity Scheme - Large Cap Fund )")

    assert singular.classification_id == plural.classification_id
    assert singular.display_name == plural.display_name
    assert singular.classification_id != large_cap.classification_id


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("Open Ended Schemes ( Equity Scheme - Large Cap Fund )", "mutual_fund"),
        ("Open Ended Schemes ( Index Funds - Equity Funds )", "index_fund"),
        ("Open Ended Schemes ( Other Scheme - Index Funds )", "index_fund"),
        ("Open Ended Schemes ( Exchange Traded Funds (ETFs) - Equity ETF )", "etf"),
        ("Open Ended Schemes ( GOLD ETFs )", "etf"),
    ],
)
def test_classification_product_type_is_an_explicit_navigation_facet(
    classification: str, expected: str
) -> None:
    assert scheme_classification_product_type(classification) == expected


@pytest.mark.parametrize(
    ("as_of", "horizon", "expected"),
    [
        (date(2026, 9, 2), "1m", date(2026, 8, 2)),
        (date(2026, 9, 2), "6m", date(2026, 3, 2)),
        (date(2024, 2, 29), "1y", date(2023, 2, 28)),
        (date(2026, 9, 2), "10y", date(2016, 9, 2)),
    ],
)
def test_horizon_start_date_uses_calendar_periods(
    as_of: date, horizon: ScreenerHorizon, expected: date
) -> None:
    assert horizon_start_date(as_of, horizon) == expected


def test_trailing_return_matches_hand_calculated_actual_365_cagr() -> None:
    total, annualized = calculate_trailing_return(
        start_date=date(2021, 1, 1),
        start_nav=Decimal("100"),
        end_date=date(2022, 1, 1),
        end_nav=Decimal("110"),
        annualize=True,
    )

    assert total == Decimal("10.0")
    assert annualized == Decimal("10.0")


def test_short_horizon_return_is_not_annualized() -> None:
    total, annualized = calculate_trailing_return(
        start_date=date(2026, 3, 2),
        start_nav=Decimal("100"),
        end_date=date(2026, 9, 2),
        end_nav=Decimal("108"),
        annualize=False,
    )

    assert total == Decimal("8.00")
    assert annualized is None
    assert is_annualized_horizon("6m") is False
    assert is_annualized_horizon("1y") is True


def test_trailing_return_rejects_invalid_endpoints() -> None:
    with pytest.raises(ValueError, match="positive"):
        calculate_trailing_return(
            start_date=date(2025, 1, 1),
            start_nav=Decimal("0"),
            end_date=date(2026, 1, 1),
            end_nav=Decimal("100"),
            annualize=True,
        )
