from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext

ROLLING_WINDOWS_YEARS = (1, 3, 5, 10)
ROLLING_START_TOLERANCE_DAYS = 7
ANNUALIZATION_DAYS = Decimal("365")
_ONE_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class NavPoint:
    nav_date: date
    nav_value: Decimal


@dataclass(frozen=True)
class NavReturn:
    start_date: date
    end_date: date
    start_nav: Decimal
    end_nav: Decimal
    elapsed_days: int
    total_return_pct: Decimal
    annualized_return_pct: Decimal | None


@dataclass(frozen=True)
class RollingReturnSummary:
    window_years: int
    sample_count: int
    first: NavReturn | None
    latest: NavReturn | None
    minimum_annualized_return_pct: Decimal | None
    median_annualized_return_pct: Decimal | None
    mean_annualized_return_pct: Decimal | None
    maximum_annualized_return_pct: Decimal | None
    positive_periods_pct: Decimal | None


@dataclass(frozen=True)
class DrawdownSummary:
    maximum_drawdown_pct: Decimal
    peak_date: date
    trough_date: date


@dataclass(frozen=True)
class NavPerformanceResult:
    observation_count: int
    since_inception: NavReturn
    rolling_returns: tuple[RollingReturnSummary, ...]
    drawdown: DrawdownSummary


def calculate_nav_performance(
    points: tuple[NavPoint, ...],
    *,
    rolling_windows_years: tuple[int, ...] = ROLLING_WINDOWS_YEARS,
) -> NavPerformanceResult:
    """Calculate NAV-only performance over ordered, valid, current observations."""

    _validate_points(points)
    since_inception = _nav_return(points[0], points[-1])
    return NavPerformanceResult(
        observation_count=len(points),
        since_inception=since_inception,
        rolling_returns=tuple(
            _rolling_summary(points, window_years) for window_years in rolling_windows_years
        ),
        drawdown=_maximum_drawdown(points),
    )


def calculate_rolling_return_summary(
    points: tuple[NavPoint, ...], *, window_years: int
) -> RollingReturnSummary:
    """Summarize daily-observation rolling returns for one explicit calendar window."""

    _validate_points(points)
    return _rolling_summary(points, window_years)


def calculate_monthly_rolling_return_summary(
    points: tuple[NavPoint, ...], *, window_years: int
) -> RollingReturnSummary:
    """Summarize rolling returns at each calendar month's last valid observation."""

    _validate_points(points)
    month_end_indices: dict[tuple[int, int], int] = {}
    for index, point in enumerate(points):
        month_end_indices[(point.nav_date.year, point.nav_date.month)] = index
    return _rolling_summary(
        points,
        window_years,
        end_indices=tuple(month_end_indices.values()),
    )


def _validate_points(points: tuple[NavPoint, ...]) -> None:
    if not points:
        raise ValueError("NAV performance requires at least one valid observation")
    previous_date: date | None = None
    for point in points:
        if point.nav_value <= 0:
            raise ValueError(f"NAV must be positive on {point.nav_date.isoformat()}")
        if previous_date is not None and point.nav_date <= previous_date:
            raise ValueError("NAV observations must have unique, strictly increasing dates")
        previous_date = point.nav_date


def _nav_return(start: NavPoint, end: NavPoint) -> NavReturn:
    elapsed_days = (end.nav_date - start.nav_date).days
    total_return = (end.nav_value / start.nav_value - 1) * _ONE_HUNDRED
    annualized: Decimal | None = None
    if elapsed_days > 0:
        with localcontext() as context:
            context.prec = 28
            annualized = (
                (end.nav_value / start.nav_value) ** (ANNUALIZATION_DAYS / Decimal(elapsed_days))
                - 1
            ) * _ONE_HUNDRED
    return NavReturn(
        start_date=start.nav_date,
        end_date=end.nav_date,
        start_nav=start.nav_value,
        end_nav=end.nav_value,
        elapsed_days=elapsed_days,
        total_return_pct=total_return,
        annualized_return_pct=annualized,
    )


def _rolling_summary(
    points: tuple[NavPoint, ...],
    window_years: int,
    *,
    end_indices: tuple[int, ...] | None = None,
) -> RollingReturnSummary:
    if window_years <= 0:
        raise ValueError("rolling window years must be positive")
    dates = tuple(point.nav_date for point in points)
    samples: list[NavReturn] = []
    for end_index in end_indices if end_indices is not None else range(len(points)):
        end = points[end_index]
        target = _anniversary(end.nav_date, window_years)
        start_index = bisect_left(dates, target, 0, end_index)
        if start_index >= end_index:
            continue
        start = points[start_index]
        if start.nav_date > target + timedelta(days=ROLLING_START_TOLERANCE_DAYS):
            continue
        samples.append(_nav_return(start, end))

    annualized_values = tuple(
        sample.annualized_return_pct
        for sample in samples
        if sample.annualized_return_pct is not None
    )
    if not annualized_values:
        return RollingReturnSummary(
            window_years=window_years,
            sample_count=0,
            first=None,
            latest=None,
            minimum_annualized_return_pct=None,
            median_annualized_return_pct=None,
            mean_annualized_return_pct=None,
            maximum_annualized_return_pct=None,
            positive_periods_pct=None,
        )
    ordered = sorted(annualized_values)
    return RollingReturnSummary(
        window_years=window_years,
        sample_count=len(annualized_values),
        first=samples[0],
        latest=samples[-1],
        minimum_annualized_return_pct=ordered[0],
        median_annualized_return_pct=_median(ordered),
        mean_annualized_return_pct=sum(ordered, Decimal(0)) / Decimal(len(ordered)),
        maximum_annualized_return_pct=ordered[-1],
        positive_periods_pct=(
            Decimal(sum(value > 0 for value in ordered)) / Decimal(len(ordered)) * _ONE_HUNDRED
        ),
    )


def _anniversary(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _median(ordered: list[Decimal]) -> Decimal:
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _maximum_drawdown(points: tuple[NavPoint, ...]) -> DrawdownSummary:
    peak = points[0]
    worst_drawdown = Decimal(0)
    worst_peak = peak
    trough = peak
    for point in points[1:]:
        if point.nav_value > peak.nav_value:
            peak = point
        drawdown = (point.nav_value / peak.nav_value - 1) * _ONE_HUNDRED
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_peak = peak
            trough = point
    return DrawdownSummary(
        maximum_drawdown_pct=worst_drawdown,
        peak_date=worst_peak.nav_date,
        trough_date=trough.nav_date,
    )
