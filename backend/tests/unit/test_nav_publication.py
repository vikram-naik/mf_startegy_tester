from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    DataQualityIssueRecord,
    IngestionBatchRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    NavRevisionSourceRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.amfi import AmfiNavParser, InvalidNavSourceRecord
from mf_strategy_tester.services.nav_publication import NormalizedNavPublisher

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'publication.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return factory()


def _batch(session: Session) -> IngestionBatchRecord:
    batch = IngestionBatchRecord(
        provider="amfi",
        source_type="historical_nav",
        source_url="https://example.invalid/source",
        request_parameters={},
        parser_version="test",
        status="completed",
        rows_received=2,
    )
    session.add(batch)
    session.commit()
    return batch


def test_repeat_and_revision_preserve_exact_nav_lineage(tmp_path: Path) -> None:
    session = _session(tmp_path)
    records = AmfiNavParser().parse_records((FIXTURES / "nav_current.txt").read_bytes())
    publisher = NormalizedNavPublisher(session)

    first = publisher.publish(records, batch_id=_batch(session).id)
    second = publisher.publish(records, batch_id=_batch(session).id)
    revised_records = (replace(records[0], nav=Decimal("12.3456")), records[1])
    third = publisher.publish(revised_records, batch_id=_batch(session).id)

    assert (first.rows_inserted, first.rows_revised, first.rows_unchanged) == (2, 0, 0)
    assert (second.rows_inserted, second.rows_revised, second.rows_unchanged) == (0, 0, 2)
    assert (third.rows_inserted, third.rows_revised, third.rows_unchanged) == (0, 1, 1)
    assert session.scalar(select(func.count()).select_from(SchemeOptionRecord)) == 2
    assert session.scalar(select(func.count()).select_from(SchemeMetadataVersionRecord)) == 2
    assert session.scalar(select(func.count()).select_from(NavRevisionRecord)) == 3
    assert session.scalar(select(func.count()).select_from(NavRevisionSourceRecord)) == 6
    revisions = list(
        session.scalars(
            select(NavRevisionRecord)
            .where(NavRevisionRecord.amfi_scheme_code == records[0].scheme_code)
            .order_by(NavRevisionRecord.revision_number)
        ).all()
    )
    assert [(item.nav_value, item.is_current) for item in revisions] == [
        (records[0].nav, False),
        (Decimal("12.3456"), True),
    ]
    stats = session.get(NavDatasetStatsRecord, 1)
    assert stats is not None
    assert (stats.valid_current_rows, stats.error_current_rows, stats.scheme_options) == (2, 0, 2)
    session.close()


def test_zero_nav_is_retained_but_quarantined(tmp_path: Path) -> None:
    session = _session(tmp_path)
    record = AmfiNavParser().parse_records((FIXTURES / "nav_current.txt").read_bytes())[0]

    stats = NormalizedNavPublisher(session).publish(
        (replace(record, nav=Decimal("0")),), batch_id=_batch(session).id
    )

    revision = session.scalar(select(NavRevisionRecord))
    issue = session.scalar(select(DataQualityIssueRecord))
    assert stats.rows_quarantined == 1
    assert revision is not None and revision.quality_status == "error"
    assert issue is not None and issue.issue_code == "NON_POSITIVE_NAV"
    dataset_stats = session.get(NavDatasetStatsRecord, 1)
    assert dataset_stats is not None and dataset_stats.error_current_rows == 1
    session.close()


def test_metadata_text_change_does_not_create_a_financial_revision(tmp_path: Path) -> None:
    session = _session(tmp_path)
    record = AmfiNavParser().parse_records((FIXTURES / "nav_current.txt").read_bytes())[0]
    publisher = NormalizedNavPublisher(session)
    publisher.publish((record,), batch_id=_batch(session).id)

    stats = publisher.publish(
        (replace(record, scheme_classification=f"{record.scheme_classification} "),),
        batch_id=_batch(session).id,
    )

    assert stats.rows_unchanged == 1
    assert session.scalar(select(func.count()).select_from(NavRevisionRecord)) == 1
    assert session.scalar(select(func.count()).select_from(SchemeMetadataVersionRecord)) == 2
    assert session.scalar(select(func.count()).select_from(NavRevisionSourceRecord)) == 2
    session.close()


def test_non_numeric_nav_is_rejected_without_losing_source_context(tmp_path: Path) -> None:
    session = _session(tmp_path)
    invalid = InvalidNavSourceRecord(
        scheme_code="103063",
        scheme_name="Example Growth",
        isin_payout_or_growth="INF109K01AS1",
        isin_reinvestment=None,
        raw_nav_value="#DIV/0!",
        nav_date=date(2006, 4, 2),
        scheme_classification="Open Ended Schemes ( Equity Scheme - Large Cap Fund )",
        fund_house="Example Mutual Fund",
        rejection_reason="NAV is not a non-negative finite decimal",
    )

    stats = NormalizedNavPublisher(session).publish((invalid,), batch_id=_batch(session).id)

    issue = session.scalar(select(DataQualityIssueRecord))
    assert stats.rows_received == 1
    assert stats.rows_quarantined == 1
    assert stats.rows_inserted == 0
    assert session.scalar(select(func.count()).select_from(NavRevisionRecord)) == 0
    assert issue is not None and issue.issue_code == "INVALID_NAV_VALUE"
    assert "#DIV/0!" in issue.details
    session.close()
