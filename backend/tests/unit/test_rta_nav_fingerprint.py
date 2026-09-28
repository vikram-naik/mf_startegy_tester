import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
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
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.distribution_identity_backlog import (
    DistributionIdentityBacklogService,
)
from mf_strategy_tester.services.rta_distribution import RtaDistributionImportService
from mf_strategy_tester.services.rta_fingerprint_reconciliation import (
    RtaFingerprintReconciliationService,
)

# Hand-built KFintech-style history: cum NAV equals AMFI's record-date NAV, ex NAV is lower.
_RECORD_DATES = (date(2025, 1, 15), date(2025, 2, 17), date(2025, 3, 17))
_AMFI_NAVS = (Decimal("12.3456"), Decimal("12.4001"), Decimal("12.1111"))
_AMOUNTS = ("0.1000", "0.1000", "0.1200")


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'fingerprint.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    session.add(
        IngestionBatchRecord(
            id="nav-batch",
            provider="amfi",
            source_type="historical_nav",
            source_url="https://www.amfiindia.com/nav-history-download",
            request_parameters={},
            parser_version="test",
            status="completed",
        )
    )
    session.commit()
    return session


def _add_option(
    session: Session,
    code: str,
    navs: dict[date, Decimal],
    *,
    plan_type: str = "direct",
    option_type: str = "idcw",
    scheme_name: str = "Alpha Income Fund - Direct Plan - Monthly IDCW",
) -> None:
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code=code,
            first_observed_nav_date=min(navs),
            last_observed_nav_date=max(navs),
            first_observed_batch_id="nav-batch",
        )
    )
    session.flush()
    metadata_id = hashlib.sha256(code.encode()).hexdigest()
    session.add(
        SchemeMetadataVersionRecord(
            id=metadata_id,
            amfi_scheme_code=code,
            scheme_name=scheme_name,
            fund_house_name="Alpha Mutual Fund",
            scheme_classification="Debt Scheme - Short Duration Fund",
            plan_type=plan_type,
            option_type=option_type,
            classification_method="test",
            first_observed_batch_id="nav-batch",
        )
    )
    session.flush()
    for nav_date, nav_value in navs.items():
        session.add(
            NavRevisionRecord(
                amfi_scheme_code=code,
                nav_date=nav_date,
                nav_value=nav_value,
                metadata_version_id=metadata_id,
                revision_number=1,
                content_signature=hashlib.sha256(f"{code}{nav_date}".encode()).hexdigest(),
                quality_status="valid",
                is_current=True,
                first_observed_batch_id="nav-batch",
            )
        )
    session.commit()


def _target_navs() -> dict[date, Decimal]:
    return dict(zip(_RECORD_DATES, _AMFI_NAVS, strict=True))


def _kfintech_capture(
    *,
    record_dates: tuple[date, ...] = _RECORD_DATES,
    cum_navs: tuple[str, ...] = ("12.34560", "12.4001", "12.1111"),
    amounts: tuple[str, ...] = _AMOUNTS,
    plan_type: str = "direct",
    scheme_code: str = "AMDD",
) -> bytes:
    source_payload = "<html><table>official KFintech dividend history</table></html>"
    records = [
        {
            "record_date": record_date.isoformat(),
            "individual_amount": amount,
            "non_individual_amount": amount,
            "ex_nav": str(Decimal(cum_nav) - Decimal(amount)),
            "cum_nav": cum_nav,
            "source_terminology": "KFintech dividend history",
        }
        for record_date, cum_nav, amount in zip(record_dates, cum_navs, amounts, strict=True)
    ]
    value = {
        "schema_version": 1,
        "provider": "kfintech",
        "source_url": "https://mfs.kfintech.com/mfs/NAVDividend/Dividend.aspx?Fund=AL",
        "captured_at": "2026-09-01T10:00:00+05:30",
        "fund": {"code": "AL", "name": "Alpha Mutual Fund"},
        "scheme": {
            "code": scheme_code,
            "name": "ALPHA INC FD DIR MTH DIV",
            "plan_type": plan_type,
            "option_variant": "payout",
            "latest_nav_date": None,
            "latest_nav_value": None,
        },
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
        "records": records,
    }
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def _import(
    tmp_path: Path, session: Session, payload: bytes, filename: str = "kfintech.jsonl"
) -> RtaDistributionImportService:
    capture_file = tmp_path / filename
    capture_file.write_bytes(payload)
    service = RtaDistributionImportService(
        session, IngestionRepository(session), ArtifactStore(tmp_path / "raw")
    )
    service.import_file(capture_file)
    return service


