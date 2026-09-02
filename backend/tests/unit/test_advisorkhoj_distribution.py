import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from mf_strategy_tester.api.routes.data import list_scheme_distributions
from mf_strategy_tester.db.base import Base
from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSnapshotRecord,
    AdvisorkhojDistributionIssueRecord,
    AdvisorkhojDistributionRecord,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
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
from mf_strategy_tester.ingestion.advisorkhoj import (
    AdvisorkhojCaptureParser,
    parse_catalog,
    parse_distribution_details_json,
    parse_historical_distributions_html,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.errors import SourceParseError
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.advisorkhoj_distribution import (
    AdvisorkhojDistributionPilotService,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "advisorkhoj"
SCHEME_NAME = "HDFC Balanced Advtg Dir IDCW"
DISPLAY_NAME = "HDFC Balanced Advantage Fund - IDCW Plan - Direct Plan"
BASE_URL = "https://www.advisorkhoj.com/mutual-funds-research/mutual-funds-historical-dividends/"


def _html() -> str:
    return (FIXTURES / "hdfc_balanced_advantage_direct.html").read_text(encoding="utf-8")


def _capture_payload(html: str | None = None, *, scheme_name: str = SCHEME_NAME) -> bytes:
    source_payload = _html() if html is None else html
    source_url = BASE_URL + quote(scheme_name, safe="")
    document = {
        "schema_version": 1,
        "provider": "advisorkhoj",
        "requested_url": source_url,
        "source_url": source_url,
        "captured_at": "2026-08-20T10:00:00+05:30",
        "amc_name": "HDFC Mutual Fund",
        "requested_scheme_name": scheme_name,
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
    }
    return (json.dumps(document, separators=(",", ":")) + "\n").encode()


def _catalog_payload(*, scheme_name: str = SCHEME_NAME) -> bytes:
    landing = (
        '<select id="sel_amcCompanies">'
        '<option value="HDFC Mutual Fund">HDFC Mutual Fund</option>'
        "</select>"
    )
    categories = json.dumps(["Hybrid: Dynamic Asset Allocation"], separators=(",", ":"))
    schemes = json.dumps([scheme_name], separators=(",", ":"))
    document = {
        "schema_version": 2,
        "provider": "advisorkhoj",
        "captured_at": "2026-08-20T10:00:00+05:30",
        "source_url": "https://www.advisorkhoj.com/mutual-funds-research/amc-wise-dividends",
        "source_payload_sha256": hashlib.sha256(landing.encode()).hexdigest(),
        "source_payload": landing,
        "queries": [
            {
                "amc_name": "HDFC Mutual Fund",
                "categories_payload_sha256": hashlib.sha256(categories.encode()).hexdigest(),
                "categories_payload": categories,
                "scheme_queries": [
                    {
                        "category": "Hybrid: Dynamic Asset Allocation",
                        "source_payload_sha256": hashlib.sha256(schemes.encode()).hexdigest(),
                        "source_payload": schemes,
                    }
                ],
            }
        ],
        "schemes": [
            {
                "amc_name": "HDFC Mutual Fund",
                "category": "Hybrid: Dynamic Asset Allocation",
                "scheme_name": scheme_name,
            }
        ],
    }
    return json.dumps(document, separators=(",", ":")).encode()


def _api_capture_payload(catalog_payload: bytes, *, scheme_name: str = SCHEME_NAME) -> bytes:
    source_payload = json.dumps(
        [
            {
                "dividend_date": "27-07-2026",
                "net_asset_value": 43.273,
                "dividend_value": 0.25,
                "dividend_yield": 0.5777274512975759,
            },
            {
                "dividend_date": "25-06-2026",
                "net_asset_value": 43.446,
                "dividend_value": 0.25,
                "dividend_yield": 0.5754269668093726,
            },
            {
                "dividend_date": "27-04-2026",
                "net_asset_value": 43.679,
                "dividend_value": 0.25,
                "dividend_yield": 0.5723574257652418,
            },
        ],
        separators=(",", ":"),
    )
    document = {
        "schema_version": 2,
        "provider": "advisorkhoj",
        "catalog_sha256": hashlib.sha256(catalog_payload).hexdigest(),
        "source_url": (
            "https://www.advisorkhoj.com/mutual-funds-research/getSchemeDividendDetails"
        ),
        "captured_at": "2026-08-20T10:05:00+05:30",
        "amc_name": "HDFC Mutual Fund",
        "category": "Hybrid: Dynamic Asset Allocation",
        "requested_scheme_name": scheme_name,
        "source_payload_sha256": hashlib.sha256(source_payload.encode()).hexdigest(),
        "source_payload": source_payload,
    }
    return (json.dumps(document, separators=(",", ":")) + "\n").encode()


def _session(tmp_path: Path) -> Session:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'advisorkhoj.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    session.add(
        IngestionBatchRecord(
            id="nav-batch",
            provider="amfi",
            source_type="historical_nav",
            source_url="https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx",
            request_parameters={},
            parser_version="test",
            status="completed",
        )
    )
    session.commit()
    return session


def _seed_option(
    session: Session,
    *,
    scheme_code: str,
    plan_type: str,
    nav_values: dict[date, str],
    scheme_name: str = DISPLAY_NAME,
) -> None:
    option = SchemeOptionRecord(
        amfi_scheme_code=scheme_code,
        first_observed_nav_date=min(nav_values),
        last_observed_nav_date=max(nav_values),
        first_observed_batch_id="nav-batch",
    )
    metadata_id = hashlib.sha256(f"metadata:{scheme_code}".encode()).hexdigest()
    metadata = SchemeMetadataVersionRecord(
        id=metadata_id,
        amfi_scheme_code=scheme_code,
        scheme_name=scheme_name,
        fund_house_name="HDFC Mutual Fund",
        scheme_classification="Hybrid: Dynamic Asset Allocation",
        plan_type=plan_type,
        option_type="idcw",
        classification_method="test",
        first_observed_batch_id="nav-batch",
    )
    session.add(option)
    session.flush()
    session.add(metadata)
    session.flush()
    for index, (nav_date, nav_value) in enumerate(sorted(nav_values.items()), start=1):
        session.add(
            NavRevisionRecord(
                id=f"nav-{scheme_code}-{index}",
                amfi_scheme_code=scheme_code,
                nav_date=nav_date,
                nav_value=Decimal(nav_value),
                metadata_version_id=metadata_id,
                revision_number=1,
                content_signature=hashlib.sha256(
                    f"nav:{scheme_code}:{nav_date}:{nav_value}".encode()
                ).hexdigest(),
                quality_status="valid",
                is_current=True,
                first_observed_batch_id="nav-batch",
            )
        )
    session.commit()


def _matching_navs() -> dict[date, str]:
    return {
        date(2026, 4, 27): "43.6790",
        date(2026, 6, 25): "43.446",
        date(2026, 7, 27): "43.2730",
    }


def _service(tmp_path: Path, session: Session) -> AdvisorkhojDistributionPilotService:
    return AdvisorkhojDistributionPilotService(
        session, IngestionRepository(session), ArtifactStore(tmp_path / "raw")
    )


def test_advisorkhoj_html_parser_preserves_decimal_source_values() -> None:
    page = parse_historical_distributions_html(_html())

    assert page.source_scheme_name == SCHEME_NAME
    assert page.display_scheme_name == DISPLAY_NAME
    assert page.plan_type == "direct"
    assert page.option_variant == "unknown"
    assert page.source_frequency == "unknown"
    assert page.display_frequency == "unknown"
    assert page.frequency_conflict is False
    assert len(page.rows) == 3
    assert page.rows[0].record_date == date(2026, 7, 27)
    assert page.rows[0].raw_amount_per_unit_inr == "0.2500"
    assert page.rows[0].amount_per_unit_inr == Decimal("0.2500")
    assert page.rows[0].reference_nav == Decimal("43.273")
    assert page.rows[0].yield_percent == Decimal("0.58")
    assert page.rows[0].is_positive_cash_distribution is True


def test_advisorkhoj_html_parser_retains_zero_rows_as_non_cash_observations() -> None:
    html = _html().replace("0.2500", "0.0000", 1).replace("0.58%", "0.00%", 1)

    page = parse_historical_distributions_html(html)

    assert page.rows[0].amount_per_unit_inr == Decimal("0.0000")
    assert page.rows[0].is_positive_cash_distribution is False


def test_advisorkhoj_page_surfaces_frequency_label_conflict() -> None:
    html = (
        _html()
        .replace(SCHEME_NAME, "HDFC Credit Risk Debt Dir Hly IDCW")
        .replace(DISPLAY_NAME, "HDFC Credit Risk Debt Fund - Quarterly IDCW - Direct Plan")
    )

    page = parse_historical_distributions_html(html)

    assert page.source_frequency == "half_yearly"
    assert page.display_frequency == "quarterly"
    assert page.frequency_conflict is True


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("Dividend Record Date", "Payment Date", "unexpected Advisorkhoj distribution header"),
        ("0.58%", "0.57%", "dividend yield mismatch"),
        ("25-06-2026", "27-07-2026", "duplicate record dates"),
    ],
)
def test_advisorkhoj_html_parser_rejects_structural_or_financial_corruption(
    old: str, new: str, message: str
) -> None:
    with pytest.raises(SourceParseError, match=message):
        parse_historical_distributions_html(_html().replace(old, new, 1))


