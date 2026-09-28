from datetime import date, timedelta
from decimal import Decimal

import pytest

from mf_strategy_tester.services.idcw_income_research import (
    DistributionPoint,
    IDCWPayoutRankCandidate,
    annual_yield_cutoffs,
    assess_payout_frequency,
    calculate_payout_yield,
    calculate_trailing_payout_yield,
    frequency_period_start,
    rank_idcw_payouts,
)
from mf_strategy_tester.services.nav_performance import NavPoint


def _monthly_dates(start: date, count: int) -> tuple[date, ...]:
    values = []
    year = start.year
    month = start.month
    for _ in range(count):
        values.append(date(year, month, 15))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return tuple(values)


def test_explicit_monthly_frequency_allows_limited_missed_months() -> None:
    dates = tuple(
        value
        for index, value in enumerate(_monthly_dates(date(2021, 9, 1), 60))
        if index not in {4, 17, 29, 41, 53}
    )

    result = assess_payout_frequency(
        "Example Direct Plan Monthly IDCW",
        dates,
        period_start=date(2021, 9, 1),
        period_end=date(2026, 8, 30),
    )

    assert result.frequency == "monthly"
    assert result.basis == "scheme_name_monthly"
    assert result.distinct_payout_months == 55
    assert result.annual_block_month_counts == (11, 11, 11, 11, 11)


def test_explicit_quarterly_frequency_requires_each_year_to_remain_consistent() -> None:
    dates = tuple(date(year, month, 15) for year in range(2021, 2027) for month in (3, 6, 9, 12))
    result = assess_payout_frequency(
        "Example Direct Quarterly IDCW",
        dates,
        period_start=date(2021, 9, 1),
        period_end=date(2026, 8, 30),
    )
    assert result.frequency == "quarterly"
    assert result.distinct_payout_months == 20
    assert result.annual_block_month_counts == (4, 4, 4, 4, 4)

    inconsistent = assess_payout_frequency(
        "Example Direct Quarterly IDCW",
        tuple(value for value in dates if not date(2023, 9, 1) <= value <= date(2024, 8, 31)),
        period_start=date(2021, 9, 1),
        period_end=date(2026, 8, 30),
    )
    assert inconsistent.frequency is None
    assert inconsistent.basis == "quarterly_consistency_failed"


def test_generic_and_explicit_other_frequencies_are_not_misclassified() -> None:
    monthly_dates = _monthly_dates(date(2021, 9, 1), 60)
    generic = assess_payout_frequency(
        "Example Direct IDCW",
        monthly_dates,
        period_start=date(2021, 9, 1),
        period_end=date(2026, 8, 30),
    )
    assert generic.frequency == "monthly"
    assert generic.basis == "observed_monthly_cadence"

    daily = assess_payout_frequency(
        "Example Direct Daily IDCW",
        tuple(date(2021, 9, 1) + timedelta(days=index) for index in range(365 * 5)),
        period_start=date(2021, 9, 1),
        period_end=date(2026, 8, 30),
    )
    assert daily.frequency is None
    assert daily.basis == "explicit_other_frequency"


def test_trailing_yield_uses_cash_amount_and_recent_nav_without_total_return_inference() -> None:
    result = calculate_trailing_payout_yield(
        (
            DistributionPoint(date(2025, 8, 30), Decimal("9")),
            DistributionPoint(date(2025, 9, 30), Decimal("1")),
            DistributionPoint(date(2026, 3, 31), Decimal("2")),
            DistributionPoint(date(2026, 8, 30), Decimal("3")),
        ),
        (
            NavPoint(date(2026, 8, 27), Decimal("100")),
            NavPoint(date(2026, 8, 31), Decimal("101")),
        ),
        window_end=date(2026, 8, 30),
    )

    assert result.window_start_exclusive == date(2025, 8, 30)
    assert result.payout_amount_per_unit_inr == Decimal("6")
    assert result.nav_date == date(2026, 8, 27)
    assert result.yield_pct == Decimal("6")


def test_trailing_yield_is_unavailable_when_nav_is_stale() -> None:
    result = calculate_trailing_payout_yield(
        (DistributionPoint(date(2026, 8, 1), Decimal("1")),),
        (NavPoint(date(2026, 8, 20), Decimal("10")),),
        window_end=date(2026, 8, 30),
    )
    assert result.yield_pct is None


def test_payout_yield_uses_endpoint_nav_without_float_conversion() -> None:
    assert calculate_payout_yield(
        payout_amount_per_unit_inr=Decimal("7.5"), nav_value=Decimal("125")
    ) == Decimal("6.00")

    with pytest.raises(ValueError, match="must not be negative"):
        calculate_payout_yield(
            payout_amount_per_unit_inr=Decimal("-0.01"), nav_value=Decimal("125")
        )


def test_idcw_rank_equally_weights_yield_and_payout_frequency_percentiles() -> None:
    result = rank_idcw_payouts(
        (
            IDCWPayoutRankCandidate("high-yield-rare", Decimal("8"), 1),
            IDCWPayoutRankCandidate("balanced", Decimal("7"), 12),
            IDCWPayoutRankCandidate("low-yield-quarterly", Decimal("4"), 4),
        )
    )

    assert [item.scheme_code for item in result] == [
        "balanced",
        "high-yield-rare",
        "low-yield-quarterly",
    ]
    assert result[0].payout_yield_rank == 2
    assert result[0].payout_frequency_rank == 1
    assert result[0].combined_score == Decimal("75")
    assert result[1].combined_score == Decimal("50")


def test_idcw_rank_preserves_component_ties_and_stable_scheme_code_order() -> None:
    result = rank_idcw_payouts(
        (
            IDCWPayoutRankCandidate("200", Decimal("6"), 4),
            IDCWPayoutRankCandidate("100", Decimal("6"), 4),
        )
    )

    assert [item.scheme_code for item in result] == ["100", "200"]
    assert all(item.payout_yield_rank == 1 for item in result)
    assert all(item.payout_frequency_rank == 1 for item in result)
    assert all(item.combined_score == Decimal("100") for item in result)


def test_cutoffs_and_frequency_start_are_calendar_stable() -> None:
    assert annual_yield_cutoffs(date(2024, 2, 29), years=3) == (
        date(2022, 2, 28),
        date(2023, 2, 28),
        date(2024, 2, 29),
    )
    assert frequency_period_start(date(2026, 8, 30)) == date(2021, 9, 1)
    with pytest.raises(ValueError, match="positive"):
        frequency_period_start(date(2026, 8, 30), months=0)
