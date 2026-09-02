import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.routes.data import list_scheme_distributions
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSchemeRecord,
    AdvisorkhojCatalogSnapshotRecord,
    AdvisorkhojDistributionRecord,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    DistributionCoverageAssessmentRecord,
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    OfficialDistributionRecord,
    RtaDistributionIssueRecord,
    RtaDistributionRecord,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    utc_now,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.errors import SourceParseError
from mf_strategy_tester.ingestion.rta import RtaCaptureParser
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.distribution_coverage import (
    DistributionCoverageAssessmentService,
)
from mf_strategy_tester.services.rta_distribution import (
    RtaDistributionImportService,
    _scheme_core,
)


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'rta.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    batch = IngestionBatchRecord(
        id="nav-batch",
        provider="amfi",
        source_type="historical_nav",
        source_url="https://www.amfiindia.com/nav-history-download",
        request_parameters={},
        parser_version="test",
        status="completed",
    )
    option = SchemeOptionRecord(
        amfi_scheme_code="118969",
        first_observed_nav_date=date(2012, 1, 1),
        last_observed_nav_date=date(2026, 8, 14),
        first_observed_batch_id=batch.id,
    )
    session.add_all([batch, option])
    session.flush()
    metadata = SchemeMetadataVersionRecord(
        id="m" * 64,
        amfi_scheme_code=option.amfi_scheme_code,
        scheme_name="HDFC Balanced Advantage Fund - IDCW Plan - Direct Plan",
        fund_house_name="HDFC Mutual Fund",
        scheme_classification="Hybrid",
        plan_type="direct",
        option_type="idcw",
        classification_method="test",
        first_observed_batch_id=batch.id,
    )
    session.add(metadata)
    session.flush()
    nav = NavRevisionRecord(
        id="nav",
        amfi_scheme_code=option.amfi_scheme_code,
        nav_date=date(2026, 8, 14),
        nav_value=Decimal("44.1234"),
        metadata_version_id=metadata.id,
        revision_number=1,
        content_signature="n" * 64,
        quality_status="valid",
        is_current=True,
        first_observed_batch_id=batch.id,
    )
    session.add(nav)
    session.commit()
    return session


def _capture(
    *,
    amount: str = "0.2500",
    latest_nav: bool = True,
    record_date: str = "2026-07-27",
) -> bytes:
    source_payload = "\n<html><table>official rendered RTA response</table></html>\r\n"
    value = {
        "schema_version": 1,
        "provider": "cams",
        "source_url": "https://www.camsonline.com/InvestorServices/COL_ISNAV.aspx",
        "captured_at": "2026-08-17T12:00:00+05:30",
        "fund": {"code": "01", "name": "HDFC Mutual Fund"},
        "scheme": {
            "code": "GFDT",
            "name": "HDFC Balanced Advantage Fund - Direct Plan - IDCW Payout",
            "plan_type": "direct",
            "option_variant": "payout",
            "latest_nav_date": "2026-08-14" if latest_nav else None,
            "latest_nav_value": "44.1234" if latest_nav else None,
        },
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
        "records": [
            {
                "record_date": record_date,
                "individual_amount": amount,
                "non_individual_amount": "0.0000",
                "ex_nav": None,
                "cum_nav": None,
                "source_terminology": "CAMS IDCW history",
            }
        ],
    }
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def _service(tmp_path: Path, session: Session) -> RtaDistributionImportService:
    return RtaDistributionImportService(
        session, IngestionRepository(session), ArtifactStore(tmp_path / "raw")
    )