def test_advisorkhoj_capture_parser_verifies_source_checksum() -> None:
    value = json.loads(_capture_payload())
    value["source_payload_sha256"] = "0" * 64

    with pytest.raises(SourceParseError, match="checksum mismatch"):
        AdvisorkhojCaptureParser().parse_records((json.dumps(value) + "\n").encode())


def test_advisorkhoj_capture_parser_rejects_rendered_scheme_mismatch() -> None:
    value = json.loads(_capture_payload())
    value["requested_scheme_name"] = "HDFC Different Scheme IDCW"
    value["requested_url"] = BASE_URL + quote(value["requested_scheme_name"], safe="")

    with pytest.raises(SourceParseError, match="rendered/requested scheme mismatch"):
        AdvisorkhojCaptureParser().parse_records((json.dumps(value) + "\n").encode())


def test_catalog_and_api_detail_parsers_validate_complete_source_shapes() -> None:
    catalog_payload = _catalog_payload()
    catalog = parse_catalog(catalog_payload)
    api_document = json.loads(_api_capture_payload(catalog_payload))
    rows = parse_distribution_details_json(api_document["source_payload"])

    assert catalog.amcs == ("HDFC Mutual Fund",)
    assert catalog.category_queries == 1
    assert catalog.schemes[0].scheme_name == SCHEME_NAME
    assert len(rows) == 3
    assert rows[0].amount_per_unit_inr == Decimal("0.25")
    assert rows[0].reference_nav == Decimal("43.273")


