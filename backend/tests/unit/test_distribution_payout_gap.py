import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    RtaDistributionRecord,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    utc_now,
)
from mf_strategy_tester.db.session import create_database_engine
from mf_strategy_tester.services.distribution_payout_gap import DistributionPayoutGapService

_SINCE = date(2025, 1, 1)


def _digest(*values: object) -> str:
    return hashlib.sha256("|".join(str(value) for value in values).encode()).hexdigest()


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'gap.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)()
    session.add_all(
        [
            IngestionBatchRecord(
                id="batch",
                provider="amfi",
                source_type="historical_nav",
                source_url="https://www.amfiindia.com/nav-history-download",
                request_parameters={},
                parser_version="test",
                status="completed",
            ),
            DistributionNormalizationRunRecord(
                id="run",
                status="completed",
                normalization_version="test",
                source_rows_examined=0,
                candidate_rows=0,
                blocked_rows=0,
                events_inserted=0,
                revisions_inserted=0,
                rows_unchanged=0,
            ),
        ]
    )
    session.commit()
    return session


def _option(
    session: Session,
    code: str,
    last_nav_date: date,
    *,
    fund_house: str,
    option_type: str = "idcw",
    payout_isin: str | None = "INF000000001",
) -> None:
    session.add(
        SchemeOptionRecord(
            amfi_scheme_code=code,
            first_observed_nav_date=date(2020, 1, 1),
            last_observed_nav_date=last_nav_date,
            first_observed_batch_id="batch",
        )
    )
    session.flush()
    metadata_id = _digest("metadata", code)
    session.add(
        SchemeMetadataVersionRecord(
            id=metadata_id,
            amfi_scheme_code=code,
            scheme_name=f"{fund_house} Scheme {code} - IDCW",
            fund_house_name=fund_house,
            scheme_classification="Debt Scheme - Short Duration Fund",
            isin_payout_or_growth=payout_isin,
            isin_reinvestment="INF000000002",
            plan_type="direct",
            option_type=option_type,
            classification_method="test",
            first_observed_batch_id="batch",
        )
    )
    session.flush()
    session.add(
        NavRevisionRecord(
            amfi_scheme_code=code,
            nav_date=last_nav_date,
            nav_value=Decimal("10.5000"),
            metadata_version_id=metadata_id,
            revision_number=1,
            content_signature=_digest("nav", code),
            quality_status="valid",
            is_current=True,
            first_observed_batch_id="batch",
        )
    )
    session.commit()


def _rta_event(session: Session, code: str, record_date: date, *, provider: str) -> None:
    capture_id = _digest("capture", code, provider)
    if session.get(RtaSchemeCaptureRecord, capture_id) is None:
        session.add(
            RtaSchemeCaptureRecord(
                id=capture_id,
                provider=provider,
                rta_fund_code="F",
                rta_fund_name="Fund",
                rta_scheme_code=code,
                source_scheme_name="Source scheme",
                source_url="https://mfs.kfintech.com/mfs/NAVDividend/Dividend.aspx?Fund=F",
                source_payload_sha256=_digest("payload", capture_id),
                plan_type="direct",
                option_variant="payout",
                source_row_count=1,
                capture_signature=_digest("signature", capture_id),
                ingestion_batch_id="batch",
                captured_at=utc_now(),
            )
        )
        session.flush()
        session.add(
            RtaSchemeMappingReviewRecord(
                id=_digest("review", capture_id)[:36],
                scheme_capture_id=capture_id,
                status="mapped",
                amfi_scheme_code=code,
                mapping_method="nav_fingerprint",
                evidence_details="{}",
                review_signature=_digest("review", capture_id),
            )
        )
        session.flush()
    source_id = _digest("source", capture_id, record_date)[:36]
    event_id = _digest("event", code, record_date)[:36]
    revision_id = _digest("revision", code, record_date)[:36]
    session.add_all(
        [
            RtaDistributionRecord(
                id=source_id,
                scheme_capture_id=capture_id,
                record_date=record_date,
                raw_individual_amount="0.1000",
                individual_amount_per_unit_inr=Decimal("0.1000"),
                raw_non_individual_amount=None,
                non_individual_amount_per_unit_inr=None,
                ex_nav=None,
                cum_nav=None,
                source_unit="inr_per_unit",
                source_terminology="test",
                content_signature=_digest("row", source_id),
                first_observed_batch_id="batch",
            ),
            DistributionEventRecord(
                id=event_id,
                amfi_scheme_code=code,
                record_date=record_date,
                event_type="idcw_cash",
            ),
        ]
    )
    session.flush()
    session.add(
        DistributionEventRevisionRecord(
            id=revision_id,
            distribution_event_id=event_id,
            amount_per_unit_inr=Decimal("0.1000"),
            revision_number=1,
            content_signature=_digest("revision-signature", revision_id),
            normalization_version="test",
            normalization_run_id="run",
            is_current=True,
        )
    )
    session.flush()
    session.add(
        DistributionEventRevisionRtaSourceRecord(
            distribution_event_revision_id=revision_id,
            rta_distribution_record_id=source_id,
            mapping_review_id=_digest("review", capture_id)[:36],
        )
    )
    session.commit()


