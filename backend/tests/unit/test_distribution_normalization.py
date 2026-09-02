from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiFundRecord,
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionSourceRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    SchemeOptionRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.distribution_normalization import (
    DistributionNormalizationService,
)


def _database(tmp_path: Path) -> tuple[Session, IngestionBatchRecord]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'normalization.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    batch = IngestionBatchRecord(
        provider="amfi",
        source_type="distributions",
        source_url="https://example.invalid/distributions",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add(batch)
    session.flush()
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="Example Mutual Fund",
            catalog_batch_id=batch.id,
        )
    )
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="known-option",
            first_observed_nav_date=date(2020, 1, 1),
            last_observed_nav_date=date(2020, 1, 1),
            first_observed_batch_id=batch.id,
        )
    )
    session.commit()
    return session, batch


def _source(
    batch: IngestionBatchRecord,
    *,
    source_id: str,
    option_id: str = "known-option",
    record_date: date = date(2020, 2, 1),
    nav_name: str = "Example Fund - IDCW",
    value: Decimal = Decimal("1.5"),
    unit: str = "amount",
    signature: str = "a",
) -> AmfiDistributionRecord:
    return AmfiDistributionRecord(
        id=source_id,
        mutual_fund_id="20",
        source_scheme_id="712",
        source_option_id=option_id,
        scheme_name="Example Fund",
        nav_name=nav_name,
        record_date=record_date,
        raw_source_value=str(value),
        source_value=value,
        source_unit=unit,
        content_signature=signature * 64,
        first_observed_batch_id=batch.id,
    )


def test_normalization_publishes_only_gated_candidates_and_is_idempotent(
    tmp_path: Path,
) -> None:
    session, batch = _database(tmp_path)
    session.add_all(
        [
            _source(batch, source_id="candidate", signature="a"),
            _source(
                batch,
                source_id="percentage",
                record_date=date(2020, 2, 2),
                value=Decimal("10"),
                unit="percentage",
                signature="b",
            ),
            _source(
                batch,
                source_id="unknown",
                record_date=date(2020, 2, 3),
                nav_name="Example Fund - Daily",
                signature="c",
            ),
            _source(
                batch,
                source_id="unmatched",
                option_id="missing-option",
                record_date=date(2020, 2, 4),
                signature="d",
            ),
        ]
    )
    session.commit()
    service = DistributionNormalizationService(session)

    first = service.normalize()
    second = service.normalize()

    event = session.scalar(select(DistributionEventRecord))
    revision = session.scalar(select(DistributionEventRevisionRecord))
    source_link = session.scalar(select(DistributionEventRevisionSourceRecord))
    assert event is not None
    assert revision is not None
    assert source_link is not None
    assert event.amfi_scheme_code == "known-option"
    assert event.record_date == date(2020, 2, 1)
    assert event.event_type == "idcw_cash"
    assert revision.amount_per_unit_inr == Decimal("1.5")
    assert revision.revision_number == 1
    assert revision.is_current is True
    assert source_link.source_distribution_record_id == "candidate"
    assert (first.source_rows_examined, first.candidate_rows, first.blocked_rows) == (4, 1, 3)
    assert (first.events_inserted, first.revisions_inserted, first.rows_unchanged) == (1, 1, 0)
    assert (second.events_inserted, second.revisions_inserted, second.rows_unchanged) == (0, 0, 1)
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DistributionEventRevisionRecord)) == 1
    assert (
        session.scalar(select(func.count()).select_from(DistributionEventRevisionSourceRecord)) == 1
    )
    session.close()


def test_normalization_preserves_amount_corrections_as_immutable_revisions(tmp_path: Path) -> None:
    session, batch = _database(tmp_path)
    session.add(_source(batch, source_id="original", signature="a"))
    session.commit()
    service = DistributionNormalizationService(session)
    service.normalize()
    session.add(
        _source(
            batch,
            source_id="correction",
            value=Decimal("2.25"),
            signature="b",
        )
    )
    session.commit()

    result = service.normalize()

    revisions = session.scalars(
        select(DistributionEventRevisionRecord).order_by(
            DistributionEventRevisionRecord.revision_number
        )
    ).all()
    assert [(item.amount_per_unit_inr, item.is_current) for item in revisions] == [
        (Decimal("1.5"), False),
        (Decimal("2.25"), True),
    ]
    assert result.events_inserted == 0
    assert result.revisions_inserted == 1
    assert result.rows_unchanged == 1
    assert (
        session.scalar(select(func.count()).select_from(DistributionEventRevisionSourceRecord)) == 2
    )
    session.close()


def test_normalization_rejects_candidate_and_blocked_rows_with_same_event_identity(
    tmp_path: Path,
) -> None:
    session, batch = _database(tmp_path)
    session.add_all(
        [
            _source(batch, source_id="candidate", signature="a"),
            _source(
                batch,
                source_id="percentage",
                value=Decimal("10"),
                unit="percentage",
                signature="b",
            ),
        ]
    )
    session.commit()

    with pytest.raises(RuntimeError, match="candidate and blocked source rows share"):
        DistributionNormalizationService(session).normalize()

    run = session.scalar(select(DistributionNormalizationRunRecord))
    assert run is not None
    assert run.status == "failed"
    assert "RuntimeError" in (run.error_details or "")
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0
    session.close()
