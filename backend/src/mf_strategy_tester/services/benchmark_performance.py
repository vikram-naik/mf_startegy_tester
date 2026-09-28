from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    BenchmarkInstrumentRecord,
    BenchmarkObservationRecord,
)
from mf_strategy_tester.services.fund_screener import (
    ScreenerHorizon,
    calculate_series_return,
    horizon_start_date,
    is_annualized_horizon,
)

BenchmarkInstrumentType = Literal[
    "price_index", "gross_total_return_index", "net_total_return_index"
]
BenchmarkReturnBasis = Literal["price", "gross_total_return", "net_total_return"]
BenchmarkPerformanceStatus = Literal["available", "stale_endpoint", "insufficient_history"]

_SUPPORTED_INSTRUMENT_TYPES = frozenset(
    {"price_index", "gross_total_return_index", "net_total_return_index"}
)


@dataclass(frozen=True)
class BenchmarkSeriesSummary:
    instrument_id: str
    display_name: str
    benchmark_family: str
    provider: Literal["nifty_indices"]
    instrument_type: BenchmarkInstrumentType
    return_basis: BenchmarkReturnBasis
    observation_count: int
    first_observation_date: date
    latest_observation_date: date


@dataclass(frozen=True)
class BenchmarkPerformance:
    instrument_id: str
    display_name: str
    benchmark_family: str
    provider: Literal["nifty_indices"]
    instrument_type: BenchmarkInstrumentType
    return_basis: BenchmarkReturnBasis
    status: BenchmarkPerformanceStatus
    reason_detail: str | None
    requested_as_of_date: date
    target_start_date: date
    endpoint_tolerance_days: int
    start_date: date | None
    end_date: date | None
    start_value: Decimal | None
    end_value: Decimal | None
    elapsed_days: int | None
    endpoint_staleness_days: int | None
    total_return_pct: Decimal | None
    annualized_return_pct: Decimal | None
    day_count_convention: Literal["actual/365"] = "actual/365"


