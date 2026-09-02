import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiDistributionRecordSource,
    AmfiFundRecord,
    DistributionParseIssueRecord,
    DistributionSyncCheckpointRecord,
    DistributionSyncRunRecord,
    IngestionBatchRecord,
    SchemeOptionRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.amfi import AmfiDistributionParser
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.http import DownloadedSource
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.distribution_sync import (
    AmfiDistributionPublisher,
    DistributionSyncService,
)
from mf_strategy_tester.services.source_ingestion import SourceIngestionService

FIXTURES = Path(__file__).parents[1] / "fixtures" / "amfi"


def _completed_batch(session: Session) -> IngestionBatchRecord:
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
    return batch


def _database(tmp_path: Path) -> tuple[Session, ArtifactStore]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'distribution.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return factory(), ArtifactStore(tmp_path / "raw")


def test_distribution_publication_is_idempotent_and_labels_source_units(
    tmp_path: Path,
) -> None:
    session, _ = _database(tmp_path)
    first_batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="ICICI Prudential Mutual Fund",
            catalog_batch_id=first_batch.id,
        )
    )
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="120335",
            first_observed_nav_date=date(2026, 3, 10),
            last_observed_nav_date=date(2026, 3, 10),
            first_observed_batch_id=first_batch.id,
        )
    )
    session.commit()
    records = AmfiDistributionParser().parse_records((FIXTURES / "distributions.json").read_bytes())
    publisher = AmfiDistributionPublisher(session)

    first = publisher.publish(
        records, mutual_fund_id="20", source_scheme_id="712", batch_id=first_batch.id
    )
    session.commit()
    second_batch = _completed_batch(session)
    second = publisher.publish(
        records, mutual_fund_id="20", source_scheme_id="712", batch_id=second_batch.id
    )
    session.commit()
    correction_batch = _completed_batch(session)
    correction = (
        replace(
            records[2],
            raw_source_value="0.1700",
            source_value=Decimal("0.1700"),
        ),
    )
    corrected = publisher.publish(
        correction,
        mutual_fund_id="20",
        source_scheme_id="712",
        batch_id=correction_batch.id,
    )
    session.commit()

    assert (first.rows_inserted, first.rows_unchanged, first.rows_unresolved) == (3, 0, 2)
    assert (second.rows_inserted, second.rows_unchanged, second.rows_unresolved) == (0, 3, 2)
    assert (corrected.rows_inserted, corrected.rows_unchanged) == (1, 0)
    stored = list(
        session.scalars(
            select(AmfiDistributionRecord).order_by(AmfiDistributionRecord.record_date)
        ).all()
    )
    assert [record.source_unit for record in stored] == [
        "percentage",
        "amount",
        "amount",
        "amount",
    ]
    assert stored[0].raw_source_value == "6.00%"
    assert session.scalar(select(func.count()).select_from(AmfiDistributionRecordSource)) == 7
    session.close()


def test_distribution_publication_rejects_request_identity_mismatch(tmp_path: Path) -> None:
    session, _ = _database(tmp_path)
    records = AmfiDistributionParser().parse_records((FIXTURES / "distributions.json").read_bytes())

    with pytest.raises(ValueError, match="fund ID does not match"):
        AmfiDistributionPublisher(session).publish(
            records, mutual_fund_id="21", source_scheme_id="712", batch_id="unused"
        )
    session.close()


def test_distribution_publication_preserves_explicit_source_qualifiers(tmp_path: Path) -> None:
    session, _ = _database(tmp_path)
    batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="ICICI Prudential Mutual Fund",
            catalog_batch_id=batch.id,
        )
    )
    session.commit()
    records = AmfiDistributionParser().parse_records(
        (FIXTURES / "distributions_with_qualifiers.json").read_bytes()
    )

    first = AmfiDistributionPublisher(session).publish(
        records, mutual_fund_id="20", source_scheme_id="712", batch_id=batch.id
    )
    session.commit()
    repeated_batch = _completed_batch(session)
    second = AmfiDistributionPublisher(session).publish(
        records,
        mutual_fund_id="20",
        source_scheme_id="712",
        batch_id=repeated_batch.id,
    )
    session.commit()

    stored = list(
        session.scalars(
            select(AmfiDistributionRecord).order_by(AmfiDistributionRecord.record_date.desc())
        ).all()
    )
    assert (first.rows_inserted, second.rows_unchanged) == (2, 2)
    assert (stored[0].source_plan, stored[0].source_option) == ("Direct Plan", "IDCW")
    assert stored[1].source_plan is None
    assert stored[1].source_option is None
    session.close()


