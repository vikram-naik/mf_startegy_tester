from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from mf_strategy_tester.db.models import (
    BenchmarkInstrumentRecord,
    BenchmarkObservationRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    SchemeClassificationAliasRecord,
    SchemeClassificationRecord,
    ScreenerClassificationAliasMemberRecord,
    ScreenerClassificationAliasRecord,
)
from mf_strategy_tester.repositories.scheme_options import current_scheme_snapshot
from mf_strategy_tester.services.benchmark_performance import BenchmarkPerformanceService
from mf_strategy_tester.services.classification_reference import (
    SchemeProductType,
    SchemeStructure,
    scheme_classification_product_type,
)
from mf_strategy_tester.services.fund_screener import (
    ScreenerHorizon,
    calculate_trailing_return,
    horizon_start_date,
    is_annualized_horizon,
)
from mf_strategy_tester.services.nav_performance import (
    NavPoint,
    calculate_monthly_rolling_return_summary,
    calculate_rolling_return_summary,
)

HeatmapUniverse = Literal["funds", "benchmarks", "indices"]
HeatmapPeriod = Literal[
    "1m",
    "3m",
    "6m",
    "1y",
    "3y",
    "5y",
    "10y",
    "rolling_1y",
    "rolling_3y",
    "rolling_5y",
    "rolling_10y",
]
HeatmapMode = Literal["trailing", "rolling"]
HeatmapStatus = Literal["available", "stale_endpoint", "insufficient_history"]
HeatmapReturnBasis = Literal["nav_only", "price", "gross_total_return", "net_total_return"]

_ROLLING_PERIOD_YEARS: dict[HeatmapPeriod, int] = {
    "rolling_1y": 1,
    "rolling_3y": 3,
    "rolling_5y": 5,
    "rolling_10y": 10,
}


@dataclass(frozen=True)
class HeatmapTile:
    tile_id: str
    label: str
    status: HeatmapStatus
    reason_detail: str | None
    value_pct: Decimal | None
    minimum_constituent_pct: Decimal | None
    maximum_constituent_pct: Decimal | None
    candidate_count: int
    constituent_count: int
    excluded_count: int
    excluded_stale_endpoint: int
    excluded_insufficient_history: int
    sample_count: int
    return_basis: HeatmapReturnBasis
    structure_type: SchemeStructure | None
    product_type: Literal["mutual_fund", "index_fund", "etf", "mixed"] | None
    classification_mapping_version: str | None
    period_start_date_min: date | None
    period_start_date_max: date | None
    period_end_date_min: date | None
    period_end_date_max: date | None
    maximum_endpoint_staleness_days: int | None


@dataclass(frozen=True)
class HeatmapResult:
    universe: HeatmapUniverse
    period: HeatmapPeriod
    mode: HeatmapMode
    requested_as_of_date: date
    target_start_date: date | None
    endpoint_tolerance_days: int
    metric: Literal[
        "median_constituent_return_pct",
        "series_return_pct",
        "median_rolling_annualized_return_pct",
    ]
    aggregation_method: str
    observation_frequency: str
    day_count_convention: Literal["actual/365"]
    distribution_treatment: str
    rolling_start_rule: str | None
    current_universe_limitation: str
    tiles: tuple[HeatmapTile, ...]


@dataclass(frozen=True)
class _FundCandidate:
    scheme_code: str
    alias_id: str
    alias_name: str
    alias_version: int
    structure_type: SchemeStructure
    product_type: SchemeProductType


@dataclass
class _FundGroup:
    alias_id: str
    label: str
    alias_version: int
    structure_type: SchemeStructure
    candidate_count: int = 0
    product_types: set[SchemeProductType] = field(default_factory=set)
    values: list[Decimal] = field(default_factory=list)
    sample_count: int = 0
    start_dates: list[date] = field(default_factory=list)
    end_dates: list[date] = field(default_factory=list)
    endpoint_staleness_days: list[int] = field(default_factory=list)
    excluded_stale_endpoint: int = 0
    excluded_insufficient_history: int = 0


