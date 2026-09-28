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


@dataclass(frozen=True)
class IDCWPayoutRankCandidate:
    scheme_code: str
    payout_yield_pct: Decimal
    payout_event_count: int


@dataclass(frozen=True)
class IDCWPayoutRank:
    scheme_code: str
    payout_yield_rank: int
    payout_frequency_rank: int
    combined_score: Decimal


IDCW_RANKING_METHOD = (
    "Equal-weight percentile score: 50% trailing payout-yield rank and 50% trailing "
    "payout-event-frequency rank; higher score ranks first, then payout yield, event count, "
    "and AMFI scheme code"
)


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
    return PayoutYield(
        window_start_exclusive=window_start,
        window_end_inclusive=window_end,
        payout_amount_per_unit_inr=payout_amount,
        nav_date=nav.nav_date,
        nav_value=nav.nav_value,
        yield_pct=calculate_payout_yield(
            payout_amount_per_unit_inr=payout_amount,
            nav_value=nav.nav_value,
        ),
    )


def calculate_payout_yield(
    *, payout_amount_per_unit_inr: Decimal, nav_value: Decimal
) -> Decimal:
    """Return cash distributions per unit as a percentage of endpoint NAV."""

    if payout_amount_per_unit_inr < 0:
        raise ValueError("payout amount must not be negative")
    if nav_value <= 0:
        raise ValueError("NAV must be positive")
    return payout_amount_per_unit_inr / nav_value * Decimal("100")


def rank_idcw_payouts(
    candidates: tuple[IDCWPayoutRankCandidate, ...],
) -> tuple[IDCWPayoutRank, ...]:
    """Rank IDCW options by equally weighted yield and payout-frequency percentiles.

    Component ranks use competition ranking, so equal values receive equal ranks. The score is a
    preference score on a 0-100 scale, not a return or prediction.
    """

    if len({candidate.scheme_code for candidate in candidates}) != len(candidates):
        raise ValueError("IDCW payout rank candidates must have unique scheme codes")
    if any(candidate.payout_yield_pct < 0 for candidate in candidates):
        raise ValueError("IDCW payout rank candidate yields must not be negative")
    if any(candidate.payout_event_count <= 0 for candidate in candidates):
        raise ValueError("IDCW payout rank candidate event counts must be positive")
    if not candidates:
        return ()

    count = len(candidates)
    denominator = Decimal(max(count - 1, 1))
    rankings = []
    for candidate in candidates:
        yield_rank = 1 + sum(
            other.payout_yield_pct > candidate.payout_yield_pct for other in candidates
        )
        frequency_rank = 1 + sum(
            other.payout_event_count > candidate.payout_event_count for other in candidates
        )
        if count == 1:
            yield_percentile = frequency_percentile = Decimal("100")
        else:
            yield_percentile = Decimal(count - yield_rank) / denominator * Decimal("100")
            frequency_percentile = (
                Decimal(count - frequency_rank) / denominator * Decimal("100")
            )
        rankings.append(
            IDCWPayoutRank(
                scheme_code=candidate.scheme_code,
                payout_yield_rank=yield_rank,
                payout_frequency_rank=frequency_rank,
                combined_score=(yield_percentile + frequency_percentile) / Decimal("2"),
            )
        )
    candidate_by_code = {candidate.scheme_code: candidate for candidate in candidates}
    return tuple(
        sorted(
            rankings,
            key=lambda ranking: (
                -ranking.combined_score,
                -candidate_by_code[ranking.scheme_code].payout_yield_pct,
                -candidate_by_code[ranking.scheme_code].payout_event_count,
                ranking.scheme_code,
            ),
        )
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
