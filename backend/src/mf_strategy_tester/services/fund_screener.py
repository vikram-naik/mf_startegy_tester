from datetime import date
from decimal import Decimal, localcontext
from typing import Literal

ScreenerHorizon = Literal["1m", "3m", "6m", "1y", "3y", "5y", "10y"]

_HORIZON_MONTHS: dict[ScreenerHorizon, int] = {
    "1m": 1,
    "3m": 3,
    "6m": 6,
    "1y": 12,
    "3y": 36,
    "5y": 60,
    "10y": 120,
}
_ONE_HUNDRED = Decimal("100")
_DAYS_PER_YEAR = Decimal("365")


def horizon_start_date(as_of: date, horizon: ScreenerHorizon) -> date:
    months = _HORIZON_MONTHS[horizon]
    target_month_index = as_of.year * 12 + (as_of.month - 1) - months
    target_year, zero_based_month = divmod(target_month_index, 12)
    target_month = zero_based_month + 1
    day = min(as_of.day, _days_in_month(target_year, target_month))
    return date(target_year, target_month, day)


def calculate_series_return(
    *,
    start_date: date,
    start_nav: Decimal,
    end_date: date,
    end_nav: Decimal,
    annualize: bool,
) -> tuple[Decimal, Decimal | None]:
    if start_nav <= 0 or end_nav <= 0:
        raise ValueError("screener series endpoints must be positive")
    elapsed_days = (end_date - start_date).days
    if elapsed_days <= 0:
        raise ValueError("screener end date must be after start date")
    with localcontext() as context:
        context.prec = 28
        total_return = (end_nav / start_nav - 1) * _ONE_HUNDRED
        annualized = (
            ((end_nav / start_nav) ** (_DAYS_PER_YEAR / Decimal(elapsed_days)) - 1) * _ONE_HUNDRED
            if annualize
            else None
        )
    return total_return, annualized


def calculate_trailing_return(
    *,
    start_date: date,
    start_nav: Decimal,
    end_date: date,
    end_nav: Decimal,
    annualize: bool,
) -> tuple[Decimal, Decimal | None]:
    return calculate_series_return(
        start_date=start_date,
        start_nav=start_nav,
        end_date=end_date,
        end_nav=end_nav,
        annualize=annualize,
    )


def is_annualized_horizon(horizon: ScreenerHorizon) -> bool:
    return _HORIZON_MONTHS[horizon] >= 12


def _days_in_month(year: int, month: int) -> int:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days
