from fastapi import APIRouter
from sqlalchemy import text

from mf_strategy_tester.api.dependencies import DatabaseSession
from mf_strategy_tester.api.schemas import HealthResponse

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=HealthResponse)
def health(session: DatabaseSession) -> HealthResponse:
    session.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="reachable")