def _latest_review(session: Session) -> RtaSchemeMappingReviewRecord:
    review = session.scalar(
        select(RtaSchemeMappingReviewRecord).order_by(
            RtaSchemeMappingReviewRecord.reviewed_at.desc(),
            RtaSchemeMappingReviewRecord.id.desc(),
        )
    )
    assert review is not None
    return review


def _fingerprint(review: RtaSchemeMappingReviewRecord) -> dict[str, object]:
    evidence = json.loads(review.evidence_details)
    fingerprint = evidence["nav_fingerprint"]
    assert isinstance(fingerprint, dict)
    return fingerprint


def test_unique_nav_fingerprint_maps_without_name_match_and_publishes_declared_amounts(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs())
    _add_option(
        session,
        "100002",
        {
            _RECORD_DATES[0]: Decimal("15.0000"),
            _RECORD_DATES[1]: Decimal("15.1000"),
            _RECORD_DATES[2]: Decimal("15.2000"),
        },
    )

    _import(tmp_path, session, _kfintech_capture())

    review = _latest_review(session)
    assert review.status == "mapped"
    assert review.amfi_scheme_code == "100001"
    assert review.mapping_method == "nav_fingerprint"
    fingerprint = _fingerprint(review)
    assert fingerprint["reason"] == "unique_nav_fingerprint"
    assert fingerprint["qualifying_codes"] == ["100001"]
    amounts = session.scalars(
        select(DistributionEventRevisionRecord.amount_per_unit_inr)
        .join(DistributionEventRecord)
        .where(DistributionEventRecord.amfi_scheme_code == "100001")
        .order_by(DistributionEventRecord.record_date)
    ).all()
    assert amounts == [Decimal("0.1000"), Decimal("0.1000"), Decimal("0.1200")]
    session.close()


def test_fingerprint_rerun_is_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs())
    service = _import(tmp_path, session, _kfintech_capture())

    service.resume_file(tmp_path / "kfintech.jsonl")

    assert session.scalar(select(func.count()).select_from(RtaSchemeMappingReviewRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DistributionEventRevisionRecord)) == 3
    session.close()


def test_comparable_date_nav_mismatch_blocks_fingerprint(tmp_path: Path) -> None:
    session = _session(tmp_path)
    navs = _target_navs()
    navs[_RECORD_DATES[2]] = Decimal("12.9999")
    _add_option(session, "100001", navs)

    _import(tmp_path, session, _kfintech_capture())

    review = _latest_review(session)
    assert review.status == "unresolved"
    assert review.amfi_scheme_code is None
    assert _fingerprint(review)["reason"] == "nav_fingerprint_conflict"
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0
    issue_codes = set(session.scalars(select(RtaDistributionIssueRecord.issue_code)))
    assert issue_codes == {"unmapped_scheme"}
    session.close()


def test_identical_nav_options_remain_ambiguous_and_are_reported(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs())
    _add_option(session, "100003", _target_navs(), scheme_name="Alpha Income Fund Twin IDCW")

    _import(tmp_path, session, _kfintech_capture())

    review = _latest_review(session)
    assert review.status == "ambiguous"
    assert _fingerprint(review)["reason"] == "multiple_nav_fingerprint_candidates"
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0
    report = DistributionIdentityBacklogService(session).build_report()
    assert [(item.status, item.reason) for item in report.reason_summaries] == [
        ("ambiguous", "multiple_nav_fingerprint_candidates")
    ]
    assert report.ranked_captures[0].qualifying_candidate_codes == ("100001", "100003")
    session.close()


