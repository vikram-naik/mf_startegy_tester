from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.routes.data import list_scheme_distributions
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    DistributionCoverageAssessmentRecord,
    DistributionEventRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    OfficialDistributionRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    SourceArtifactRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.hdfc import HdfcNoticeDistribution, HdfcSchemeIdentity
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.data_quality import DataQualityReportService
from mf_strategy_tester.services.distribution_coverage import (
    DistributionCoverageAssessmentService,
)
from mf_strategy_tester.services.official_distribution_notice import (
    HdfcDistributionNoticeService,
    OfficialDistributionConflictError,
)
from mf_strategy_tester.services.source_ingestion import SourceIngestionService


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'official-notice.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    notice_artifact = SourceArtifactRecord(
        id="notice-artifact",
        sha256="a" * 64,
        byte_size=100,
        media_type="application/pdf",
        storage_path="sha256/aa/" + "a" * 64,
    )
    identity_artifact = SourceArtifactRecord(
        id="identity-artifact",
        sha256="b" * 64,
        byte_size=100,
        media_type="application/pdf",
        storage_path="sha256/bb/" + "b" * 64,
    )
    notice_batch = IngestionBatchRecord(
        id="notice-batch",
        provider="hdfc_amc",
        source_type="distribution_notice",
        source_url="https://files.hdfcfund.com/notice.pdf",
        final_url="https://files.hdfcfund.com/notice.pdf",
        request_parameters={},
        parser_version="test",
        status="completed",
        artifact_id=notice_artifact.id,
        rows_received=2,
        rows_accepted=2,
    )
    identity_batch = IngestionBatchRecord(
        id="identity-batch",
        provider="hdfc_amc",
        source_type="scheme_summary",
        source_url="https://files.hdfcfund.com/summary.pdf",
        final_url="https://files.hdfcfund.com/summary.pdf",
        request_parameters={},
        parser_version="test",
        status="completed",
        artifact_id=identity_artifact.id,
        rows_received=4,
        rows_accepted=4,
    )
    session.add_all([notice_artifact, identity_artifact, notice_batch, identity_batch])
    session.commit()
    for index, (scheme_code, plan_type) in enumerate(
        (("100120", "regular"), ("118969", "direct")), start=1
    ):
        session.add(
            SchemeOptionRecord(
                amfi_scheme_code=scheme_code,
                first_observed_nav_date=date(2020, 1, 1),
                last_observed_nav_date=date(2026, 2, 20),
                first_observed_batch_id=identity_batch.id,
            )
        )
        session.flush()
        metadata = SchemeMetadataVersionRecord(
            id=str(index) * 64,
            amfi_scheme_code=scheme_code,
            scheme_name="HDFC Balanced Advantage Fund",
            fund_house_name="HDFC Mutual Fund",
            scheme_classification="Hybrid",
            plan_type="unknown" if plan_type == "regular" else plan_type,
            option_type="idcw",
            classification_method="test",
            first_observed_batch_id=identity_batch.id,
        )
        session.add(metadata)
        session.flush()
        session.add(
            NavRevisionRecord(
                id=f"nav-{scheme_code}",
                amfi_scheme_code=scheme_code,
                nav_date=date(2026, 2, 20),
                nav_value=Decimal("40"),
                metadata_version_id=metadata.id,
                revision_number=1,
                content_signature=("c" if index == 1 else "d") * 64,
                quality_status="valid",
                is_current=True,
                first_observed_batch_id=identity_batch.id,
            )
        )
    session.commit()
    return session


def _service(tmp_path: Path, session: Session) -> HdfcDistributionNoticeService:
    return HdfcDistributionNoticeService(
        session,
        cast(SourceIngestionService, object()),
        IngestionRepository(session),
        ArtifactStore(tmp_path / "raw"),
    )


def _resolved() -> tuple[tuple[HdfcNoticeDistribution, HdfcSchemeIdentity], ...]:
    return tuple(
        (
            HdfcNoticeDistribution(
                scheme_name="HDFC Balanced Advantage Fund",
                plan_type=plan_type,
                option_label="IDCW Option (Payout and Reinvestment)",
                record_date=date(2026, 2, 25),
                raw_amount_per_unit_inr="0.250",
                amount_per_unit_inr=Decimal("0.250"),
            ),
            HdfcSchemeIdentity(
                amfi_scheme_code=scheme_code,
                scheme_name="HDFC Balanced Advantage Fund",
                plan_type=plan_type,
                option_type="idcw",
            ),
        )
        for scheme_code, plan_type in (("100120", "regular"), ("118969", "direct"))
    )