def test_distribution_publication_stores_structured_ratio(tmp_path: Path) -> None:
    session, _ = _database(tmp_path)
    batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="26",
            mutual_fund_name="Taurus Mutual Fund",
            catalog_batch_id=batch.id,
        )
    )
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="101209",
            first_observed_nav_date=date(2003, 10, 8),
            last_observed_nav_date=date(2003, 10, 8),
            first_observed_batch_id=batch.id,
        )
    )
    session.commit()
    records = AmfiDistributionParser().parse_records(
        (FIXTURES / "distribution_ratio.json").read_bytes()
    )

    stats = AmfiDistributionPublisher(session).publish(
        records,
        mutual_fund_id="26",
        source_scheme_id="615",
        batch_id=batch.id,
    )
    session.commit()

    stored = session.scalar(select(AmfiDistributionRecord))
    assert stats.rows_inserted == 1
    assert stats.rows_unresolved == 0
    assert stored is not None
    assert stored.source_unit == "ratio"
    assert stored.raw_source_value == "1:3"
    assert stored.source_value is None
    assert stored.ratio_numerator == 1
    assert stored.ratio_denominator == 3
    session.close()


def test_distribution_publication_stores_percentage_amount_annotation(
    tmp_path: Path,
) -> None:
    session, _ = _database(tmp_path)
    batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="28",
            mutual_fund_name="UTI Mutual Fund",
            catalog_batch_id=batch.id,
        )
    )
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code="100668",
            first_observed_nav_date=date(2006, 7, 19),
            last_observed_nav_date=date(2006, 7, 19),
            first_observed_batch_id=batch.id,
        )
    )
    session.commit()
    records = AmfiDistributionParser().parse_records(
        (FIXTURES / "distribution_composite_value.json").read_bytes()
    )

    stats = AmfiDistributionPublisher(session).publish(
        records,
        mutual_fund_id="28",
        source_scheme_id="273",
        batch_id=batch.id,
    )
    session.commit()

    stored = session.scalar(select(AmfiDistributionRecord))
    assert stats.rows_inserted == 1
    assert stats.rows_unresolved == 0
    assert stored is not None
    assert stored.source_unit == "percentage"
    assert stored.raw_source_value == "20% (Rs 2/- Per Unit"
    assert stored.source_value == Decimal("20")
    assert stored.annotated_amount_per_unit_inr == Decimal("2")
    session.close()


class DistributionDownloader:
    def __init__(self, distribution_payload: bytes) -> None:
        self.distribution_payload = distribution_payload
        self.urls: list[str] = []

    def download(self, url: str) -> DownloadedSource:
        self.urls.append(url)
        if "populate-scheme" in url:
            content = json.dumps(
                [{"scheme_id": "712", "scheme_name": "ICICI Prudential Multi-Asset Fund"}]
            ).encode()
        else:
            content = self.distribution_payload
        return DownloadedSource(
            content=content,
            media_type="application/json",
            status_code=200,
            final_url=url,
        )


def test_full_distribution_sync_resumes_completed_scheme_snapshots(tmp_path: Path) -> None:
    session, artifacts = _database(tmp_path)
    catalog_batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="ICICI Prudential Mutual Fund",
            catalog_batch_id=catalog_batch.id,
        )
    )
    session.commit()
    downloader = DistributionDownloader((FIXTURES / "distributions.json").read_bytes())
    repository = IngestionRepository(session)
    ingestion = SourceIngestionService(repository, artifacts, downloader)
    service = DistributionSyncService(session, ingestion, repository, artifacts)

    first = service.synchronize(
        mode="full",
        fund_ids=frozenset({"20"}),
        scheme_ids=frozenset({"712"}),
    )
    calls_after_first = len(downloader.urls)
    second = service.synchronize(
        mode="full",
        fund_ids=frozenset({"20"}),
        scheme_ids=frozenset({"712"}),
    )

    assert first.status == "completed"
    assert (first.funds_completed, first.schemes_completed, first.schemes_total) == (1, 1, 1)
    assert first.rows_inserted == 3
    assert second.status == "completed"
    assert second.schemes_completed == 1
    assert second.rows_received == 0
    assert len(downloader.urls) == calls_after_first + 1
    assert session.scalar(select(func.count()).select_from(DistributionSyncCheckpointRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DistributionSyncRunRecord)) == 2
    session.close()


