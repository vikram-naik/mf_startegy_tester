import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSchemeRecord,
    AdvisorkhojCatalogSnapshotRecord,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    IngestionBatchRecord,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.distribution_identity_backlog import (
    DistributionIdentityBacklogService,
)


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'identity-backlog.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    batch = IngestionBatchRecord(
        id="batch",
        provider="amfi",
        source_type="distributions",
        source_url="https://example.invalid/source",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    session.add(batch)
    session.flush()

    _add_rta_capture(
        session,
        batch_id=batch.id,
        capture_id="cams-unresolved",
        provider="cams",
        rows=100,
        status="unresolved",
        candidate_codes=[],
    )
    session.add(
        RtaSchemeMappingReviewRecord(
            id="older-review-cams-unresolved",
            scheme_capture_id="cams-unresolved",
            status="ambiguous",
            mapping_method="none",
            evidence_details=json.dumps({"candidate_codes": ["old-1", "old-2"]}),
            review_signature="0" * 64,
            reviewed_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
    )
    _add_rta_capture(
        session,
        batch_id=batch.id,
        capture_id="kfintech-ambiguous",
        provider="kfintech",
        rows=50,
        status="ambiguous",
        candidate_codes=["100", "101"],
    )

    catalog = AdvisorkhojCatalogSnapshotRecord(
        id="catalog",
        ingestion_batch_id=batch.id,
        catalog_sha256="a" * 64,
        amc_count=1,
        category_query_count=1,
        scheme_count=3,
        captured_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    session.add(catalog)
    session.flush()
    _add_advisorkhoj_capture(
        session,
        batch_id=batch.id,
        catalog_id=catalog.id,
        capture_id="ak-unresolved",
        rows=75,
        status="unresolved",
        candidate_codes=["200"],
        qualifying_codes=[],
        conflicts=True,
    )
    _add_advisorkhoj_capture(
        session,
        batch_id=batch.id,
        catalog_id=catalog.id,
        capture_id="ak-ambiguous",
        rows=25,
        status="ambiguous",
        candidate_codes=["201", "202"],
        qualifying_codes=["201", "202"],
        conflicts=False,
    )
    _add_advisorkhoj_capture(
        session,
        batch_id=batch.id,
        catalog_id=catalog.id,
        capture_id="ak-empty",
        rows=0,
        status="unresolved",
        candidate_codes=[],
        qualifying_codes=[],
        conflicts=False,
    )
    session.commit()
    return session


def _add_rta_capture(
    session: Session,
    *,
    batch_id: str,
    capture_id: str,
    provider: str,
    rows: int,
    status: str,
    candidate_codes: list[str],
) -> None:
    capture = RtaSchemeCaptureRecord(
        id=capture_id,
        provider=provider,
        rta_fund_code="fund",
        rta_fund_name=f"{provider} fund",
        rta_scheme_code=f"scheme-{capture_id}",
        source_scheme_name=f"Source {capture_id}",
        source_url="https://example.invalid/rta",
        source_payload_sha256=("b" if provider == "cams" else "c") * 64,
        plan_type="regular",
        option_variant="payout",
        source_row_count=rows,
        capture_signature=("d" if provider == "cams" else "e") * 64,
        ingestion_batch_id=batch_id,
        captured_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    session.add(capture)
    session.flush()
    session.add(
        RtaSchemeMappingReviewRecord(
            id=f"review-{capture_id}",
            scheme_capture_id=capture.id,
            status=status,
            mapping_method="none",
            evidence_details=json.dumps({"candidate_codes": candidate_codes}),
            review_signature=("f" if provider == "cams" else "1") * 64,
            reviewed_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
    )


def _add_advisorkhoj_capture(
    session: Session,
    *,
    batch_id: str,
    catalog_id: str,
    capture_id: str,
    rows: int,
    status: str,
    candidate_codes: list[str],
    qualifying_codes: list[str],
    conflicts: bool,
) -> None:
    index = {"ak-unresolved": "2", "ak-ambiguous": "3", "ak-empty": "4"}[capture_id]
    catalog_scheme = AdvisorkhojCatalogSchemeRecord(
        id=f"catalog-{capture_id}",
        catalog_snapshot_id=catalog_id,
        amc_name="Example Mutual Fund",
        category="Debt",
        scheme_name=f"Source {capture_id}",
        content_signature=index * 64,
    )
    session.add(catalog_scheme)
    session.flush()
    capture = AdvisorkhojSchemeCaptureRecord(
        id=capture_id,
        catalog_scheme_id=catalog_scheme.id,
        source_url="https://example.invalid/advisorkhoj",
        source_payload_sha256=index * 64,
        plan_type="direct",
        option_variant="payout",
        source_frequency="monthly",
        source_row_count=rows,
        capture_signature=chr(ord(index) + 3) * 64,
        ingestion_batch_id=batch_id,
        captured_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    session.add(capture)
    session.flush()
    session.add(
        AdvisorkhojSchemeMappingReviewRecord(
            id=f"review-{capture_id}",
            scheme_capture_id=capture.id,
            status=status,
            mapping_method="none",
            evidence_details=json.dumps(
                {
                    "candidate_codes": candidate_codes,
                    "qualifying_codes": qualifying_codes,
                    "candidates": [{"conflicts": ([{"date": "2026-01-01"}] if conflicts else [])}],
                }
            ),
            review_signature=chr(ord(index) + 6) * 64,
        )
    )


def test_report_uses_latest_unique_backlog_and_ranks_nonempty_source_impact(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)

    report = DistributionIdentityBacklogService(session).build_report(ranked_capture_limit=3)

    assert report.nonempty_backlog_captures == 4
    assert report.nonempty_backlog_source_rows == 250
    assert [item.capture_id for item in report.ranked_captures] == [
        "cams-unresolved",
        "ak-unresolved",
        "kfintech-ambiguous",
    ]
    summaries = {item.provider: item for item in report.provider_summaries}
    assert summaries["cams"].unresolved_nonempty_captures == 1
    assert summaries["cams"].unresolved_source_rows == 100
    assert summaries["kfintech"].ambiguous_nonempty_captures == 1
    assert summaries["kfintech"].ambiguous_source_rows == 50
    assert summaries["advisorkhoj"].unresolved_source_rows == 75
    assert summaries["advisorkhoj"].ambiguous_source_rows == 25
    assert summaries["advisorkhoj"].empty_unresolved_captures == 1
    reasons = {(item.provider, item.status, item.reason): item for item in report.reason_summaries}
    assert reasons[("cams", "unresolved", "no_exact_name_plan_nav_candidate")].captures == 1
    assert (
        reasons[("kfintech", "ambiguous", "multiple_exact_name_plan_nav_candidates")].captures == 1
    )
    assert reasons[("advisorkhoj", "unresolved", "comparable_nav_conflict")].source_rows == 75
    assert (
        reasons[("advisorkhoj", "ambiguous", "multiple_qualifying_nav_candidates")].source_rows
        == 25
    )
    session.close()


def test_report_rejects_invalid_limit_and_inconsistent_mapping_evidence(tmp_path: Path) -> None:
    session = _session(tmp_path)
    service = DistributionIdentityBacklogService(session)

    with pytest.raises(ValueError, match="between 1 and 1000"):
        service.build_report(ranked_capture_limit=0)

    review = session.get(RtaSchemeMappingReviewRecord, "review-cams-unresolved")
    assert review is not None
    review.evidence_details = json.dumps({"candidate_codes": ["unexpected"]})
    session.commit()
    with pytest.raises(RuntimeError, match=r"unresolved RTA review.*has candidate codes"):
        service.build_report()
    session.close()
