from typing import Annotated

from fastapi import APIRouter, Query

from mf_strategy_tester.api.dependencies import IngestionRepositoryDependency
from mf_strategy_tester.api.schemas import IngestionBatchResponse

router = APIRouter(prefix="/ingestion-batches", tags=["ingestion"])


@router.get("", response_model=list[IngestionBatchResponse])
def list_ingestion_batches(
    repository: IngestionRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IngestionBatchResponse]:
    return [
        IngestionBatchResponse.from_record(record)
        for record in repository.list_batches(limit=limit, offset=offset)
    ]