def test_distribution_sync_quarantines_bad_rows_and_commits_good_rows(
    tmp_path: Path,
) -> None:
    session, artifacts = _database(tmp_path)
    catalog_batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="ICICI Prudential Mutual Fund",
            catalog_batch_id=catalog_batch.id,
        )
    )
    session.commit()
    payload = json.loads((FIXTURES / "distributions.json").read_bytes())
    invalid = dict(payload["data"][0])
    invalid["SD_ID"] = 999999
    invalid["Div_year"] = "2008-01-01T00:00:00.000Z"
    invalid["year"] = "2008"
    invalid["Rate_of_div"] = "not disclosed"
    payload["data"].append(invalid)
    downloader = DistributionDownloader(json.dumps(payload).encode())
    repository = IngestionRepository(session)
    ingestion = SourceIngestionService(repository, artifacts, downloader)

    result = DistributionSyncService(session, ingestion, repository, artifacts).synchronize(
        mode="full",
        fund_ids=frozenset({"20"}),
        scheme_ids=frozenset({"712"}),
        quarantine_record_errors=True,
    )

    issue = session.scalar(select(DistributionParseIssueRecord))
    checkpoint = session.get(DistributionSyncCheckpointRecord, ("20", "712"))
    source_batch = session.scalar(
        select(IngestionBatchRecord).where(
            IngestionBatchRecord.source_type == "distributions",
            IngestionBatchRecord.id != catalog_batch.id,
        )
    )
    assert result.status == "completed_with_issues"
    assert (result.rows_received, result.rows_inserted, result.rows_rejected) == (4, 3, 1)
    assert session.scalar(select(func.count()).select_from(AmfiDistributionRecord)) == 3
    assert issue is not None
    assert issue.status == "open"
    assert issue.record_number == 4
    assert issue.raw_record == invalid
    assert checkpoint is not None
    assert (checkpoint.rows_received, checkpoint.rows_accepted, checkpoint.rows_rejected) == (
        4,
        3,
        1,
    )
    assert source_batch is not None
    assert (source_batch.rows_received, source_batch.rows_accepted, source_batch.rows_rejected) == (
        4,
        3,
        1,
    )
    session.close()


def test_retry_quarantined_resolves_exact_raw_record_signature(tmp_path: Path) -> None:
    session, artifacts = _database(tmp_path)
    catalog_batch = _completed_batch(session)
    session.add(
        AmfiFundRecord(
            mutual_fund_id="20",
            mutual_fund_name="ICICI Prudential Mutual Fund",
            catalog_batch_id=catalog_batch.id,
        )
    )
    session.commit()
    payload = (FIXTURES / "distributions.json").read_bytes()
    downloader = DistributionDownloader(payload)
    repository = IngestionRepository(session)
    ingestion = SourceIngestionService(repository, artifacts, downloader)
    service = DistributionSyncService(session, ingestion, repository, artifacts)
    service.synchronize(
        mode="full",
        fund_ids=frozenset({"20"}),
        scheme_ids=frozenset({"712"}),
    )
    parsed_record = AmfiDistributionParser().parse_records(payload)[0]
    raw_record = json.loads(payload)["data"][0]
    issue = DistributionParseIssueRecord(
        ingestion_batch_id=catalog_batch.id,
        mutual_fund_id="20",
        source_scheme_id="712",
        record_number=1,
        issue_code="SOURCE_PARSE_ERROR",
        error_details="previous parser did not support this record",
        raw_record=raw_record,
        source_record_signature=parsed_record.source_record_signature,
        status="open",
    )
    session.add(issue)
    session.commit()

    result = service.synchronize(
        mode="full",
        fund_ids=frozenset({"20"}),
        scheme_ids=frozenset({"712"}),
        retry_quarantined=True,
    )
    session.refresh(issue)

    assert result.status == "completed"
    assert result.rows_unchanged == 3
    assert issue.status == "resolved"
    assert issue.resolved_at is not None
    assert issue.resolved_batch_id is not None
    session.close()
