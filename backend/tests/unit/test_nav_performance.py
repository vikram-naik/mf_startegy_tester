from datetime import date
from decimal import Decimal

import pytest

from mf_strategy_tester.services.nav_performance import (
    NavPoint,
    calculate_nav_performance,
)


def test_nav_performance_uses_actual_elapsed_days_and_nav_only_returns() -> None:
    points = (
        NavPoint(date(2020, 1, 1), Decimal("100")),
        NavPoint(date(2021, 1, 1), Decimal("110")),
        NavPoint(date(2022, 1, 1), Decimal("121")),
    )

    result = calculate_nav_performance(points, rolling_windows_years=(1,))

    assert result.since_inception.elapsed_days == 731
    assert result.since_inception.total_return_pct == Decimal("21.00")
    assert result.since_inception.annualized_return_pct == pytest.approx(
        Decimal("9.9856587738"), abs=Decimal("0.0000001")
    )
    rolling = result.rolling_returns[0]
    assert rolling.sample_count == 2
    assert rolling.latest is not None
    assert rolling.latest.start_date == date(2021, 1, 1)
    assert rolling.latest.annualized_return_pct == Decimal("10.0")


def test_default_rolling_windows_include_ten_year_annualized_return() -> None:
    result = calculate_nav_performance(
        (
            NavPoint(date(2010, 1, 1), Decimal("100")),
            NavPoint(date(2020, 1, 1), Decimal("200")),
        )
    )

    assert [summary.window_years for summary in result.rolling_returns] == [1, 3, 5, 10]
    ten_year = result.rolling_returns[-1]
    assert ten_year.sample_count == 1
    assert ten_year.latest is not None
    assert ten_year.latest.start_date == date(2010, 1, 1)
    assert ten_year.latest.end_date == date(2020, 1, 1)
    assert ten_year.latest.annualized_return_pct == pytest.approx(
        Decimal("7.1732778930"), abs=Decimal("0.0000001")
    )


def test_rolling_returns_use_first_nav_after_anniversary_within_seven_days() -> None:
    result = calculate_nav_performance(
        (
            NavPoint(date(2020, 1, 1), Decimal("100")),
            NavPoint(date(2021, 1, 4), Decimal("120")),
            NavPoint(date(2022, 1, 1), Decimal("132")),
        ),
        rolling_windows_years=(1,),
    )

    rolling = result.rolling_returns[0]
    assert rolling.sample_count == 1
    assert rolling.latest is not None
    assert rolling.latest.start_date == date(2021, 1, 4)


def test_rolling_returns_reject_large_missing_nav_gap() -> None:
    result = calculate_nav_performance(
        (
            NavPoint(date(2020, 1, 20), Decimal("100")),
            NavPoint(date(2021, 1, 1), Decimal("110")),
        ),
        rolling_windows_years=(1,),
    )

    assert result.rolling_returns[0].sample_count == 0


def test_maximum_drawdown_retains_peak_and_trough_dates() -> None:
    result = calculate_nav_performance(
        (
            NavPoint(date(2020, 1, 1), Decimal("100")),
            NavPoint(date(2020, 2, 1), Decimal("120")),
            NavPoint(date(2020, 3, 1), Decimal("90")),
            NavPoint(date(2020, 4, 1), Decimal("130")),
        ),
        rolling_windows_years=(1,),
    )

    assert result.drawdown.maximum_drawdown_pct == Decimal("-25.00")
    assert result.drawdown.peak_date == date(2020, 2, 1)
    assert result.drawdown.trough_date == date(2020, 3, 1)


def test_performance_rejects_nonpositive_or_duplicate_observations() -> None:
    with pytest.raises(ValueError, match="positive"):
        calculate_nav_performance((NavPoint(date(2020, 1, 1), Decimal("0")),))
    with pytest.raises(ValueError, match="strictly increasing"):
        calculate_nav_performance(
            (
                NavPoint(date(2020, 1, 1), Decimal("100")),
                NavPoint(date(2020, 1, 1), Decimal("101")),
            )
        )
