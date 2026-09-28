from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.main import create_app
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.session import create_database_engine, get_session


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_path = tmp_path / "test.db"
    test_engine = create_database_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False, class_=Session)

    def override_session() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    test_engine.dispose()
