from mf_strategy_tester.db.models import StrategyRecord, StrategyVersionRecord
from mf_strategy_tester.domain.strategy import StrategyDefinition
from mf_strategy_tester.repositories.strategies import StrategyRepository


class StrategyNotFoundError(LookupError):
    pass


class StrategyCatalog:
    def __init__(self, repository: StrategyRepository) -> None:
        self._repository = repository

    def list_strategies(self) -> list[StrategyRecord]:
        return self._repository.list()

    def get_strategy(self, strategy_id: str) -> StrategyRecord:
        record = self._repository.get(strategy_id)
        if record is None:
            raise StrategyNotFoundError(strategy_id)
        return record

    def create_strategy(self, definition: StrategyDefinition) -> StrategyRecord:
        return self._repository.create(definition)

    def revise_strategy(
        self, strategy_id: str, definition: StrategyDefinition
    ) -> StrategyVersionRecord:
        record = self.get_strategy(strategy_id)
        return self._repository.add_version(record, definition)
