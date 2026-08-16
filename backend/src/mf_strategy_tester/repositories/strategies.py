from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from mf_strategy_tester.db.models import StrategyRecord, StrategyVersionRecord, utc_now
from mf_strategy_tester.domain.strategy import StrategyDefinition


class StrategyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self) -> list[StrategyRecord]:
        statement = (
            select(StrategyRecord)
            .options(selectinload(StrategyRecord.versions))
            .order_by(StrategyRecord.updated_at.desc(), StrategyRecord.id)
        )
        return list(self._session.scalars(statement).all())

    def get(self, strategy_id: str) -> StrategyRecord | None:
        statement = (
            select(StrategyRecord)
            .where(StrategyRecord.id == strategy_id)
            .options(selectinload(StrategyRecord.versions))
        )
        return self._session.scalar(statement)

    def create(self, definition: StrategyDefinition) -> StrategyRecord:
        record = StrategyRecord(name=definition.name, description=definition.description)
        record.versions.append(
            StrategyVersionRecord(
                version=1,
                definition=definition.model_dump(mode="json"),
            )
        )
        self._session.add(record)
        self._session.commit()
        return record

    def add_version(
        self, record: StrategyRecord, definition: StrategyDefinition
    ) -> StrategyVersionRecord:
        latest_version = self._session.scalar(
            select(func.max(StrategyVersionRecord.version)).where(
                StrategyVersionRecord.strategy_id == record.id
            )
        )
        version = StrategyVersionRecord(
            version=(latest_version or 0) + 1,
            definition=definition.model_dump(mode="json"),
        )
        record.name = definition.name
        record.description = definition.description
        record.updated_at = utc_now()
        record.versions.append(version)
        self._session.commit()
        return version