class BenchmarkPerformanceService:
    """Read-only performance access for official Nifty reference series."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_series(self) -> tuple[BenchmarkSeriesSummary, ...]:
        rows = self._session.execute(
            select(
                BenchmarkInstrumentRecord,
                func.count(distinct(BenchmarkObservationRecord.observation_date)),
                func.min(BenchmarkObservationRecord.observation_date),
                func.max(BenchmarkObservationRecord.observation_date),
            )
            .join(
                BenchmarkObservationRecord,
                BenchmarkObservationRecord.benchmark_instrument_id == BenchmarkInstrumentRecord.id,
            )
            .where(
                BenchmarkInstrumentRecord.provider == "nifty_indices",
                BenchmarkInstrumentRecord.instrument_type.in_(_SUPPORTED_INSTRUMENT_TYPES),
                BenchmarkObservationRecord.exchange.is_(None),
            )
            .group_by(BenchmarkInstrumentRecord.id)
            .order_by(
                BenchmarkInstrumentRecord.benchmark_family,
                BenchmarkInstrumentRecord.instrument_type,
                BenchmarkInstrumentRecord.id,
            )
        ).all()
        return tuple(
            BenchmarkSeriesSummary(
                instrument_id=instrument.id,
                display_name=instrument.display_name,
                benchmark_family=instrument.benchmark_family,
                provider="nifty_indices",
                instrument_type=_instrument_type(instrument.instrument_type),
                return_basis=_return_basis(instrument.instrument_type),
                observation_count=int(observation_count),
                first_observation_date=first_date,
                latest_observation_date=latest_date,
            )
            for instrument, observation_count, first_date, latest_date in rows
        )

    def calculate(
        self,
        *,
        instrument_id: str,
        as_of: date,
        horizon: ScreenerHorizon,
        endpoint_tolerance_days: int,
    ) -> BenchmarkPerformance:
        if endpoint_tolerance_days < 0 or endpoint_tolerance_days > 31:
            raise ValueError("endpoint_tolerance_days must be between 0 and 31")
        instrument = self._instrument(instrument_id)
        target_start = horizon_start_date(as_of, horizon)
        start_deadline = target_start + timedelta(days=endpoint_tolerance_days)
        stale_cutoff = as_of - timedelta(days=endpoint_tolerance_days)
        base_filter = (
            BenchmarkObservationRecord.benchmark_instrument_id == instrument.id,
            BenchmarkObservationRecord.exchange.is_(None),
        )
        end_observation = self._session.scalar(
            select(BenchmarkObservationRecord)
            .where(*base_filter, BenchmarkObservationRecord.observation_date <= as_of)
            .order_by(
                BenchmarkObservationRecord.observation_date.desc(),
                BenchmarkObservationRecord.observed_at.desc(),
                BenchmarkObservationRecord.id.desc(),
            )
            .limit(1)
        )
        if end_observation is None or end_observation.observation_date < stale_cutoff:
            latest_date = end_observation.observation_date if end_observation else None
            return self._response(
                instrument=instrument,
                status="stale_endpoint",
                reason_detail=(
                    f"Latest benchmark value {latest_date.isoformat()} precedes required endpoint "
                    f"cutoff {stale_cutoff.isoformat()}"
                    if latest_date
                    else f"No benchmark value exists on or before {as_of.isoformat()}"
                ),
                as_of=as_of,
                target_start=target_start,
                endpoint_tolerance_days=endpoint_tolerance_days,
                start_date=None,
                end_date=latest_date,
                start_value=None,
                end_value=end_observation.close_value if end_observation else None,
                elapsed_days=None,
                endpoint_staleness_days=(as_of - latest_date).days if latest_date else None,
                total_return_pct=None,
                annualized_return_pct=None,
            )
        start_observation = self._session.scalar(
            select(BenchmarkObservationRecord)
            .where(
                *base_filter,
                BenchmarkObservationRecord.observation_date >= target_start,
                BenchmarkObservationRecord.observation_date <= start_deadline,
            )
            .order_by(
                BenchmarkObservationRecord.observation_date,
                BenchmarkObservationRecord.observed_at.desc(),
                BenchmarkObservationRecord.id.desc(),
            )
            .limit(1)
        )
        if start_observation is None:
            return self._response(
                instrument=instrument,
                status="insufficient_history",
                reason_detail=(
                    f"No benchmark value exists from {target_start.isoformat()} through "
                    f"{start_deadline.isoformat()}"
                ),
                as_of=as_of,
                target_start=target_start,
                endpoint_tolerance_days=endpoint_tolerance_days,
                start_date=None,
                end_date=end_observation.observation_date,
                start_value=None,
                end_value=end_observation.close_value,
                elapsed_days=None,
                endpoint_staleness_days=(as_of - end_observation.observation_date).days,
                total_return_pct=None,
                annualized_return_pct=None,
            )
        total_return, annualized_return = calculate_series_return(
            start_date=start_observation.observation_date,
            start_nav=start_observation.close_value,
            end_date=end_observation.observation_date,
            end_nav=end_observation.close_value,
            annualize=is_annualized_horizon(horizon),
        )
        return self._response(
            instrument=instrument,
            status="available",
            reason_detail=None,
            as_of=as_of,
            target_start=target_start,
            endpoint_tolerance_days=endpoint_tolerance_days,
            start_date=start_observation.observation_date,
            end_date=end_observation.observation_date,
            start_value=start_observation.close_value,
            end_value=end_observation.close_value,
            elapsed_days=(
                end_observation.observation_date - start_observation.observation_date
            ).days,
            endpoint_staleness_days=(as_of - end_observation.observation_date).days,
            total_return_pct=total_return,
            annualized_return_pct=annualized_return,
        )

    def _instrument(self, instrument_id: str) -> BenchmarkInstrumentRecord:
        instrument = self._session.get(BenchmarkInstrumentRecord, instrument_id)
        if (
            instrument is None
            or instrument.provider != "nifty_indices"
            or instrument.instrument_type not in _SUPPORTED_INSTRUMENT_TYPES
        ):
            raise LookupError("Official Nifty benchmark series not found")
        return instrument

    @staticmethod
    def _response(
        *,
        instrument: BenchmarkInstrumentRecord,
        status: BenchmarkPerformanceStatus,
        reason_detail: str | None,
        as_of: date,
        target_start: date,
        endpoint_tolerance_days: int,
        start_date: date | None,
        end_date: date | None,
        start_value: Decimal | None,
        end_value: Decimal | None,
        elapsed_days: int | None,
        endpoint_staleness_days: int | None,
        total_return_pct: Decimal | None,
        annualized_return_pct: Decimal | None,
    ) -> BenchmarkPerformance:
        return BenchmarkPerformance(
            instrument_id=instrument.id,
            display_name=instrument.display_name,
            benchmark_family=instrument.benchmark_family,
            provider="nifty_indices",
            instrument_type=_instrument_type(instrument.instrument_type),
            return_basis=_return_basis(instrument.instrument_type),
            status=status,
            reason_detail=reason_detail,
            requested_as_of_date=as_of,
            target_start_date=target_start,
            endpoint_tolerance_days=endpoint_tolerance_days,
            start_date=start_date,
            end_date=end_date,
            start_value=start_value,
            end_value=end_value,
            elapsed_days=elapsed_days,
            endpoint_staleness_days=endpoint_staleness_days,
            total_return_pct=total_return_pct,
            annualized_return_pct=annualized_return_pct,
        )


def _instrument_type(value: str) -> BenchmarkInstrumentType:
    return cast(BenchmarkInstrumentType, value)


def _return_basis(instrument_type: str) -> BenchmarkReturnBasis:
    mapping: dict[str, BenchmarkReturnBasis] = {
        "price_index": "price",
        "gross_total_return_index": "gross_total_return",
        "net_total_return_index": "net_total_return",
    }
    try:
        return mapping[instrument_type]
    except KeyError as error:
        raise ValueError(f"unsupported benchmark instrument type: {instrument_type}") from error