def _seed_advisorkhoj_canonical(session: Session) -> DistributionEventRevisionRecord:
    prior_run = DistributionNormalizationRunRecord(
        id="advisorkhoj-run",
        status="completed",
        normalization_version="test-advisorkhoj",
        source_rows_examined=1,
        candidate_rows=1,
        blocked_rows=0,
        events_inserted=1,
        revisions_inserted=1,
        rows_unchanged=0,
    )
    catalog = AdvisorkhojCatalogSnapshotRecord(
        id="advisorkhoj-catalog",
        ingestion_batch_id="nav-batch",
        catalog_sha256="a" * 64,
        amc_count=1,
        category_query_count=1,
        scheme_count=1,
        captured_at=utc_now(),
    )
    catalog_scheme = AdvisorkhojCatalogSchemeRecord(
        id="advisorkhoj-scheme",
        catalog_snapshot_id=catalog.id,
        amc_name="HDFC Mutual Fund",
        category="Hybrid",
        scheme_name="HDFC Balanced Advantage Fund - Direct IDCW",
        content_signature="b" * 64,
    )
    capture = AdvisorkhojSchemeCaptureRecord(
        id="advisorkhoj-capture",
        catalog_scheme_id=catalog_scheme.id,
        source_url="https://www.advisorkhoj.com/source",
        source_payload_sha256="c" * 64,
        plan_type="direct",
        option_variant="payout",
        source_frequency="monthly",
        source_row_count=1,
        capture_signature="d" * 64,
        ingestion_batch_id="nav-batch",
        captured_at=utc_now(),
    )
    mapping = AdvisorkhojSchemeMappingReviewRecord(
        id="advisorkhoj-mapping",
        scheme_capture_id=capture.id,
        status="mapped",
        amfi_scheme_code="118969",
        mapping_method="nav_fingerprint",
        evidence_details="three exact AMFI NAV matches",
        review_signature="e" * 64,
    )
    source = AdvisorkhojDistributionRecord(
        id="advisorkhoj-source",
        scheme_capture_id=capture.id,
        record_date=date(2026, 7, 27),
        raw_amount_per_unit_inr="0.3000",
        amount_per_unit_inr=Decimal("0.3000"),
        raw_reference_nav="43.2730",
        reference_nav=Decimal("43.2730"),
        raw_yield_percent="0.6933",
        yield_percent=Decimal("0.6933"),
        is_positive_cash_distribution=True,
        quality_status="valid",
        content_signature="f" * 64,
        first_observed_batch_id="nav-batch",
    )
    event = DistributionEventRecord(
        id="advisorkhoj-event",
        amfi_scheme_code="118969",
        record_date=date(2026, 7, 27),
        event_type="idcw_cash",
    )
    session.add_all([prior_run, catalog])
    session.flush()
    session.add(catalog_scheme)
    session.flush()
    session.add(capture)
    session.flush()
    session.add_all([mapping, source, event])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id="advisorkhoj-revision",
        distribution_event_id=event.id,
        amount_per_unit_inr=Decimal("0.3000"),
        revision_number=1,
        content_signature="g" * 64,
        normalization_version="test-advisorkhoj",
        normalization_run_id=prior_run.id,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    session.add(
        DistributionEventRevisionAdvisorkhojSourceRecord(
            distribution_event_revision_id=revision.id,
            advisorkhoj_distribution_record_id=source.id,
            mapping_review_id=mapping.id,
        )
    )
    session.commit()
    return revision


def test_rta_capture_parser_verifies_embedded_source_checksum() -> None:
    value = json.loads(_capture())
    value["source_payload_sha256"] = "0" * 64
    payload = (json.dumps(value) + "\n").encode()

    with pytest.raises(SourceParseError, match="checksum mismatch"):
        RtaCaptureParser().parse_records(payload)


def test_cams_parser_normalizes_binary_float_noise_but_retains_raw_amount() -> None:
    capture = RtaCaptureParser().parse_records(_capture(amount="1.4000000000000001"))[0]

    assert capture.rows[0].raw_individual_amount == "1.4000000000000001"
    assert capture.rows[0].individual_amount_per_unit_inr == Decimal("1.4000000000")


def test_cams_parser_rejects_unexplained_precision_beyond_ten_decimals() -> None:
    with pytest.raises(SourceParseError, match="unsupported precision beyond 10 decimals"):
        RtaCaptureParser().parse_records(_capture(amount="1.12345678901"))