class HeatmapService:
    """Calculate read-only classification and official-index heatmap values."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def calculate(
        self,
        *,
        universe: HeatmapUniverse,
        period: HeatmapPeriod,
        plan_type: Literal["direct", "regular"],
        as_of: date | None,
        endpoint_tolerance_days: int,
    ) -> HeatmapResult:
        if endpoint_tolerance_days < 0 or endpoint_tolerance_days > 31:
            raise ValueError("endpoint_tolerance_days must be between 0 and 31")
        resolved_as_of = self._resolve_as_of(universe, as_of)
        mode = heatmap_mode(period)
        if universe == "funds":
            tiles = (
                self._fund_trailing_tiles(
                    period=trailing_horizon(period),
                    plan_type=plan_type,
                    as_of=resolved_as_of,
                    endpoint_tolerance_days=endpoint_tolerance_days,
                )
                if mode == "trailing"
                else self._fund_rolling_tiles(
                    window_years=rolling_window_years(period),
                    plan_type=plan_type,
                    as_of=resolved_as_of,
                    endpoint_tolerance_days=endpoint_tolerance_days,
                )
            )
        else:
            tiles = self._reference_tiles(
                universe=universe,
                period=period,
                as_of=resolved_as_of,
                endpoint_tolerance_days=endpoint_tolerance_days,
            )
        target_start = (
            horizon_start_date(resolved_as_of, trailing_horizon(period))
            if mode == "trailing"
            else None
        )
        return HeatmapResult(
            universe=universe,
            period=period,
            mode=mode,
            requested_as_of_date=resolved_as_of,
            target_start_date=target_start,
            endpoint_tolerance_days=endpoint_tolerance_days,
            metric=(
                "median_constituent_return_pct"
                if universe == "funds" and mode == "trailing"
                else "series_return_pct"
                if mode == "trailing"
                else "median_rolling_annualized_return_pct"
            ),
            aggregation_method=(
                "median of eligible constituent scheme-option returns"
                if universe == "funds" and mode == "trailing"
                else "one official series; no cross-series aggregation"
                if mode == "trailing"
                else (
                    "median of each eligible scheme option's median monthly-observation rolling "
                    "annualized return"
                    if universe == "funds"
                    else (
                        "median of the official series' daily-observation rolling "
                        "annualized returns"
                    )
                )
            ),
            observation_frequency=(
                "one trailing period per constituent"
                if mode == "trailing"
                else "last valid NAV observation in each calendar month"
                if universe == "funds"
                else "each valid daily official index observation"
            ),
            day_count_convention="actual/365",
            distribution_treatment=(
                "fund IDCW excluded; published Growth-option NAV already reflects fund expenses"
                if universe == "funds"
                else "gross index distributions reinvested before tax for TRI; net index "
                "distributions follow provider withholding assumptions for NTR"
                if universe == "benchmarks"
                else "price indices exclude distributions"
            ),
            rolling_start_rule=(
                "first valid observation on or after each calendar anniversary, "
                "within 7 calendar days"
                if mode == "rolling"
                else None
            ),
            current_universe_limitation=(
                "Uses the latest observed scheme metadata/current catalog and is not a "
                "survivorship-free historical-universe study."
                if universe == "funds"
                else "Uses only locally stored official Nifty series and their latest immutable "
                "observation revisions."
            ),
            tiles=tiles,
        )

    def _resolve_as_of(self, universe: HeatmapUniverse, requested: date | None) -> date:
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        resolved = requested or today
        if resolved > today:
            raise ValueError(f"as_of cannot exceed current date {today.isoformat()}")
        if universe == "funds":
            dataset_stats = self._session.get(NavDatasetStatsRecord, 1)
            latest = dataset_stats.latest_valid_nav_date if dataset_stats else None
            if latest is None:
                raise LookupError("No valid NAV dataset is available")
            return resolved
        types = reference_instrument_types(universe)
        latest = self._session.scalar(
            select(func.max(BenchmarkObservationRecord.observation_date))
            .join(
                BenchmarkInstrumentRecord,
                BenchmarkInstrumentRecord.id == BenchmarkObservationRecord.benchmark_instrument_id,
            )
            .where(
                BenchmarkInstrumentRecord.provider == "nifty_indices",
                BenchmarkInstrumentRecord.instrument_type.in_(types),
                BenchmarkObservationRecord.exchange.is_(None),
            )
        )
        if latest is None:
            raise LookupError(f"No official Nifty {universe} dataset is available")
        return resolved

    def _fund_candidates(
        self, plan_type: Literal["direct", "regular"]
    ) -> dict[str, _FundCandidate]:
        snapshot = current_scheme_snapshot().subquery()
        rows = self._session.execute(
            select(
                snapshot.c.amfi_scheme_code,
                ScreenerClassificationAliasRecord.id,
                ScreenerClassificationAliasRecord.name,
                ScreenerClassificationAliasRecord.version,
                ScreenerClassificationAliasRecord.structure_type,
                SchemeClassificationRecord.display_name,
            )
            .select_from(snapshot)
            .join(
                SchemeClassificationAliasRecord,
                and_(
                    SchemeClassificationAliasRecord.source_provider == "amfi",
                    SchemeClassificationAliasRecord.raw_classification
                    == snapshot.c.scheme_classification,
                ),
            )
            .join(
                SchemeClassificationRecord,
                SchemeClassificationRecord.id == SchemeClassificationAliasRecord.classification_id,
            )
            .join(
                ScreenerClassificationAliasMemberRecord,
                ScreenerClassificationAliasMemberRecord.classification_id
                == SchemeClassificationRecord.id,
            )
            .join(
                ScreenerClassificationAliasRecord,
                ScreenerClassificationAliasRecord.id
                == ScreenerClassificationAliasMemberRecord.alias_id,
            )
            .where(
                snapshot.c.plan_type == plan_type,
                snapshot.c.option_type == "growth",
                SchemeClassificationRecord.status == "active",
                ScreenerClassificationAliasRecord.status == "active",
            )
            .order_by(snapshot.c.amfi_scheme_code)
        ).all()
        return {
            str(row[0]): _FundCandidate(
                scheme_code=str(row[0]),
                alias_id=str(row[1]),
                alias_name=str(row[2]),
                alias_version=int(row[3]),
                structure_type=cast(SchemeStructure, row[4]),
                product_type=scheme_classification_product_type(str(row[5])),
            )
            for row in rows
        }

    @staticmethod
    def _fund_groups(candidates: dict[str, _FundCandidate]) -> dict[str, _FundGroup]:
        groups: dict[str, _FundGroup] = {}
        for candidate in candidates.values():
            group = groups.setdefault(
                candidate.alias_id,
                _FundGroup(
                    alias_id=candidate.alias_id,
                    label=candidate.alias_name,
                    alias_version=candidate.alias_version,
                    structure_type=candidate.structure_type,
                ),
            )
            group.candidate_count += 1
            group.product_types.add(candidate.product_type)
        return groups

    def _fund_trailing_tiles(
        self,
        *,
        period: ScreenerHorizon,
        plan_type: Literal["direct", "regular"],
        as_of: date,
        endpoint_tolerance_days: int,
    ) -> tuple[HeatmapTile, ...]:
        candidates = self._fund_candidates(plan_type)
        groups = self._fund_groups(candidates)
        if not candidates:
            return ()
        target_start = horizon_start_date(as_of, period)
        start_deadline = target_start + timedelta(days=endpoint_tolerance_days)
        stale_cutoff = as_of - timedelta(days=endpoint_tolerance_days)
        codes = tuple(candidates)
        # Alias the endpoint source so the scalar subqueries correlate only to the code list.
        code_source = (
            select(NavRevisionRecord.amfi_scheme_code)
            .where(NavRevisionRecord.amfi_scheme_code.in_(codes))
            .distinct()
            .subquery()
        )
        endpoint = aliased(NavRevisionRecord)
        end_date = (
            select(endpoint.nav_date)
            .where(
                endpoint.amfi_scheme_code == code_source.c.amfi_scheme_code,
                endpoint.is_current.is_(True),
                endpoint.quality_status == "valid",
                endpoint.nav_date <= as_of,
            )
            .order_by(endpoint.nav_date.desc())
            .limit(1)
            .correlate(code_source)
            .scalar_subquery()
        )
        start_date = (
            select(endpoint.nav_date)
            .where(
                endpoint.amfi_scheme_code == code_source.c.amfi_scheme_code,
                endpoint.is_current.is_(True),
                endpoint.quality_status == "valid",
                endpoint.nav_date >= target_start,
                endpoint.nav_date <= start_deadline,
            )
            .order_by(endpoint.nav_date)
            .limit(1)
            .correlate(code_source)
            .scalar_subquery()
        )
        start_nav = aliased(NavRevisionRecord)
        end_nav = aliased(NavRevisionRecord)
        rows = self._session.execute(
            select(
                code_source.c.amfi_scheme_code,
                start_date.label("start_date"),
                start_nav.nav_value.label("start_value"),
                end_date.label("end_date"),
                end_nav.nav_value.label("end_value"),
            )
            .select_from(code_source)
            .outerjoin(
                start_nav,
                and_(
                    start_nav.amfi_scheme_code == code_source.c.amfi_scheme_code,
                    start_nav.nav_date == start_date,
                    start_nav.is_current.is_(True),
                    start_nav.quality_status == "valid",
                ),
            )
            .outerjoin(
                end_nav,
                and_(
                    end_nav.amfi_scheme_code == code_source.c.amfi_scheme_code,
                    end_nav.nav_date == end_date,
                    end_nav.is_current.is_(True),
                    end_nav.quality_status == "valid",
                ),
            )
        ).all()
        for scheme_code, actual_start, start_value, actual_end, end_value in rows:
            group = groups[candidates[str(scheme_code)].alias_id]
            if actual_end is None or end_value is None or actual_end < stale_cutoff:
                group.excluded_stale_endpoint += 1
                continue
            if actual_start is None or start_value is None:
                group.excluded_insufficient_history += 1
                continue
            total_return, annualized_return = calculate_trailing_return(
                start_date=actual_start,
                start_nav=start_value,
                end_date=actual_end,
                end_nav=end_value,
                annualize=is_annualized_horizon(period),
            )
            group.values.append(
                annualized_return if annualized_return is not None else total_return
            )
            group.sample_count += 1
            group.start_dates.append(actual_start)
            group.end_dates.append(actual_end)
            group.endpoint_staleness_days.append((as_of - actual_end).days)
        return _group_tiles(groups)

    def _fund_rolling_tiles(
        self,
        *,
        window_years: int,
        plan_type: Literal["direct", "regular"],
        as_of: date,
        endpoint_tolerance_days: int,
    ) -> tuple[HeatmapTile, ...]:
        candidates = self._fund_candidates(plan_type)
        groups = self._fund_groups(candidates)
        if not candidates:
            return ()
        stale_cutoff = as_of - timedelta(days=endpoint_tolerance_days)
        eligible_codes = (
            select(NavRevisionRecord.amfi_scheme_code)
            .where(
                NavRevisionRecord.amfi_scheme_code.in_(tuple(candidates)),
                NavRevisionRecord.is_current.is_(True),
                NavRevisionRecord.quality_status == "valid",
                NavRevisionRecord.nav_date >= stale_cutoff,
                NavRevisionRecord.nav_date <= as_of,
            )
            .distinct()
        )
        result = self._session.execute(
            select(
                NavRevisionRecord.amfi_scheme_code,
                NavRevisionRecord.nav_date,
                NavRevisionRecord.nav_value,
            )
            .where(
                NavRevisionRecord.amfi_scheme_code.in_(eligible_codes),
                NavRevisionRecord.is_current.is_(True),
                NavRevisionRecord.quality_status == "valid",
                NavRevisionRecord.nav_date <= as_of,
            )
            .order_by(NavRevisionRecord.amfi_scheme_code, NavRevisionRecord.nav_date)
        ).yield_per(10_000)
        current_code: str | None = None
        points: list[NavPoint] = []
        endpoint_eligible_codes: set[str] = set()
        summary_codes: set[str] = set()
        for scheme_code, nav_date, nav_value in result:
            code = str(scheme_code)
            endpoint_eligible_codes.add(code)
            if current_code is not None and code != current_code:
                if self._record_fund_rolling(
                    candidates[current_code], groups, tuple(points), window_years, as_of
                ):
                    summary_codes.add(current_code)
                points = []
            current_code = code
            points.append(NavPoint(nav_date=nav_date, nav_value=nav_value))
        if current_code is not None and self._record_fund_rolling(
            candidates[current_code], groups, tuple(points), window_years, as_of
        ):
            summary_codes.add(current_code)
        for code, candidate in candidates.items():
            group = groups[candidate.alias_id]
            if code not in endpoint_eligible_codes:
                group.excluded_stale_endpoint += 1
            elif code not in summary_codes:
                group.excluded_insufficient_history += 1
        return _group_tiles(groups)

    @staticmethod
    def _record_fund_rolling(
        candidate: _FundCandidate,
        groups: dict[str, _FundGroup],
        points: tuple[NavPoint, ...],
        window_years: int,
        as_of: date,
    ) -> bool:
        summary = calculate_monthly_rolling_return_summary(points, window_years=window_years)
        if summary.median_annualized_return_pct is None:
            return False
        group = groups[candidate.alias_id]
        group.values.append(summary.median_annualized_return_pct)
        group.sample_count += summary.sample_count
        if summary.first is not None:
            group.start_dates.append(summary.first.start_date)
            group.end_dates.append(summary.first.end_date)
        if summary.latest is not None:
            group.start_dates.append(summary.latest.start_date)
            group.end_dates.append(summary.latest.end_date)
            group.endpoint_staleness_days.append((as_of - summary.latest.end_date).days)
        return True

    def _reference_tiles(
        self,
        *,
        universe: Literal["benchmarks", "indices"],
        period: HeatmapPeriod,
        as_of: date,
        endpoint_tolerance_days: int,
    ) -> tuple[HeatmapTile, ...]:
        instruments = tuple(
            self._session.scalars(
                select(BenchmarkInstrumentRecord)
                .where(
                    BenchmarkInstrumentRecord.provider == "nifty_indices",
                    BenchmarkInstrumentRecord.instrument_type.in_(
                        reference_instrument_types(universe)
                    ),
                )
                .order_by(
                    BenchmarkInstrumentRecord.benchmark_family,
                    BenchmarkInstrumentRecord.instrument_type,
                    BenchmarkInstrumentRecord.id,
                )
            ).all()
        )
        if heatmap_mode(period) == "trailing":
            service = BenchmarkPerformanceService(self._session)
            tiles = []
            for instrument in instruments:
                performance = service.calculate(
                    instrument_id=instrument.id,
                    as_of=as_of,
                    horizon=trailing_horizon(period),
                    endpoint_tolerance_days=endpoint_tolerance_days,
                )
                value = (
                    performance.annualized_return_pct
                    if performance.annualized_return_pct is not None
                    else performance.total_return_pct
                )
                tiles.append(
                    HeatmapTile(
                        tile_id=instrument.id,
                        label=instrument.display_name,
                        status=performance.status,
                        reason_detail=performance.reason_detail,
                        value_pct=value,
                        minimum_constituent_pct=value,
                        maximum_constituent_pct=value,
                        candidate_count=1,
                        constituent_count=1 if value is not None else 0,
                        excluded_count=0 if value is not None else 1,
                        excluded_stale_endpoint=(
                            1 if performance.status == "stale_endpoint" else 0
                        ),
                        excluded_insufficient_history=(
                            1 if performance.status == "insufficient_history" else 0
                        ),
                        sample_count=1 if value is not None else 0,
                        return_basis=_reference_return_basis(instrument.instrument_type),
                        structure_type=None,
                        product_type=None,
                        classification_mapping_version=None,
                        period_start_date_min=performance.start_date,
                        period_start_date_max=performance.start_date,
                        period_end_date_min=performance.end_date,
                        period_end_date_max=performance.end_date,
                        maximum_endpoint_staleness_days=performance.endpoint_staleness_days,
                    )
                )
            return _sort_tiles(tuple(tiles))
        return self._reference_rolling_tiles(
            instruments=instruments,
            window_years=rolling_window_years(period),
            as_of=as_of,
            endpoint_tolerance_days=endpoint_tolerance_days,
        )

    def _reference_rolling_tiles(
        self,
        *,
        instruments: tuple[BenchmarkInstrumentRecord, ...],
        window_years: int,
        as_of: date,
        endpoint_tolerance_days: int,
    ) -> tuple[HeatmapTile, ...]:
        if not instruments:
            return ()
        instrument_by_id = {item.id: item for item in instruments}
        ranked = (
            select(
                BenchmarkObservationRecord.benchmark_instrument_id.label("instrument_id"),
                BenchmarkObservationRecord.observation_date.label("observation_date"),
                BenchmarkObservationRecord.close_value.label("close_value"),
                func.row_number()
                .over(
                    partition_by=(
                        BenchmarkObservationRecord.benchmark_instrument_id,
                        BenchmarkObservationRecord.observation_date,
                    ),
                    order_by=(
                        BenchmarkObservationRecord.observed_at.desc(),
                        BenchmarkObservationRecord.id.desc(),
                    ),
                )
                .label("revision_rank"),
            )
            .where(
                BenchmarkObservationRecord.benchmark_instrument_id.in_(tuple(instrument_by_id)),
                BenchmarkObservationRecord.exchange.is_(None),
                BenchmarkObservationRecord.observation_date <= as_of,
            )
            .subquery()
        )
        rows = self._session.execute(
            select(ranked.c.instrument_id, ranked.c.observation_date, ranked.c.close_value)
            .where(ranked.c.revision_rank == 1)
            .order_by(ranked.c.instrument_id, ranked.c.observation_date)
        )
        points_by_instrument: dict[str, list[NavPoint]] = {
            instrument_id: [] for instrument_id in instrument_by_id
        }
        for instrument_id, observation_date, close_value in rows:
            points_by_instrument[str(instrument_id)].append(
                NavPoint(nav_date=observation_date, nav_value=close_value)
            )
        stale_cutoff = as_of - timedelta(days=endpoint_tolerance_days)
        tiles = []
        for instrument in instruments:
            points = tuple(points_by_instrument[instrument.id])
            latest_date = points[-1].nav_date if points else None
            if latest_date is None or latest_date < stale_cutoff:
                tiles.append(
                    _unavailable_reference_tile(
                        instrument,
                        status="stale_endpoint",
                        reason_detail=(
                            f"Latest official value {latest_date.isoformat()} precedes required "
                            f"endpoint cutoff {stale_cutoff.isoformat()}"
                            if latest_date
                            else f"No official value exists on or before {as_of.isoformat()}"
                        ),
                        latest_date=latest_date,
                        as_of=as_of,
                    )
                )
                continue
            summary = calculate_rolling_return_summary(points, window_years=window_years)
            value = summary.median_annualized_return_pct
            if value is None:
                tiles.append(
                    _unavailable_reference_tile(
                        instrument,
                        status="insufficient_history",
                        reason_detail=f"No valid {window_years}-year rolling period is available",
                        latest_date=latest_date,
                        as_of=as_of,
                    )
                )
                continue
            latest = summary.latest
            tiles.append(
                HeatmapTile(
                    tile_id=instrument.id,
                    label=instrument.display_name,
                    status="available",
                    reason_detail=None,
                    value_pct=value,
                    minimum_constituent_pct=summary.minimum_annualized_return_pct,
                    maximum_constituent_pct=summary.maximum_annualized_return_pct,
                    candidate_count=1,
                    constituent_count=1,
                    excluded_count=0,
                    excluded_stale_endpoint=0,
                    excluded_insufficient_history=0,
                    sample_count=summary.sample_count,
                    return_basis=_reference_return_basis(instrument.instrument_type),
                    structure_type=None,
                    product_type=None,
                    classification_mapping_version=None,
                    period_start_date_min=summary.first.start_date if summary.first else None,
                    period_start_date_max=latest.start_date if latest else None,
                    period_end_date_min=summary.first.end_date if summary.first else None,
                    period_end_date_max=latest.end_date if latest else None,
                    maximum_endpoint_staleness_days=(as_of - latest_date).days,
                )
            )
        return _sort_tiles(tuple(tiles))


def heatmap_mode(period: HeatmapPeriod) -> HeatmapMode:
    return "rolling" if period.startswith("rolling_") else "trailing"


def trailing_horizon(period: HeatmapPeriod) -> ScreenerHorizon:
    if heatmap_mode(period) != "trailing":
        raise ValueError("a rolling heatmap period is not a trailing screener horizon")
    return cast(ScreenerHorizon, period)


def rolling_window_years(period: HeatmapPeriod) -> int:
    try:
        return _ROLLING_PERIOD_YEARS[period]
    except KeyError as error:
        raise ValueError("a trailing heatmap period is not a rolling window") from error


def reference_instrument_types(universe: HeatmapUniverse) -> tuple[str, ...]:
    if universe == "benchmarks":
        return ("gross_total_return_index", "net_total_return_index")
    if universe == "indices":
        return ("price_index",)
    raise ValueError("fund heatmaps do not use benchmark instrument types")


def _reference_return_basis(instrument_type: str) -> HeatmapReturnBasis:
    mapping: dict[str, HeatmapReturnBasis] = {
        "price_index": "price",
        "gross_total_return_index": "gross_total_return",
        "net_total_return_index": "net_total_return",
    }
    return mapping[instrument_type]


def _group_tiles(groups: dict[str, _FundGroup]) -> tuple[HeatmapTile, ...]:
    tiles = []
    for group in groups.values():
        value = _median(group.values) if group.values else None
        tiles.append(
            HeatmapTile(
                tile_id=group.alias_id,
                label=group.label,
                status="available" if value is not None else "insufficient_history",
                reason_detail=(
                    None
                    if value is not None
                    else "No constituent has valid endpoints and sufficient history"
                ),
                value_pct=value,
                minimum_constituent_pct=min(group.values) if group.values else None,
                maximum_constituent_pct=max(group.values) if group.values else None,
                candidate_count=group.candidate_count,
                constituent_count=len(group.values),
                excluded_count=group.candidate_count - len(group.values),
                excluded_stale_endpoint=group.excluded_stale_endpoint,
                excluded_insufficient_history=group.excluded_insufficient_history,
                sample_count=group.sample_count,
                return_basis="nav_only",
                structure_type=group.structure_type,
                product_type=(
                    next(iter(group.product_types)) if len(group.product_types) == 1 else "mixed"
                ),
                classification_mapping_version=f"{group.alias_id}:v{group.alias_version}",
                period_start_date_min=min(group.start_dates) if group.start_dates else None,
                period_start_date_max=max(group.start_dates) if group.start_dates else None,
                period_end_date_min=min(group.end_dates) if group.end_dates else None,
                period_end_date_max=max(group.end_dates) if group.end_dates else None,
                maximum_endpoint_staleness_days=(
                    max(group.endpoint_staleness_days) if group.endpoint_staleness_days else None
                ),
            )
        )
    return _sort_tiles(tuple(tiles))


def _unavailable_reference_tile(
    instrument: BenchmarkInstrumentRecord,
    *,
    status: HeatmapStatus,
    reason_detail: str,
    latest_date: date | None,
    as_of: date,
) -> HeatmapTile:
    return HeatmapTile(
        tile_id=instrument.id,
        label=instrument.display_name,
        status=status,
        reason_detail=reason_detail,
        value_pct=None,
        minimum_constituent_pct=None,
        maximum_constituent_pct=None,
        candidate_count=1,
        constituent_count=0,
        excluded_count=1,
        excluded_stale_endpoint=1 if status == "stale_endpoint" else 0,
        excluded_insufficient_history=1 if status == "insufficient_history" else 0,
        sample_count=0,
        return_basis=_reference_return_basis(instrument.instrument_type),
        structure_type=None,
        product_type=None,
        classification_mapping_version=None,
        period_start_date_min=None,
        period_start_date_max=None,
        period_end_date_min=latest_date,
        period_end_date_max=latest_date,
        maximum_endpoint_staleness_days=(as_of - latest_date).days if latest_date else None,
    )


def _sort_tiles(tiles: tuple[HeatmapTile, ...]) -> tuple[HeatmapTile, ...]:
    return tuple(
        sorted(
            tiles,
            key=lambda tile: (
                tile.value_pct is None,
                -(tile.value_pct or Decimal(0)),
                tile.label.casefold(),
                tile.tile_id,
            ),
        )
    )


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