def test_api_detail_parser_retains_observed_empty_and_negative_source_values() -> None:
    assert parse_distribution_details_json("null") == ()
    rows = parse_distribution_details_json(
        '[{"dividend_date":"16-01-2026","net_asset_value":10.899,'
        '"dividend_value":-0.0046,"dividend_yield":-0.04220570694559134}]'
    )

    assert rows[0].amount_per_unit_inr == Decimal("-0.0046")
    assert rows[0].is_positive_cash_distribution is False
    zero_nav_rows = parse_distribution_details_json(
        '[{"dividend_date":"30-09-2026","net_asset_value":0,'
        '"dividend_value":0.2016083,"dividend_yield":0}]'
    )
    assert zero_nav_rows[0].reference_nav == 0


def test_complete_catalog_acquisition_persists_secondary_rows_and_mapping(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _seed_option(
        session,
        scheme_code="118969",
        plan_type="direct",
        nav_values=_matching_navs(),
    )
    catalog_file = tmp_path / "catalog.json"
    catalog_payload = _catalog_payload()
    catalog_file.write_bytes(catalog_payload)
    capture_file = tmp_path / "capture.jsonl"
    capture_file.write_bytes(_api_capture_payload(catalog_payload))

    result = _service(tmp_path, session).acquire_catalog(catalog_file, capture_file)

    assert result.catalog_amcs == 1
    assert result.catalog_schemes == 1
    assert result.captures_imported == 1
    assert result.source_rows_received == 3
    assert result.source_rows_inserted == 3
    assert result.negative_source_rows == 0
    assert result.zero_reference_nav_rows == 0
    assert result.implausible_historical_date_rows == 0
    assert result.future_dated_source_rows == 0
    assert result.mapped_captures == 1
    assert result.unresolved_captures == 0
    assert result.canonical_events_published == 0
    assert session.scalar(select(func.count()).select_from(AdvisorkhojCatalogSnapshotRecord)) == 1
    assert session.scalar(select(func.count()).select_from(AdvisorkhojSchemeCaptureRecord)) == 1
    assert session.scalar(select(func.count()).select_from(AdvisorkhojDistributionRecord)) == 3
    review = session.scalar(select(AdvisorkhojSchemeMappingReviewRecord))
    assert review is not None
    assert review.status == "mapped"
    assert review.amfi_scheme_code == "118969"
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0


def test_mapped_advisorkhoj_rows_publish_as_resumable_tertiary_fallback(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _seed_option(
        session,
        scheme_code="118969",
        plan_type="direct",
        nav_values=_matching_navs(),
    )
    catalog_payload = _catalog_payload()
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_bytes(catalog_payload)
    capture_file = tmp_path / "capture.jsonl"
    capture_file.write_bytes(_api_capture_payload(catalog_payload))
    service = _service(tmp_path, session)
    service.acquire_catalog(catalog_file, capture_file)

    first = service.publish_pending()
    second = service.publish_pending()

    assert first.options_processed == 1
    assert first.events_inserted == 3
    assert first.revisions_inserted == 3
    assert first.source_rows_linked == 3
    assert first.source_rows_blocked == 0
    assert second.options_processed == 0
    assert second.source_rows_examined == 0
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 3
    assert (
        session.scalar(
            select(func.count()).select_from(DistributionEventRevisionAdvisorkhojSourceRecord)
        )
        == 3
    )
    browser = list_scheme_distributions("118969", session, limit=50, offset=0)
    provenance = browser.items[0].revisions[0].sources[0]
    assert provenance.provider == "advisorkhoj"
    assert provenance.source_kind == "third_party_distribution_history"
    assert provenance.identity_evidence_details is not None
    assert "exact NAV" in provenance.identity_evidence_details


def test_pre_inception_advisorkhoj_date_is_retained_and_not_published(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _seed_option(
        session,
        scheme_code="118969",
        plan_type="direct",
        nav_values=_matching_navs(),
    )
    catalog_payload = _catalog_payload()
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_bytes(catalog_payload)
    capture_file = tmp_path / "capture.jsonl"
    capture_file.write_bytes(_api_capture_payload(catalog_payload))
    service = _service(tmp_path, session)
    service.acquire_catalog(catalog_file, capture_file)
    placeholder = session.scalar(
        select(AdvisorkhojDistributionRecord).where(
            AdvisorkhojDistributionRecord.record_date == date(2026, 7, 27)
        )
    )
    assert placeholder is not None
    placeholder.record_date = date(1900, 1, 2)
    session.commit()

    result = service.publish_pending()

    assert result.events_inserted == 2
    assert result.source_rows_linked == 2
    assert result.source_rows_blocked == 1
    issue = session.scalar(
        select(AdvisorkhojDistributionIssueRecord).where(
            AdvisorkhojDistributionIssueRecord.issue_code == "implausible_record_date"
        )
    )
    assert issue is not None
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 2


def test_advisorkhoj_conflict_cannot_replace_primary_source(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed_option(
        session,
        scheme_code="118969",
        plan_type="direct",
        nav_values=_matching_navs(),
    )
    catalog_payload = _catalog_payload()
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_bytes(catalog_payload)
    capture_file = tmp_path / "capture.jsonl"
    capture_file.write_bytes(_api_capture_payload(catalog_payload))
    service = _service(tmp_path, session)
    service.acquire_catalog(catalog_file, capture_file)
    prior_run = DistributionNormalizationRunRecord(
        id="primary-run",
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
        id="primary-event",
        amfi_scheme_code="118969",
        record_date=date(2026, 7, 27),
        event_type="idcw_cash",
    )
    primary_source = OfficialDistributionRecord(
        id="primary-source",
        provider="hdfc",
        amfi_scheme_code="118969",
        source_scheme_name=DISPLAY_NAME,
        source_plan_type="direct",
        source_option_label="IDCW payout",
        record_date=date(2026, 7, 27),
        raw_amount_per_unit_inr="0.30",
        amount_per_unit_inr=Decimal("0.30"),
        source_unit="inr_per_unit",
        content_signature="o" * 64,
        first_notice_batch_id="nav-batch",
        first_identity_batch_id="nav-batch",
    )
    session.add_all([prior_run, event, primary_source])
    session.flush()
    revision = DistributionEventRevisionRecord(
        id="primary-revision",
        distribution_event_id=event.id,
        amount_per_unit_inr=Decimal("0.30"),
        revision_number=1,
        content_signature="p" * 64,
        normalization_version="test",
        normalization_run_id=prior_run.id,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    session.add(
        DistributionEventRevisionOfficialSourceRecord(
            distribution_event_revision_id=revision.id,
            official_distribution_record_id=primary_source.id,
        )
    )
    session.commit()

    result = service.publish_pending()

    assert result.events_inserted == 2
    assert result.source_rows_linked == 2
    assert result.source_rows_blocked == 1
    assert result.higher_priority_conflicts == 1
    assert session.get_one(DistributionEventRevisionRecord, revision.id).is_current is True
    issue = session.scalar(select(AdvisorkhojDistributionIssueRecord))
    assert issue is not None and issue.issue_code == "higher_priority_conflict"


def test_complete_mapping_uses_explicit_payout_and_frequency_qualifiers(
    tmp_path: Path,
) -> None:
    source_name = "HDFC Balanced Advtg Dir Mly IDCW Pay"
    session = _session(tmp_path)
    for scheme_code, suffix in (
        ("118969", "Monthly Payout of IDCW"),
        ("999999", "Monthly Reinvestment of IDCW"),
    ):
        _seed_option(
            session,
            scheme_code=scheme_code,
            plan_type="direct",
            nav_values=_matching_navs(),
            scheme_name=f"HDFC Balanced Advantage Fund Direct Plan - {suffix}",
        )
    catalog_payload = _catalog_payload(scheme_name=source_name)
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_bytes(catalog_payload)
    capture_file = tmp_path / "capture.jsonl"
    capture_file.write_bytes(_api_capture_payload(catalog_payload, scheme_name=source_name))
    service = _service(tmp_path, session)

    result = service.acquire_catalog(catalog_file, capture_file)
    before_rows = session.scalar(select(func.count()).select_from(AdvisorkhojDistributionRecord))
    refreshed = service.refresh_mappings(capture_file)
    after_rows = session.scalar(select(func.count()).select_from(AdvisorkhojDistributionRecord))

    assert result.mapped_captures == 1
    assert result.ambiguous_captures == 0
    assert refreshed.mapped_captures == 1
    assert before_rows == after_rows == 3
    latest_review = session.scalar(
        select(AdvisorkhojSchemeMappingReviewRecord).order_by(
            AdvisorkhojSchemeMappingReviewRecord.reviewed_at.desc()
        )
    )
    assert latest_review is not None
    assert latest_review.amfi_scheme_code == "118969"


def test_pilot_maps_three_conflict_free_nav_points_and_never_publishes(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _seed_option(
        session,
        scheme_code="118969",
        plan_type="direct",
        nav_values=_matching_navs(),
    )
    capture_file = tmp_path / "hdfc.jsonl"
    capture_file.write_bytes(_capture_payload())
    service = _service(tmp_path, session)

    first = service.import_file(capture_file)
    second = service.import_file(capture_file)

    assert first.mapped_captures == 1
    assert first.assessments[0].status == "mapped"
    assert first.assessments[0].amfi_scheme_code == "118969"
    candidate = first.assessments[0].candidates[0]
    assert candidate.exact_nav_matches == 3
    assert candidate.nav_conflicts == 0
    assert candidate.evidence_span_days == 91
    assert candidate.qualifying is True
    assert first.canonical_events_published == 0
    assert second.artifact_reused is True
    assert session.scalar(select(func.count()).select_from(SourceArtifactRecord)) == 1
    assert session.scalar(select(func.count()).select_from(IngestionBatchRecord)) == 3
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0


def test_pilot_quarantines_insufficient_nav_evidence(tmp_path: Path) -> None:
    session = _session(tmp_path)
    navs = _matching_navs()
    navs.pop(date(2026, 4, 27))
    _seed_option(session, scheme_code="118969", plan_type="direct", nav_values=navs)
    capture_file = tmp_path / "hdfc.jsonl"
    capture_file.write_bytes(_capture_payload())

    result = _service(tmp_path, session).import_file(capture_file)

    assert result.unresolved_captures == 1
    assert result.assessments[0].amfi_scheme_code is None
    assert result.assessments[0].candidates[0].exact_nav_matches == 2
    assert result.assessments[0].candidates[0].qualifying is False


def test_pilot_marks_multiple_nav_fingerprint_matches_ambiguous(tmp_path: Path) -> None:
    session = _session(tmp_path)
    for scheme_code in ("118969", "999999"):
        _seed_option(
            session,
            scheme_code=scheme_code,
            plan_type="direct",
            nav_values=_matching_navs(),
        )
    capture_file = tmp_path / "hdfc.jsonl"
    capture_file.write_bytes(_capture_payload())

    result = _service(tmp_path, session).import_file(capture_file)

    assert result.ambiguous_captures == 1
    assert result.assessments[0].status == "ambiguous"
    assert result.assessments[0].amfi_scheme_code is None
    assert {item.amfi_scheme_code for item in result.assessments[0].candidates} == {
        "118969",
        "999999",
    }


def test_pilot_quarantines_conflicting_frequency_labels(tmp_path: Path) -> None:
    source_name = "HDFC Credit Risk Debt Dir Hly IDCW"
    display_name = "HDFC Credit Risk Debt Fund - Quarterly IDCW - Direct Plan"
    html = _html().replace(SCHEME_NAME, source_name).replace(DISPLAY_NAME, display_name)
    session = _session(tmp_path)
    _seed_option(
        session,
        scheme_code="128050",
        plan_type="direct",
        nav_values=_matching_navs(),
        scheme_name=display_name,
    )
    capture_file = tmp_path / "hdfc.jsonl"
    capture_file.write_bytes(_capture_payload(html, scheme_name=source_name))

    result = _service(tmp_path, session).import_file(capture_file)

    assessment = result.assessments[0]
    assert assessment.status == "unresolved"
    assert assessment.amfi_scheme_code is None
    assert assessment.frequency_conflict is True
    assert assessment.candidates[0].qualifying is True
    assert "disagree on distribution frequency" in assessment.conclusion


def test_pilot_retains_raw_artifact_and_failed_batch_when_parsing_fails(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    capture_file = tmp_path / "malformed.jsonl"
    capture_file.write_bytes(
        _capture_payload(_html().replace("Dividend Record Date", "Payment Date", 1))
    )

    with pytest.raises(SourceParseError, match="unexpected Advisorkhoj distribution header"):
        _service(tmp_path, session).import_file(capture_file)

    batch = session.scalar(
        select(IngestionBatchRecord).where(IngestionBatchRecord.provider == "advisorkhoj")
    )
    assert batch is not None
    assert batch.status == "failed"
    assert batch.artifact_id is not None
    assert "SourceParseError" in (batch.error_details or "")
    assert session.scalar(select(func.count()).select_from(SourceArtifactRecord)) == 1
    assert session.scalar(select(func.count()).select_from(DistributionEventRecord)) == 0
