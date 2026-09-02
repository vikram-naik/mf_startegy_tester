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
    DistributionIdentifierReviewRecord,
    IngestionBatchRecord,
    SchemeOptionRecord,
    SourceArtifactRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.data_quality import DataQualityReportService
from mf_strategy_tester.services.distribution_identity import DistributionIdentifierReviewService


def _database(
    tmp_path: Path,
) -> tuple[Session, IngestionBatchRecord, IngestionBatchRecord]:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'identity.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    artifact = SourceArtifactRecord(
        sha256="a" * 64,
        byte_size=10,
        media_type="application/json",
        storage_path="sha256/aa/artifact",
    )
    session.add(artifact)
    session.flush()
    batch = IngestionBatchRecord(
        provider="amfi",
        source_type="distributions",
        source_url="https://example.invalid/distributions",
        request_parameters={"MF_ID": "20", "strSDid": "129"},
        parser_version="test",
        status="completed",
        artifact_id=artifact.id,
    )
    session.add(batch)
    session.flush()
    identity_artifact = SourceArtifactRecord(
        sha256="c" * 64,
        byte_size=10,
        media_type="application/json",
        storage_path="sha256/cc/artifact",
    )
    session.add(identity_artifact)
    session.flush()
    identity_batch = IngestionBatchRecord(
        provider="amfi",
        source_type="scheme_details",
        source_url="https://example.invalid/scheme-details",
        request_parameters={"MF_ID": "20", "scheme_id": "129"},
        parser_version="test",
        status="completed",
        artifact_id=identity_artifact.id,
    )
    session.add(identity_batch)
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
            amfi_scheme_code="120597",
            first_observed_nav_date=date(2013, 1, 1),
            last_observed_nav_date=date(2026, 1, 1),
            first_observed_batch_id=batch.id,
        )
    )
    session.flush()
    distribution_record = AmfiDistributionRecord(
        mutual_fund_id="20",
        source_scheme_id="129",
        source_option_id="120286",
        scheme_name="Example Fund",
        nav_name="Example retired option",
        record_date=date(2014, 5, 30),
        raw_source_value="0",
        source_value=Decimal("0"),
        source_unit="amount",
        content_signature="b" * 64,
        first_observed_batch_id=batch.id,
    )
    session.add(distribution_record)
    session.flush()
    session.add(
        AmfiDistributionRecordSource(
            distribution_record_id=distribution_record.id,
            ingestion_batch_id=batch.id,
        )
    )
    session.commit()
    return session, batch, identity_batch


def test_source_only_identifier_survey_is_append_only_and_idempotent(tmp_path: Path) -> None:
    session, batch, _ = _database(tmp_path)
    service = DistributionIdentifierReviewService(session)

    first = service.survey_unmatched_source_identifiers()
    second = service.survey_unmatched_source_identifiers()

    review = session.scalar(select(DistributionIdentifierReviewRecord))
    assert (first.identifiers_reviewed, first.reviews_inserted, first.reviews_unchanged) == (
        1,
        1,
        0,
    )
    assert (second.identifiers_reviewed, second.reviews_inserted, second.reviews_unchanged) == (
        1,
        0,
        1,
    )
    assert session.scalar(select(func.count()).select_from(DistributionIdentifierReviewRecord)) == 1
    assert review is not None
    assert review.source_option_id == "120286"
    assert review.status == "source_only"
    assert review.evidence_batch_id == batch.id

    report = DataQualityReportService(session).build_report()
    assert report.non_positive_distribution_value_rows == 1
    assert report.non_positive_distribution_value_identifiers == 1
    assert report.distribution_identifier_review_counts == {
        "unreviewed": 0,
        "source_only": 1,
        "mapped": 0,
        "source_error": 0,
    }
    assert report.unresolved_distribution_identifiers[0].review_status == "source_only"
    session.close()


def test_mapped_review_requires_an_existing_exact_scheme_code(tmp_path: Path) -> None:
    session, batch, identity_batch = _database(tmp_path)
    service = DistributionIdentifierReviewService(session)

    with pytest.raises(ValueError, match="requires an AMFI scheme code"):
        service.record_review(
            source_option_id="120286",
            status="mapped",
            evidence_batch_id=identity_batch.id,
            evidence_details="official correction",
        )
    with pytest.raises(LookupError, match="does not exist"):
        service.record_review(
            source_option_id="120286",
            status="mapped",
            evidence_batch_id=identity_batch.id,
            evidence_details="official correction",
            matched_amfi_scheme_code="999999",
        )
    with pytest.raises(ValueError, match="proves source occurrence"):
        service.record_review(
            source_option_id="120286",
            status="mapped",
            evidence_batch_id=batch.id,
            evidence_details="unsupported mapping claim",
            matched_amfi_scheme_code="120597",
        )

    review, inserted = service.record_review(
        source_option_id="120286",
        status="mapped",
        evidence_batch_id=identity_batch.id,
        evidence_details="official artifact explicitly establishes identifier continuity",
        matched_amfi_scheme_code="120597",
    )

    assert inserted is True
    assert review.status == "mapped"
    assert review.matched_amfi_scheme_code == "120597"
    session.close()
