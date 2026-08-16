from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
Fraction = Annotated[Decimal, Field(ge=0, le=1)]
PositiveFraction = Annotated[Decimal, Field(gt=0, le=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlanType(StrEnum):
    DIRECT = "direct"
    REGULAR = "regular"


class OptionType(StrEnum):
    GROWTH = "growth"
    IDCW = "idcw"


class MetricType(StrEnum):
    TRAILING_RETURN = "trailing_return"
    VOLATILITY = "volatility"
    MAX_DRAWDOWN = "max_drawdown"
    DOWNSIDE_DEVIATION = "downside_deviation"
    MOVING_AVERAGE = "moving_average"


class RankDirection(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


class AllocationMethod(StrEnum):
    EQUAL_WEIGHT = "equal_weight"
    SCORE_WEIGHTED = "score_weighted"
    INVERSE_VOLATILITY = "inverse_volatility"
    FIXED_WEIGHT = "fixed_weight"


class RebalanceFrequency(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    YEARLY = "yearly"


class MissingNavPolicy(StrEnum):
    FAIL = "fail"
    NEXT_AVAILABLE = "next_available"
    SKIP_ORDER = "skip_order"


class ContributionFrequency(StrEnum):
    NONE = "none"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class BenchmarkSeriesType(StrEnum):
    PRICE_RETURN = "price_return"
    TOTAL_RETURN = "total_return"


class DateRange(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "DateRange":
        if self.end <= self.start:
            raise ValueError("end date must be after start date")
        return self


class UniverseDefinition(StrictModel):
    """Point-in-time eligibility rules; empty filters mean all historically eligible schemes."""

    scheme_codes: tuple[str, ...] = ()
    amc_names: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    plan: PlanType = PlanType.DIRECT
    option: OptionType = OptionType.GROWTH
    require_active_at_decision_time: bool = True
    minimum_history_periods: int = Field(default=252, ge=0)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "UniverseDefinition":
        if len(self.scheme_codes) != len(set(self.scheme_codes)):
            raise ValueError("universe.scheme_codes must be unique")
        return self


class SignalDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    metric: MetricType
    window_periods: int = Field(ge=2, le=5000)
    direction: RankDirection = RankDirection.DESCENDING
    weight: PositiveDecimal = Decimal("1")


class SelectionDefinition(StrictModel):
    rank_by: tuple[str, ...] = ()
    maximum_holdings: int = Field(ge=1, le=1000)


class AllocationDefinition(StrictModel):
    method: AllocationMethod
    maximum_weight: PositiveFraction = Decimal("1")
    fixed_weights: dict[str, Fraction] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fixed_weights(self) -> "AllocationDefinition":
        if self.method is AllocationMethod.FIXED_WEIGHT:
            if not self.fixed_weights:
                raise ValueError("fixed_weights are required for fixed_weight allocation")
            total = sum(self.fixed_weights.values(), start=Decimal("0"))
            if total != Decimal("1"):
                raise ValueError("fixed_weights must sum exactly to 1")
            overweight = sorted(
                code for code, weight in self.fixed_weights.items() if weight > self.maximum_weight
            )
            if overweight:
                raise ValueError(f"fixed_weights exceed maximum_weight for: {overweight}")
        elif self.fixed_weights:
            raise ValueError("fixed_weights are only valid for fixed_weight allocation")
        return self


class RebalanceDefinition(StrictModel):
    frequency: RebalanceFrequency
    calendar_day: int = Field(default=1, ge=1, le=28)


class ExecutionDefinition(StrictModel):
    """Mutual-fund order timing, expressed as valuation-day lags from a decision."""

    decision_time: Literal["before_cutoff", "after_cutoff"] = "after_cutoff"
    nav_lag_valuation_days: int = Field(default=1, ge=0, le=30)
    missing_nav_policy: MissingNavPolicy = MissingNavPolicy.NEXT_AVAILABLE
    maximum_nav_wait_days: int = Field(default=7, ge=0, le=90)


class CashFlowDefinition(StrictModel):
    initial_investment: PositiveDecimal
    contribution_amount: Decimal = Field(default=Decimal("0"), ge=0)
    contribution_frequency: ContributionFrequency = ContributionFrequency.NONE

    @model_validator(mode="after")
    def validate_contribution(self) -> "CashFlowDefinition":
        has_contribution = self.contribution_amount > 0
        has_frequency = self.contribution_frequency is not ContributionFrequency.NONE
        if has_contribution != has_frequency:
            raise ValueError(
                "contribution_amount and contribution_frequency must either both be set "
                "or both be absent"
            )
        return self


class CostDefinition(StrictModel):
    exit_load_rate: Fraction = Decimal("0")
    transaction_cost_rate: Fraction = Decimal("0")
    taxes_included: bool = False
    expense_ratio_already_reflected_in_nav: Literal[True] = True


class BenchmarkDefinition(StrictModel):
    identifier: str = Field(min_length=1, max_length=128)
    series_type: BenchmarkSeriesType
    currency: Literal["INR"] = "INR"


class StrategyDefinition(StrictModel):
    """Versioned, declarative input understood by the point-in-time engine."""

    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    date_range: DateRange
    universe: UniverseDefinition
    signals: tuple[SignalDefinition, ...] = ()
    selection: SelectionDefinition
    allocation: AllocationDefinition
    rebalance: RebalanceDefinition
    execution: ExecutionDefinition
    cash_flows: CashFlowDefinition
    costs: CostDefinition = Field(default_factory=CostDefinition)
    benchmark: BenchmarkDefinition | None = None

    @model_validator(mode="after")
    def validate_signal_references(self) -> "StrategyDefinition":
        signal_ids = [signal.id for signal in self.signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("signal ids must be unique")
        unknown = sorted(set(self.selection.rank_by) - set(signal_ids))
        if unknown:
            raise ValueError(f"selection.rank_by references unknown signals: {unknown}")
        if self.allocation.method is AllocationMethod.FIXED_WEIGHT:
            configured_codes = set(self.universe.scheme_codes)
            weighted_codes = set(self.allocation.fixed_weights)
            if not configured_codes or configured_codes != weighted_codes:
                raise ValueError(
                    "fixed_weight allocation keys must exactly match universe.scheme_codes"
                )
            if self.signals or self.selection.rank_by:
                raise ValueError("fixed_weight strategies cannot define ranking signals")
            if self.selection.maximum_holdings != len(weighted_codes):
                raise ValueError(
                    "fixed_weight selection.maximum_holdings must match the number of weights"
                )
        elif not self.signals or not self.selection.rank_by:
            raise ValueError("ranked strategies require signals and selection.rank_by")
        return self
