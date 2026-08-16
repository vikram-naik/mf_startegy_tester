from fastapi import APIRouter, HTTPException, status

from mf_strategy_tester.api.dependencies import StrategyCatalogDependency
from mf_strategy_tester.api.schemas import (
    StrategyDetailResponse,
    StrategySummaryResponse,
    StrategyVersionResponse,
    StrategyWriteRequest,
)
from mf_strategy_tester.services.strategy_catalog import StrategyNotFoundError

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategySummaryResponse])
def list_strategies(catalog: StrategyCatalogDependency) -> list[StrategySummaryResponse]:
    return [StrategySummaryResponse.from_record(record) for record in catalog.list_strategies()]


@router.post("", response_model=StrategyDetailResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    request: StrategyWriteRequest, catalog: StrategyCatalogDependency
) -> StrategyDetailResponse:
    return StrategyDetailResponse.from_record(catalog.create_strategy(request.definition))


@router.get("/{strategy_id}", response_model=StrategyDetailResponse)
def get_strategy(strategy_id: str, catalog: StrategyCatalogDependency) -> StrategyDetailResponse:
    try:
        return StrategyDetailResponse.from_record(catalog.get_strategy(strategy_id))
    except StrategyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found"
        ) from error


@router.post(
    "/{strategy_id}/versions",
    response_model=StrategyVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def revise_strategy(
    strategy_id: str,
    request: StrategyWriteRequest,
    catalog: StrategyCatalogDependency,
) -> StrategyVersionResponse:
    try:
        version = catalog.revise_strategy(strategy_id, request.definition)
    except StrategyNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found"
        ) from error
    return StrategyVersionResponse.from_record(version)