def test_constant_nav_evidence_is_insufficient_and_skips_nav_queries(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", dict.fromkeys(_RECORD_DATES, Decimal("10.0000")))
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM nav_revisions" in statement:
            statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        payload = _kfintech_capture(cum_navs=("10.0000", "10.0000", "10.0000"))
        source = json.loads(payload)
        for record in source["records"]:
            record["ex_nav"] = "10.0000"
        _import(tmp_path, session, (json.dumps(source) + "\n").encode())
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    review = _latest_review(session)
    assert review.status == "unresolved"
    assert _fingerprint(review)["reason"] == "insufficient_nav_fingerprint_evidence"
    assert statements == []
    session.close()


def test_explicit_plan_conflict_excludes_candidate(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs(), plan_type="direct")

    _import(tmp_path, session, _kfintech_capture(plan_type="regular"))

    review = _latest_review(session)
    assert review.status == "unresolved"
    assert _fingerprint(review)["reason"] == "no_nav_fingerprint_candidate"
    session.close()


def test_short_evidence_span_is_insufficient(tmp_path: Path) -> None:
    session = _session(tmp_path)
    dates = (date(2025, 1, 15), date(2025, 1, 22), date(2025, 1, 29))
    _add_option(session, "100001", dict(zip(dates, _AMFI_NAVS, strict=True)))

    _import(tmp_path, session, _kfintech_capture(record_dates=dates))

    review = _latest_review(session)
    assert review.status == "unresolved"
    assert _fingerprint(review)["reason"] == "insufficient_nav_fingerprint_matches"
    candidates = _fingerprint(review)["candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["evidence_span_days"] == 14
    session.close()


def test_growth_option_with_matching_navs_is_never_a_candidate(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100004", _target_navs(), option_type="growth")

    _import(tmp_path, session, _kfintech_capture())

    review = _latest_review(session)
    assert review.status == "unresolved"
    assert _fingerprint(review)["reason"] == "no_nav_fingerprint_candidate"
    session.close()


def _seed_revision(session: Session, code: str, record_date: date, amount: str) -> str:
    run = DistributionNormalizationRunRecord(
        id=f"seed-run-{code}-{record_date}",
        status="completed",
        normalization_version="test",
        source_rows_examined=1,
        candidate_rows=1,
        blocked_rows=0,
        events_inserted=1,
        revisions_inserted=1,
        rows_unchanged=0,
    )
    event_record = DistributionEventRecord(
        id=f"seed-event-{code}-{record_date}",
        amfi_scheme_code=code,
        record_date=record_date,
        event_type="idcw_cash",
    )
    session.add_all([run, event_record])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id=f"seed-revision-{code}-{record_date}",
        distribution_event_id=event_record.id,
        amount_per_unit_inr=Decimal(amount),
        revision_number=1,
        content_signature=hashlib.sha256(f"seed{code}{record_date}".encode()).hexdigest(),
        normalization_version="test",
        normalization_run_id=run.id,
        is_current=True,
    )
    session.add(revision)
    session.commit()
    return revision.id


def _cams_capture(record_date: date, amount: str) -> bytes:
    source_payload = "<html><table>official CAMS IDCW history</table></html>"
    value = {
        "schema_version": 1,
        "provider": "cams",
        "source_url": "https://www.camsonline.com/InvestorServices/COL_ISNAV.aspx",
        "captured_at": "2026-09-01T10:00:00+05:30",
        "fund": {"code": "AL", "name": "Alpha Mutual Fund"},
        "scheme": {
            "code": "ALMD",
            "name": "Alpha Income Fund - Direct Plan - Monthly IDCW Payout",
            "plan_type": "direct",
            "option_variant": "payout",
            "latest_nav_date": _RECORD_DATES[2].isoformat(),
            "latest_nav_value": str(_AMFI_NAVS[2]),
        },
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
        "records": [
            {
                "record_date": record_date.isoformat(),
                "individual_amount": amount,
                "non_individual_amount": None,
                "ex_nav": None,
                "cum_nav": None,
                "source_terminology": "CAMS IDCW history",
            }
        ],
    }
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def _review_for(session: Session, provider: str) -> RtaSchemeMappingReviewRecord:
    review = session.scalar(
        select(RtaSchemeMappingReviewRecord)
        .join(
            RtaSchemeCaptureRecord,
            RtaSchemeCaptureRecord.id == RtaSchemeMappingReviewRecord.scheme_capture_id,
        )
        .where(RtaSchemeCaptureRecord.provider == provider)
        .order_by(
            RtaSchemeMappingReviewRecord.reviewed_at.desc(),
            RtaSchemeMappingReviewRecord.id.desc(),
        )
    )
    assert review is not None
    return review


def _current_amounts(session: Session, code: str) -> dict[date, Decimal]:
    return dict(
        session.execute(
            select(
                DistributionEventRecord.record_date,
                DistributionEventRevisionRecord.amount_per_unit_inr,
            )
            .join(DistributionEventRevisionRecord)
            .where(
                DistributionEventRecord.amfi_scheme_code == code,
                DistributionEventRevisionRecord.is_current.is_(True),
            )
        )
        .tuples()
        .all()
    )


def test_near_constant_nav_series_is_low_information(tmp_path: Path) -> None:
    session = _session(tmp_path)
    dates = tuple(date(2025, month, 15) for month in range(1, 7))
    navs = ("1000.1000",) * 5 + ("1000.2000",)
    _add_option(session, "100001", dict(zip(dates, map(Decimal, navs), strict=True)))

    _import(
        tmp_path,
        session,
        _kfintech_capture(record_dates=dates, cum_navs=navs, amounts=("0.0500",) * 6),
    )

    review = _latest_review(session)
    assert review.status == "unresolved"
    fingerprint = _fingerprint(review)
    assert fingerprint["reason"] == "nav_fingerprint_low_information"
    candidates = fingerprint["candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["informative"] is False
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0
    session.close()


def test_declared_amount_disagreement_withdraws_fingerprint_identity(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs())
    _seed_revision(session, "100001", _RECORD_DATES[1], "0.2500")

    _import(tmp_path, session, _kfintech_capture())

    review = _latest_review(session)
    assert review.status == "unresolved"
    assert json.loads(review.evidence_details)["candidate_codes"] == []
    fingerprint = _fingerprint(review)
    assert fingerprint["reason"] == "nav_fingerprint_amount_disagreement"
    assert fingerprint["qualifying_codes"] == ["100001"]
    corroboration = fingerprint["amount_corroboration"]
    assert isinstance(corroboration, dict)
    assert (corroboration["compared_dates"], corroboration["disagreeing_dates"]) == (1, 1)
    assert _current_amounts(session, "100001") == {_RECORD_DATES[1]: Decimal("0.2500")}
    report = DistributionIdentityBacklogService(session).build_report()
    assert [item.reason for item in report.reason_summaries] == [
        "nav_fingerprint_amount_disagreement"
    ]
    session.close()


def test_agreeing_declared_amount_corroborates_fingerprint_identity(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs())
    _seed_revision(session, "100001", _RECORD_DATES[1], "0.1000")

    _import(tmp_path, session, _kfintech_capture())

    review = _latest_review(session)
    assert review.status == "mapped"
    corroboration = _fingerprint(review)["amount_corroboration"]
    assert isinstance(corroboration, dict)
    assert (corroboration["compared_dates"], corroboration["disagreeing_dates"]) == (1, 0)
    session.close()


def _cams_then_fingerprint_conflict(tmp_path: Path, session: Session) -> str:
    """CAMS publishes a pre-2020 value; a fingerprint capture disagrees on that date only."""
    conflict_date = date(2019, 6, 17)
    _add_option(session, "100001", _target_navs())
    _import(tmp_path, session, _cams_capture(conflict_date, "0.5000"), "cams.jsonl")
    assert _review_for(session, "cams").mapping_method == "exact_name_plan_nav"
    cams_revision = session.scalar(
        select(DistributionEventRevisionRecord.id).where(
            DistributionEventRevisionRecord.is_current.is_(True)
        )
    )
    assert cams_revision is not None
    _import(
        tmp_path,
        session,
        _kfintech_capture(
            record_dates=(conflict_date, *_RECORD_DATES),
            cum_navs=("11.0000", "12.34560", "12.4001", "12.1111"),
            amounts=("0.4000", *_AMOUNTS),
        ),
    )
    return cams_revision


def test_fingerprint_conflict_blocks_row_without_retiring_rta_value(tmp_path: Path) -> None:
    session = _session(tmp_path)
    cams_revision = _cams_then_fingerprint_conflict(tmp_path, session)

    assert _review_for(session, "kfintech").mapping_method == "nav_fingerprint"
    assert session.get_one(DistributionEventRevisionRecord, cams_revision).is_current is True
    issue = session.scalar(
        select(RtaDistributionIssueRecord).where(
            RtaDistributionIssueRecord.issue_code == "amount_conflict"
        )
    )
    assert issue is not None
    assert "tier=rta" in issue.details
    assert "existing_value_retired=false" in issue.details
    assert _current_amounts(session, "100001")[date(2019, 6, 17)] == Decimal("0.5000")
    session.close()


def test_reconciliation_retires_values_of_withdrawn_fingerprint_identity(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_option(session, "100001", _target_navs())
    _import(tmp_path, session, _kfintech_capture())
    fingerprint_review = _latest_review(session)
    session.add(
        RtaSchemeMappingReviewRecord(
            scheme_capture_id=fingerprint_review.scheme_capture_id,
            status="unresolved",
            amfi_scheme_code=None,
            mapping_method="none",
            evidence_details='{"candidate_codes":[]}',
            review_signature="w" * 64,
            reviewed_at=utc_now(),
        )
    )
    session.commit()
    service = RtaFingerprintReconciliationService(session)

    preview = service.reconcile(dry_run=True)
    assert len(preview.revisions_retired) == 3
    assert len(_current_amounts(session, "100001")) == 3
    applied = service.reconcile(dry_run=False)
    rerun = service.reconcile(dry_run=False)

    assert applied.revisions_retired == preview.revisions_retired
    assert applied.revisions_restored == ()
    assert applied.events_left_without_current == 3
    assert _current_amounts(session, "100001") == {}
    assert (rerun.revisions_retired, rerun.revisions_restored) == ((), ())
    session.close()


def test_reconciliation_restores_rta_value_retired_by_fingerprint_row(tmp_path: Path) -> None:
    session = _session(tmp_path)
    cams_revision = _cams_then_fingerprint_conflict(tmp_path, session)
    session.get_one(DistributionEventRevisionRecord, cams_revision).is_current = False
    session.commit()

    result = RtaFingerprintReconciliationService(session).reconcile(dry_run=False)

    assert result.revisions_restored == (cams_revision,)
    assert result.revisions_retired == ()
    assert session.get_one(DistributionEventRevisionRecord, cams_revision).is_current is True
    session.close()


def test_reconciliation_respects_genuine_same_priority_conflict(tmp_path: Path) -> None:
    session = _session(tmp_path)
    cams_revision = _cams_then_fingerprint_conflict(tmp_path, session)
    session.get_one(DistributionEventRevisionRecord, cams_revision).is_current = False
    cams_row = session.scalar(
        select(RtaDistributionRecord)
        .join(RtaSchemeCaptureRecord)
        .where(RtaSchemeCaptureRecord.provider == "cams")
    )
    run_id = session.scalar(select(DistributionNormalizationRunRecord.id))
    assert cams_row is not None and run_id is not None
    session.add(
        RtaDistributionIssueRecord(
            rta_distribution_record_id=cams_row.id,
            normalization_run_id=run_id,
            issue_code="amount_conflict",
            details="peer disagreement: tier=rta, AMFI code=100001",
        )
    )
    session.commit()

    result = RtaFingerprintReconciliationService(session).reconcile(dry_run=False)

    assert result.revisions_restored == ()
    assert result.events_withheld_for_same_priority_conflict == 1
    assert session.get_one(DistributionEventRevisionRecord, cams_revision).is_current is False
    session.close()