def test_official_notice_publication_is_exact_code_and_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    service = _service(tmp_path, session)
    notice_batch = session.get_one(IngestionBatchRecord, "notice-batch")
    identity_batch = session.get_one(IngestionBatchRecord, "identity-batch")
    pairs = _resolved()
    resolved = service._resolve_rows(
        tuple(pair[0] for pair in pairs), tuple(pair[1] for pair in pairs)
    )

    source_records, inserted = service._persist_source_records(
        resolved, notice_batch=notice_batch, identity_batch=identity_batch
    )
    first = service._publish_canonical(source_records)
    second = service._publish_canonical(source_records)

    assert inserted == 2
    assert first.events_inserted == 2
    assert first.revisions_inserted == 2
    assert second.events_inserted == 0
    assert second.revisions_inserted == 0
    assert second.rows_unchanged == 2
    assert session.scalar(select(func.count()).select_from(OfficialDistributionRecord)) == 2
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 2
    assert (
        session.scalar(
            select(func.count()).select_from(DistributionEventRevisionOfficialSourceRecord)
        )
        == 2
    )
    coverage = DistributionCoverageAssessmentService(session).assess()
    direct_assessment = session.get_one(
        DistributionCoverageAssessmentRecord, (coverage.run_id, "118969")
    )
    assert direct_assessment.coverage_status == "events_present"
    assert direct_assessment.source_row_count == 1
    assert direct_assessment.canonical_source_row_count == 1
    browser = list_scheme_distributions("118969", session, limit=50, offset=0)
    provenance = browser.items[0].revisions[0].sources[0]
    assert provenance.provider == "hdfc_amc"
    assert provenance.source_kind == "amc_distribution_notice"
    assert provenance.identity_evidence_batch_id == "identity-batch"
    assert provenance.identity_artifact_sha256 == "b" * 64
    quality_report = DataQualityReportService(session).build_report()
    assert quality_report.canonical_distribution_events == 2
    assert quality_report.canonical_distribution_revisions == 2
    assert quality_report.canonical_distribution_source_links == 2
    session.close()


def test_official_notice_conflict_preserves_source_and_blocks_canonical_change(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    service = _service(tmp_path, session)
    notice_batch = session.get_one(IngestionBatchRecord, "notice-batch")
    identity_batch = session.get_one(IngestionBatchRecord, "identity-batch")
    existing_run = DistributionNormalizationRunRecord(
        id="existing-run",
        status="completed",
        normalization_version="test",
        source_rows_examined=1,
        candidate_rows=1,
        blocked_rows=0,
        events_inserted=1,
        revisions_inserted=1,
        rows_unchanged=0,
    )
    event = DistributionEventRecord(
        id="existing-event",
        amfi_scheme_code="100120",
        record_date=date(2026, 2, 25),
        event_type="idcw_cash",
    )
    session.add_all([existing_run, event])
    session.flush()
    session.add(
        DistributionEventRevisionRecord(
            id="existing-revision",
            distribution_event_id=event.id,
            amount_per_unit_inr=Decimal("0.300"),
            revision_number=1,
            content_signature="e" * 64,
            normalization_version="test",
            normalization_run_id=existing_run.id,
            is_current=True,
        )
    )
    session.commit()
    source_records, _ = service._persist_source_records(
        (_resolved()[0],), notice_batch=notice_batch, identity_batch=identity_batch
    )

    with pytest.raises(OfficialDistributionConflictError, match="conflicts"):
        service._publish_canonical(source_records)

    assert session.scalar(select(func.count()).select_from(OfficialDistributionRecord)) == 1
    current = session.scalar(
        select(DistributionEventRevisionRecord).where(
            DistributionEventRevisionRecord.distribution_event_id == event.id,
            DistributionEventRevisionRecord.is_current.is_(True),
        )
    )
    assert current is not None
    assert current.amount_per_unit_inr == Decimal("0.300")
    failed_run = session.scalar(
        select(DistributionNormalizationRunRecord)
        .where(DistributionNormalizationRunRecord.status == "failed")
        .order_by(DistributionNormalizationRunRecord.started_at.desc())
    )
    assert failed_run is not None
    assert "OfficialDistributionConflictError" in (failed_run.error_details or "")
    session.close()


def test_official_identity_cannot_override_an_explicit_local_plan_conflict(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    service = _service(tmp_path, session)
    direct_metadata = session.get_one(SchemeMetadataVersionRecord, "2" * 64)
    direct_metadata.plan_type = "regular"
    session.commit()
    direct_row, direct_identity = _resolved()[1]

    with pytest.raises(RuntimeError, match="local metadata conflicts"):
        service._resolve_rows((direct_row,), (direct_identity,))

    session.close()