def test_gap_report_counts_live_idcw_options_and_declared_events_since_cutoff(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _option(session, "100001", date(2026, 8, 31), fund_house="Alpha Mutual Fund")
    _option(session, "100002", date(2026, 8, 31), fund_house="Alpha Mutual Fund")
    _option(session, "100003", date(2026, 8, 31), fund_house="Alpha Mutual Fund", payout_isin=None)
    _option(session, "100004", date(2024, 6, 28), fund_house="Alpha Mutual Fund")
    _option(
        session, "100005", date(2026, 8, 31), fund_house="Alpha Mutual Fund", option_type="growth"
    )
    _option(session, "200001", date(2026, 8, 31), fund_house="Beta Mutual Fund")
    _rta_event(session, "100001", date(2025, 3, 17), provider="kfintech")
    _rta_event(session, "100001", date(2025, 4, 15), provider="kfintech")
    _rta_event(session, "100002", date(2024, 12, 16), provider="kfintech")
    _rta_event(session, "100004", date(2025, 2, 17), provider="kfintech")
    _rta_event(session, "200001", date(2025, 5, 15), provider="cams")

    report = DistributionPayoutGapService(session).build_report(since=_SINCE)

    assert report.since == "2025-01-01"
    assert report.live_idcw_options == 4
    assert report.payout_eligible_options == 3
    assert report.options_with_events == 2
    assert report.options_without_events == 2
    assert report.payout_eligible_options_without_events == 1
    assert report.events == 3
    assert report.events_by_source == {
        "amfi": 0,
        "official_notice": 0,
        "cams": 1,
        "kfintech": 2,
        "advisorkhoj": 0,
    }
    alpha, beta = report.fund_houses
    assert alpha.fund_house_name == "Alpha Mutual Fund"
    assert (alpha.live_idcw_options, alpha.options_with_events, alpha.options_without_events) == (
        3,
        1,
        2,
    )
    assert alpha.options_by_source["kfintech"] == 1
    assert beta.options_without_events == 0
    assert [item.amfi_scheme_code for item in report.missing_options] == ["100002", "100003"]
    assert report.missing_options[0].payout_eligible is True
    assert report.missing_options[1].payout_eligible is False
    assert report.missing_options_total == 2
    session.close()


def test_gap_report_bounds_missing_option_listing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _option(session, "100001", date(2026, 8, 31), fund_house="Alpha Mutual Fund")
    _option(session, "100002", date(2026, 8, 31), fund_house="Alpha Mutual Fund")
    service = DistributionPayoutGapService(session)

    report = service.build_report(since=_SINCE, missing_option_limit=1)

    assert report.missing_options_total == 2
    assert len(report.missing_options) == 1
    with pytest.raises(ValueError, match="missing option limit"):
        service.build_report(since=_SINCE, missing_option_limit=5001)
    session.close()