def test_cams_float_noise_correction_creates_idempotent_versioned_source_row(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams-noise.jsonl"
    capture_file.write_bytes(_capture(amount="1.4000000000000001"))
    service = _service(tmp_path, session)

    first = service.import_file(capture_file)
    second = service.import_file(capture_file)

    assert first.source_rows_inserted == 1
    assert second.source_rows_inserted == 0
    source = session.scalar(select(RtaDistributionRecord))
    assert source is not None
    assert source.raw_individual_amount == "1.4000000000000001"
    assert source.individual_amount_per_unit_inr == Decimal("1.4000000000")
    session.close()


def test_cams_parser_revision_supersedes_only_the_same_raw_rta_source(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    payload = _capture(amount="1.4000000000000001")
    parsed_capture = RtaCaptureParser().parse_records(payload)[0]
    capture_file = tmp_path / "cams-noise-revision.jsonl"
    capture_file.write_bytes(payload)
    service = _service(tmp_path, session)
    batch = service._capture_batch("cams", payload, 1)
    captures, _ = service._persist_captures((parsed_capture,), batch)
    mapping = RtaSchemeMappingReviewRecord(
        id="legacy-mapping",
        scheme_capture_id=captures[0].id,
        status="mapped",
        amfi_scheme_code="118969",
        mapping_method="manual",
        evidence_details="legacy parser mapping",
        review_signature="p" * 64,
    )
    source = RtaDistributionRecord(
        id="legacy-noisy-source",
        scheme_capture_id=captures[0].id,
        record_date=date(2026, 7, 27),
        raw_individual_amount="1.4000000000000001",
        individual_amount_per_unit_inr=Decimal("1.4000000000000001"),
        raw_non_individual_amount="0.0000",
        non_individual_amount_per_unit_inr=Decimal("0.0000"),
        ex_nav=None,
        cum_nav=None,
        source_unit="inr_per_unit",
        source_terminology="CAMS IDCW history",
        content_signature="q" * 64,
        first_observed_batch_id=batch.id,
    )
    run = DistributionNormalizationRunRecord(
        id="legacy-noisy-run",
        status="completed",
        normalization_version="rta-distribution-legacy",
        source_rows_examined=1,
        candidate_rows=1,
        blocked_rows=0,
        events_inserted=1,
        revisions_inserted=1,
        rows_unchanged=0,
    )
    event_record = DistributionEventRecord(
        id="legacy-noisy-event",
        amfi_scheme_code="118969",
        record_date=date(2026, 7, 27),
        event_type="idcw_cash",
    )
    session.add_all([mapping, source, run, event_record])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id="legacy-noisy-revision",
        distribution_event_id=event_record.id,
        amount_per_unit_inr=Decimal("1.4000000000000001"),
        revision_number=1,
        content_signature="r" * 64,
        normalization_version="rta-distribution-legacy",
        normalization_run_id=run.id,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    session.add(
        DistributionEventRevisionRtaSourceRecord(
            distribution_event_revision_id=revision.id,
            rta_distribution_record_id=source.id,
            mapping_review_id=mapping.id,
        )
    )
    session.commit()

    result = service.import_file(capture_file)

    assert result.amount_conflicts == 0
    assert result.revisions_inserted == 1
    assert session.get_one(DistributionEventRevisionRecord, revision.id).is_current is False
    current = session.scalar(
        select(DistributionEventRevisionRecord).where(
            DistributionEventRevisionRecord.is_current.is_(True)
        )
    )
    assert current is not None
    assert current.revision_number == 2
    assert current.amount_per_unit_inr == Decimal("1.4000000000")
    assert session.scalar(select(func.count()).select_from(RtaDistributionRecord)) == 2
    session.close()


def test_rta_scheme_core_ignores_cams_reinvestment_and_former_name_decorations() -> None:
    source = (
        "Bandhan Short Duration Fund-Regular Plan-Quarterly IDCW-RE "
        "(Formerly known as Bandhan Bond Fund Short Term Plan-Regular Plan-Quarterly IDCW-RE)"
    )
    amfi = "Bandhan Short Duration Fund - Regular Plan - Quarterly IDCW"

    assert _scheme_core(source) == _scheme_core(amfi)
    assert _scheme_core("Bandhan Balanced Advantage Fund-IDCW-Reinv Ex") == (
        _scheme_core("Bandhan Balanced Advantage Fund-IDCW")
    )
    assert _scheme_core("Bandhan Floater Fund-IDCW Re investment Exchange") == (
        _scheme_core("Bandhan Floater Fund-IDCW")
    )
    assert _scheme_core("Bandhan Financial Services Fund-IDCW (Re-inv)") == (
        _scheme_core("Bandhan Financial Services Fund-IDCW")
    )
    assert _scheme_core(
        "Bandhan CRISIL IBX 90:10 SDL Plus Gilt Sep 2027 Index Fund-IDCW"
    ) == _scheme_core("Bandhan CRISIL IBX 90:10 SDL Plus Gilt September 2027 Index-IDCW")
    assert _scheme_core("ABSL Bal Bhavishya Yojna Regular IDCW") == _scheme_core(
        "Aditya Birla Sun Life Bal Bhavishya Yojna - Regular Plan - IDCW"
    )
    assert _scheme_core("Bandhan Fund IDCW (erstwhile IDFC Fund IDCW)") == _scheme_core(
        "Bandhan Fund IDCW"
    )
    assert _scheme_core("Bandhan Gilt + SDL Index Direct Pl IDCW") == _scheme_core(
        "Bandhan Gilt Plus SDL Index Direct Plan IDCW"
    )
    assert _scheme_core("Bandhan Fixed Term Series 179 Halfyerarly IDCW") == _scheme_core(
        "Bandhan Fixed Term Series 179 Half Yearly IDCW"
    )


def test_rta_scheme_core_preserves_distribution_frequency_identity() -> None:
    assert _scheme_core("Bandhan Corporate Bond Fund Monthly IDCW") != _scheme_core(
        "Bandhan Corporate Bond Fund Quarterly IDCW"
    )


def test_rta_import_maps_by_exact_name_plan_and_nav_then_publishes(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams.jsonl"
    capture_file.write_bytes(_capture())
    service = _service(tmp_path, session)

    first = service.import_file(capture_file)
    second = service.import_file(capture_file)

    assert first.mapped_captures == 1
    assert first.revisions_inserted == 1
    assert first.rows_blocked == 0
    assert second.captures_inserted == 0
    assert second.source_rows_inserted == 0
    assert second.rows_unchanged == 1
    assert session.scalar(select(func.count()).select_from(RtaSchemeCaptureRecord)) == 1
    assert session.scalar(select(func.count()).select_from(RtaDistributionRecord)) == 1
    stored_source = session.scalar(select(RtaDistributionRecord))
    assert stored_source is not None
    assert stored_source.raw_non_individual_amount == "0.0000"
    assert stored_source.non_individual_amount_per_unit_inr == Decimal("0.0000")
    assert session.scalar(select(func.count()).select_from(RtaSchemeMappingReviewRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 1
    assert (
        session.scalar(select(func.count()).select_from(DistributionEventRevisionRtaSourceRecord))
        == 1
    )
    browser = list_scheme_distributions("118969", session, limit=50, offset=0)
    provenance = browser.items[0].revisions[0].sources[0]
    assert provenance.provider == "cams"
    assert provenance.source_kind == "rta_distribution_history"
    assert provenance.source_option_id == "GFDT"
    assert provenance.identity_evidence_details is not None
    assert "exact normalized scheme core" in provenance.identity_evidence_details
    coverage = DistributionCoverageAssessmentService(session).assess()
    assessment = session.get_one(DistributionCoverageAssessmentRecord, (coverage.run_id, "118969"))
    assert assessment.source_row_count == 1
    assert assessment.canonical_source_row_count == 1
    session.close()


def test_rta_mapping_queries_nav_by_name_plan_candidates_first(tmp_path: Path) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams.jsonl"
    capture_file.write_bytes(_capture())
    engine = session.get_bind()
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM nav_revisions JOIN scheme_metadata_versions" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        result = _service(tmp_path, session).import_file(capture_file)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert result.mapped_captures == 1
    assert len(statements) == 1
    assert "nav_revisions.amfi_scheme_code IN" in statements[0]
    assert "nav_revisions.nav_date =" not in statements[0]
    session.close()


def test_rta_import_resume_uses_committed_captures_without_new_ingestion_batch(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams.jsonl"
    payload = _capture()
    capture_file.write_bytes(payload)
    captures = RtaCaptureParser().parse_records(payload)
    service = _service(tmp_path, session)
    batch = service._capture_batch("cams", payload, len(captures))
    stored_captures, _ = service._persist_captures(captures, batch)
    service._persist_rows(captures, stored_captures, batch)
    batches_before = session.scalar(select(func.count()).select_from(IngestionBatchRecord))

    result = service.resume_file(capture_file)

    assert result.captures_received == 1
    assert result.source_rows_received == 1
    assert result.mapped_captures == 1
    assert result.revisions_inserted == 1
    assert session.scalar(select(func.count()).select_from(IngestionBatchRecord)) == batches_before
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 1
    session.close()


def test_unmapped_rta_rows_are_retained_and_blocked(tmp_path: Path) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams-unmapped.jsonl"
    capture_file.write_bytes(_capture(latest_nav=False))

    result = _service(tmp_path, session).import_file(capture_file)

    assert result.unresolved_captures == 1
    assert result.rows_blocked == 1
    assert result.revisions_inserted == 0
    issue = session.scalar(select(RtaDistributionIssueRecord))
    assert issue is not None
    assert issue.issue_code == "unmapped_scheme"
    assert session.scalar(select(func.count()).select_from(RtaDistributionRecord)) == 1
    session.close()


def test_pre_inception_rta_date_is_retained_and_not_published(tmp_path: Path) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams-placeholder-date.jsonl"
    capture_file.write_bytes(_capture(record_date="1900-01-01"))

    result = _service(tmp_path, session).import_file(capture_file)

    assert result.mapped_captures == 1
    assert result.rows_blocked == 1
    assert result.revisions_inserted == 0
    issue = session.scalar(select(RtaDistributionIssueRecord))
    assert issue is not None
    assert issue.issue_code == "implausible_record_date"
    assert session.scalar(select(func.count()).select_from(RtaDistributionRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0
    session.close()


def test_rta_amount_conflict_does_not_replace_canonical_revision(tmp_path: Path) -> None:
    session = _session(tmp_path)
    prior_run = DistributionNormalizationRunRecord(
        id="prior-run",
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
        id="event",
        amfi_scheme_code="118969",
        record_date=date(2026, 7, 27),
        event_type="idcw_cash",
    )
    session.add_all([prior_run, event])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id="revision",
        distribution_event_id=event.id,
        amount_per_unit_inr=Decimal("0.3000"),
        revision_number=1,
        content_signature="r" * 64,
        normalization_version="test",
        normalization_run_id=prior_run.id,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    primary_source = OfficialDistributionRecord(
        id="official-source",
        provider="hdfc",
        amfi_scheme_code="118969",
        source_scheme_name="HDFC Balanced Advantage Fund",
        source_plan_type="direct",
        source_option_label="IDCW payout",
        record_date=date(2026, 7, 27),
        raw_amount_per_unit_inr="0.3000",
        amount_per_unit_inr=Decimal("0.3000"),
        source_unit="inr_per_unit",
        content_signature="o" * 64,
        first_notice_batch_id="nav-batch",
        first_identity_batch_id="nav-batch",
    )
    session.add(primary_source)
    session.flush()
    session.add(
        DistributionEventRevisionOfficialSourceRecord(
            distribution_event_revision_id=revision.id,
            official_distribution_record_id=primary_source.id,
        )
    )
    session.commit()
    capture_file = tmp_path / "cams-conflict.jsonl"
    capture_file.write_bytes(_capture())

    result = _service(tmp_path, session).import_file(capture_file)

    assert result.amount_conflicts == 1
    assert result.rows_blocked == 1
    assert result.revisions_inserted == 0
    current = session.get_one(DistributionEventRevisionRecord, "revision")
    assert current.is_current is True
    assert current.amount_per_unit_inr == Decimal("0.3000")
    issue = session.scalar(select(RtaDistributionIssueRecord))
    assert issue is not None and issue.issue_code == "amount_conflict"
    session.close()


def test_rta_supersedes_conflicting_advisorkhoj_fallback(tmp_path: Path) -> None:
    session = _session(tmp_path)
    advisorkhoj_revision = _seed_advisorkhoj_canonical(session)
    capture_file = tmp_path / "cams.jsonl"
    capture_file.write_bytes(_capture(amount="0.2500"))

    result = _service(tmp_path, session).import_file(capture_file)

    assert result.amount_conflicts == 0
    assert result.revisions_inserted == 1
    assert (
        session.get_one(DistributionEventRevisionRecord, advisorkhoj_revision.id).is_current
        is False
    )
    current = session.scalar(
        select(DistributionEventRevisionRecord).where(
            DistributionEventRevisionRecord.is_current.is_(True)
        )
    )
    assert current is not None
    assert current.revision_number == 2
    assert current.amount_per_unit_inr == Decimal("0.2500")


def test_conflicting_rta_peers_leave_no_arbitrary_current_value(tmp_path: Path) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "cams.jsonl"
    capture_file.write_bytes(_capture(amount="0.3000"))
    service = _service(tmp_path, session)
    first = service.import_file(capture_file)
    capture_file.write_bytes(_capture(amount="0.2500"))

    second = service.import_file(capture_file)

    assert first.revisions_inserted == 1
    assert second.amount_conflicts == 1
    assert second.revisions_inserted == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(DistributionEventRevisionRecord)
            .where(DistributionEventRevisionRecord.is_current.is_(True))
        )
        == 0
    )
