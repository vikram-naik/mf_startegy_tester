from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from mf_strategy_tester.db.session import get_session
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.repositories.strategies import StrategyRepository
from mf_strategy_tester.services.strategy_catalog import StrategyCatalog

DatabaseSession = Annotated[Session, Depends(get_session)]


def get_strategy_catalog(session: DatabaseSession) -> StrategyCatalog:
    return StrategyCatalog(StrategyRepository(session))


StrategyCatalogDependency = Annotated[StrategyCatalog, Depends(get_strategy_catalog)]


def get_ingestion_repository(session: DatabaseSession) -> IngestionRepository:
    return IngestionRepository(session)


IngestionRepositoryDependency = Annotated[IngestionRepository, Depends(get_ingestion_repository)]
