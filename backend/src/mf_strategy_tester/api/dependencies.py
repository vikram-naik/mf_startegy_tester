from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from mf_strategy_tester.db.session import get_session
from mf_strategy_tester.repositories.ingestion import IngestionRepository

DatabaseSession = Annotated[Session, Depends(get_session)]


def get_ingestion_repository(session: DatabaseSession) -> IngestionRepository:
    return IngestionRepository(session)


IngestionRepositoryDependency = Annotated[IngestionRepository, Depends(get_ingestion_repository)]
