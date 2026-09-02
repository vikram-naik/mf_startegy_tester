from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median

from mf_strategy_tester.services.nav_performance import NavPoint

MONTHLY_MINIMUM_MONTHS = 48
MONTHLY_MINIMUM_MONTHS_PER_YEAR = 8
QUARTERLY_MINIMUM_MONTHS = 16
QUARTERLY_MINIMUM_MONTHS_PER_YEAR = 3
NAV_TOLERANCE_DAYS = 7

_MONTHLY_PATTERN = re.compile(r"\bmonth(?:ly)?\b", re.IGNORECASE)
_QUARTERLY_PATTERN = re.compile(r"\bquarter(?:ly)?\b", re.IGNORECASE)
_OTHER_FREQUENCY_PATTERN = re.compile(
    r"\b(?:daily|weekly|fortnightly|annual|annually|half[ -]?yearly|semi[ -]?annual)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DistributionPoint:
    record_date: date
    amount_per_unit_inr: Decimal


@dataclass(frozen=True)
class PayoutFrequencyAssessment:
    frequency: str | None
    basis: str
    distinct_payout_months: int
    annual_block_month_counts: tuple[int, ...]
    event_count: int
    median_gap_days: Decimal | None


@dataclass(frozen=True)
class PayoutYield:
    window_start_exclusive: date
    window_end_inclusive: date
    payout_amount_per_unit_inr: Decimal
    nav_date: date | None
    nav_value: Decimal | None
    yield_pct: Decimal | None


def assess_payout_frequency(
    scheme_name: str,
    event_dates: tuple[date, ...],
    *,
    period_start: date,
    period_end: date,
) -> PayoutFrequencyAssessment:
    """Classify a near-consistent monthly or quarterly cash distribution history."""

    if period_end < period_start:
        raise ValueError("payout-frequency period end must not precede its start")
    dates = tuple(sorted({value for value in event_dates if period_start <= value <= period_end}))
    payout_months = {(value.year, value.month) for value in dates}
    block_counts = tuple(
        len({(value.year, value.month) for value in dates if start <= value <= end})
        for start, end in _annual_blocks(period_start, period_end)
    )
    gaps = tuple((current - previous).days for previous, current in pairwise(dates))
    median_gap = Decimal(str(median(gaps))) if gaps else None

    explicit_frequency: str | None = None
    if _MONTHLY_PATTERN.search(scheme_name):
        explicit_frequency = "monthly"
    elif _QUARTERLY_PATTERN.search(scheme_name):
        explicit_frequency = "quarterly"
    elif _OTHER_FREQUENCY_PATTERN.search(scheme_name):
        return PayoutFrequencyAssessment(
            frequency=None,
            basis="explicit_other_frequency",
            distinct_payout_months=len(payout_months),
            annual_block_month_counts=block_counts,
            event_count=len(dates),
            median_gap_days=median_gap,
        )

    monthly_consistent = len(payout_months) >= MONTHLY_MINIMUM_MONTHS and all(
        count >= MONTHLY_MINIMUM_MONTHS_PER_YEAR for count in block_counts
    )
    quarterly_consistent = len(payout_months) >= QUARTERLY_MINIMUM_MONTHS and all(
        count >= QUARTERLY_MINIMUM_MONTHS_PER_YEAR for count in block_counts
    )

    if explicit_frequency == "monthly":
        frequency = "monthly" if monthly_consistent else None
        basis = "scheme_name_monthly" if frequency else "monthly_consistency_failed"
    elif explicit_frequency == "quarterly":
        frequency = "quarterly" if quarterly_consistent else None
        basis = "scheme_name_quarterly" if frequency else "quarterly_consistency_failed"
    elif monthly_consistent and median_gap is not None and Decimal("20") <= median_gap <= 45:
        frequency = "monthly"
        basis = "observed_monthly_cadence"
    elif quarterly_consistent and median_gap is not None and Decimal("60") <= median_gap <= 120:
        frequency = "quarterly"
        basis = "observed_quarterly_cadence"
    else:
        frequency = None
        basis = "no_supported_monthly_or_quarterly_cadence"

    return PayoutFrequencyAssessment(
        frequency=frequency,
        basis=basis,
        distinct_payout_months=len(payout_months),
        annual_block_month_counts=block_counts,
        event_count=len(dates),
        median_gap_days=median_gap,
    )


def calculate_trailing_payout_yield(
    distributions: tuple[DistributionPoint, ...],
    nav_points: tuple[NavPoint, ...],
    *,
    window_end: date,
    nav_tolerance_days: int = NAV_TOLERANCE_DAYS,
) -> PayoutYield:
    """Calculate trailing cash distributions divided by NAV on or before the window end."""

    if nav_tolerance_days < 0:
        raise ValueError("NAV tolerance days must not be negative")
    window_start = _anniversary(window_end, 1)
    payout_amount = sum(
        (
            point.amount_per_unit_inr
            for point in distributions
            if window_start < point.record_date <= window_end
        ),
        Decimal(0),
    )
    nav = next((point for point in reversed(nav_points) if point.nav_date <= window_end), None)
    if nav is None or window_end - nav.nav_date > timedelta(days=nav_tolerance_days):
        return PayoutYield(window_start, window_end, payout_amount, None, None, None)
    if nav.nav_value <= 0:
        raise ValueError(f"NAV must be positive on {nav.nav_date.isoformat()}")
    return PayoutYield(
        window_start_exclusive=window_start,
        window_end_inclusive=window_end,
        payout_amount_per_unit_inr=payout_amount,
        nav_date=nav.nav_date,
        nav_value=nav.nav_value,
        yield_pct=payout_amount / nav.nav_value * Decimal("100"),
    )


def annual_yield_cutoffs(as_of: date, *, years: int = 5) -> tuple[date, ...]:
    if years <= 0:
        raise ValueError("annual yield years must be positive")
    return tuple(_anniversary(as_of, offset) for offset in reversed(range(years)))


def frequency_period_start(as_of: date, *, months: int = 60) -> date:
    """Return the first day of the calendar month completing ``months`` observed months."""

    if months <= 0:
        raise ValueError("frequency-period months must be positive")
    month_index = as_of.year * 12 + as_of.month - months
    return date(month_index // 12, month_index % 12 + 1, 1)


def _annual_blocks(period_start: date, period_end: date) -> tuple[tuple[date, date], ...]:
    blocks: list[tuple[date, date]] = []
    block_start = period_start
    while block_start <= period_end:
        next_start = _add_year(block_start)
        blocks.append((block_start, min(period_end, next_start - timedelta(days=1))))
        block_start = next_start
    return tuple(blocks)


def _add_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def _anniversary(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)
