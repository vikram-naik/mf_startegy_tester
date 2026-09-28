import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mf_strategy_tester.config import get_settings
from mf_strategy_tester.db.session import SessionFactory
from mf_strategy_tester.ingestion.amfi import (
    AmfiSourceRequest,
    SchemeType,
    current_nav_request,
    distribution_request,
    fund_list_request,
    historical_nav_request,
    scheme_details_request,
    scheme_list_request,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.benchmarks import OfficialBenchmarkDownloader
from mf_strategy_tester.ingestion.http import SourceDownloader
from mf_strategy_tester.observability.logging import configure_logging
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.advisorkhoj_distribution import (
    AdvisorkhojDistributionPilotService,
)
from mf_strategy_tester.services.benchmark_acquisition import BenchmarkAcquisitionService
from mf_strategy_tester.services.classification_reference import ClassificationReferenceService
from mf_strategy_tester.services.data_quality import DataQualityReportService
from mf_strategy_tester.services.distribution_coverage import (
    DistributionCoverageAssessmentService,
)
from mf_strategy_tester.services.distribution_identity import (
    REVIEW_STATUSES,
    DistributionIdentifierReviewService,
)
from mf_strategy_tester.services.distribution_identity_backlog import (
    DistributionIdentityBacklogService,
)
from mf_strategy_tester.services.distribution_normalization import (
    DistributionNormalizationService,
)
from mf_strategy_tester.services.distribution_payout_gap import DistributionPayoutGapService
from mf_strategy_tester.services.distribution_sync import DistributionSyncService
from mf_strategy_tester.services.nav_sync import EARLIEST_AMFI_NAV_DATE, NavSyncService
from mf_strategy_tester.services.official_distribution_notice import (
    HdfcDistributionNoticeService,
)
from mf_strategy_tester.services.rta_distribution import RtaDistributionImportService
from mf_strategy_tester.services.rta_fingerprint_reconciliation import (
    RtaFingerprintReconciliationService,
)
from mf_strategy_tester.services.scheme_lifecycle import SchemeLifecycleSyncService
from mf_strategy_tester.services.source_ingestion import SourceIngestionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mfst", description="MF Fund Screener operations")
    subcommands = parser.add_subparsers(dest="command", required=True)
    ingest = subcommands.add_parser("ingest", help="Capture and validate an official source")
    sources = ingest.add_subparsers(dest="source", required=True)

    sources.add_parser("fund-list", help="Capture AMFI's current mutual-fund catalog")
    sources.add_parser("current-nav", help="Capture the complete latest AMFI NAV feed")

    historical = sources.add_parser(
        "historical-nav", help="Capture up to 90 calendar days of AMFI NAV history"
    )
    historical.add_argument("--fund-id", default="all")
    historical.add_argument("--from-date", required=True, type=date.fromisoformat)
    historical.add_argument("--to-date", required=True, type=date.fromisoformat)
    historical.add_argument(
        "--scheme-type", choices=[item.value for item in SchemeType], default=SchemeType.ALL.value
    )

    scheme_list = sources.add_parser("scheme-list", help="Capture AMFI's scheme list for a fund")
    scheme_list.add_argument("--fund-id", required=True)

    scheme_details = sources.add_parser(
        "scheme-details", help="Capture official AMFI details for a scheme"
    )
    scheme_details.add_argument("--fund-id", required=True)
    scheme_details.add_argument("--scheme-id", required=True)

    distributions = sources.add_parser(
        "distributions", help="Capture official AMFI dividend/IDCW source records"
    )
    distributions.add_argument("--fund-id", required=True)
    distributions.add_argument("--scheme-id", required=True)
    distributions.add_argument("--year", default="All")

    sync = subcommands.add_parser(
        "sync-nav", help="Resume or incrementally refresh normalized AMFI NAV history"
    )
    sync.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    sync.add_argument("--start-date", type=date.fromisoformat, default=EARLIEST_AMFI_NAV_DATE)
    sync.add_argument("--end-date", type=date.fromisoformat)
    sync.add_argument("--overlap-days", type=int, default=7)
    sync.add_argument(
        "--fund-id",
        action="append",
        dest="fund_ids",
        help="Limit a run to one or more AMFI fund IDs (repeatable; intended for recovery/testing)",
    )
    sync.add_argument("--skip-current-feed", action="store_true", help=argparse.SUPPRESS)

    distribution_sync = subcommands.add_parser(
        "sync-distributions",
        help="Resume initial AMFI distribution capture or refresh all current source schemes",
    )
    distribution_sync.add_argument("--mode", choices=("full", "refresh"), default="full")
    distribution_sync.add_argument(
        "--fund-id",
        action="append",
        dest="fund_ids",
        help="Limit a run to one or more AMFI fund IDs (repeatable; intended for recovery/testing)",
    )
    distribution_sync.add_argument(
        "--scheme-id",
        action="append",
        dest="scheme_ids",
        help="Limit a run to source scheme IDs (repeatable; requires the matching fund selection)",
    )
    distribution_sync.add_argument(
        "--quarantine-record-errors",
        action="store_true",
        help=(
            "continue after distribution row parse failures and persist each rejected row; "
            "transport and structural envelope errors still fail"
        ),
    )
    distribution_sync.add_argument(
        "--retry-quarantined",
        action="store_true",
        help="re-fetch checkpointed distribution families that still have open parse issues",
    )

    lifecycle_sync = subcommands.add_parser(
        "sync-scheme-lifecycle",
        help="Resume AMFI family detail acquisition or refresh all current lifecycle facts",
    )
    lifecycle_sync.add_argument("--mode", choices=("full", "refresh"), default="full")
    lifecycle_sync.add_argument(
        "--delay-seconds",
        type=float,
        default=0.1,
        help="Minimum polite delay after each official AMFI request (default: 0.1)",
    )
    lifecycle_sync.add_argument(
        "--fund-id",
        action="append",
        dest="fund_ids",
        help="Limit the run to AMFI fund IDs (repeatable; intended for recovery/testing)",
    )
    subcommands.add_parser(
        "scheme-lifecycle-report",
        help="Report official AMFI family/detail and explicit lifecycle-event completeness",
    )

    nifty_sync = subcommands.add_parser(
        "sync-nifty-benchmarks",
        help="Acquire official Nifty price, gross-TRI, and NTR history in resumable windows",
    )
    nifty_sync.add_argument("--mode", choices=("full", "refresh"), default="full")
    nifty_sync.add_argument("--start-date", required=True, type=date.fromisoformat)
    nifty_sync.add_argument("--end-date", required=True, type=date.fromisoformat)
    nifty_sync.add_argument(
        "--index",
        action="append",
        dest="indices",
        required=True,
        help="Official Nifty display or trading name (repeatable)",
    )

    etf_sync = subcommands.add_parser(
        "sync-etf-prices",
        help="Acquire official NSE ETF membership and NSE/BSE bhavcopy prices by date",
    )
    etf_sync.add_argument("--mode", choices=("full", "refresh"), default="full")
    etf_sync.add_argument("--start-date", required=True, type=date.fromisoformat)
    etf_sync.add_argument("--end-date", required=True, type=date.fromisoformat)
    etf_sync.add_argument(
        "--exchange",
        action="append",
        dest="exchanges",
        choices=("NSE", "BSE"),
        help="Exchange to acquire (repeatable; default: NSE and BSE)",
    )
    subcommands.add_parser(
        "benchmark-report",
        help="Report benchmark instruments, observations, date ranges, and quality issues",
    )

    subcommands.add_parser(
        "data-quality-report",
        help="Report consolidated NAV issues and unresolved distribution identifiers",
    )
    classification_report = subcommands.add_parser(
        "classification-reference-report",
        help="Report approved AMFI classification aliases and similarity review proposals",
    )
    classification_report.add_argument(
        "--proposal-threshold",
        type=float,
        default=0.82,
        help="Minimum lexical or token similarity for non-mutating proposals (default: 0.82)",
    )

    reconcile = subcommands.add_parser(
        "reconcile-stale-batch",
        help="Mark one verified abandoned ingestion batch as failed",
    )
    reconcile.add_argument("--batch-id", required=True)
    reconcile.add_argument(
        "--stale-before",
        required=True,
        type=_timezone_aware_datetime,
        help="ISO-8601 cutoff with timezone; the batch must have started before it",
    )
    reconcile.add_argument(
        "--reason",
        required=True,
        help="Auditable reason confirming why the running batch is abandoned",
    )

    subcommands.add_parser(
        "survey-distribution-identifiers",
        help="Record source-only reviews for distribution IDs absent from NAV history",
    )

    subcommands.add_parser(
        "normalize-distributions",
        help="Publish only exact-ID, positive, amount-valued IDCW candidates",
    )

    subcommands.add_parser(
        "assess-distribution-coverage",
        help="Snapshot source and canonical-event coverage for every IDCW scheme option",
    )
    subcommands.add_parser(
        "distribution-blocker-report",
        help="Explain blocked rows in the latest completed IDCW coverage snapshot",
    )
    identity_backlog = subcommands.add_parser(
        "distribution-identity-backlog-report",
        help="Rank unique latest unresolved RTA and AdvisorKhoj identities",
    )
    identity_backlog.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Number of highest-row nonempty captures to include (1-1000; default: 100)",
    )
    payout_gap = subcommands.add_parser(
        "distribution-payout-gap-report",
        help="Report, per fund house, live IDCW options lacking declared payouts since a date",
    )
    payout_gap.add_argument(
        "--since",
        type=date.fromisoformat,
        default=date(2025, 1, 1),
        help="Inclusive record-date and live-NAV cutoff (ISO date; default: 2025-01-01)",
    )
    payout_gap.add_argument(
        "--missing-limit",
        type=int,
        default=200,
        help="Number of options without events to list (0-5000; default: 200)",
    )

    hdfc_notice = subcommands.add_parser(
        "publish-hdfc-distribution-notice",
        help="Capture and publish one HDFC IDCW notice with scheme-summary identity evidence",
    )
    hdfc_notice.add_argument("--notice-url", required=True)
    hdfc_notice.add_argument("--scheme-summary-url", required=True)

    rta_import = subcommands.add_parser(
        "import-rta-distributions",
        help="Import a source-containing CAMS or KFintech IDCW JSONL capture",
    )
    rta_import.add_argument("--capture-file", required=True, type=Path)

    rta_resume = subcommands.add_parser(
        "resume-rta-distribution-import",
        help="Resume RTA mapping/publication after captures and source rows were committed",
    )
    rta_resume.add_argument("--capture-file", required=True, type=Path)

    advisorkhoj_import = subcommands.add_parser(
        "import-advisorkhoj-distributions",
        help=(
            "Retain an Advisorkhoj capture and report AMFI NAV-fingerprint mappings "
            "without canonical publication"
        ),
    )
    advisorkhoj_import.add_argument("--capture-file", required=True, type=Path)

    advisorkhoj_acquire = subcommands.add_parser(
        "acquire-advisorkhoj-distributions",
        help="Import one complete AdvisorKhoj catalog and its matching scheme-history capture",
    )
    advisorkhoj_acquire.add_argument("--catalog-file", required=True, type=Path)
    advisorkhoj_acquire.add_argument("--capture-file", required=True, type=Path)

    advisorkhoj_refresh = subcommands.add_parser(
        "refresh-advisorkhoj-mappings",
        help="Re-evaluate an imported AdvisorKhoj capture without duplicating source rows",
    )
    advisorkhoj_refresh.add_argument("--capture-file", required=True, type=Path)

    advisorkhoj_publish = subcommands.add_parser(
        "publish-pending-advisorkhoj-distributions",
        help=(
            "Publish strictly mapped AdvisorKhoj rows only where AMFI and official RTA "
            "evidence do not provide a conflicting value"
        ),
    )
    advisorkhoj_publish.add_argument(
        "--option-limit",
        type=int,
        help="Process at most this many AMFI options; reruns resume from immutable provenance",
    )

    rta_mapping = subcommands.add_parser(
        "review-rta-scheme-mapping",
        help="Append an operator-reviewed RTA scheme to AMFI-code mapping",
    )
    rta_mapping.add_argument("--scheme-capture-id", required=True)
    rta_mapping.add_argument("--amfi-scheme-code", required=True)
    rta_mapping.add_argument("--evidence-details", required=True)

    subcommands.add_parser(
        "publish-pending-rta-distributions",
        help="Publish captured RTA rows after new manual scheme mappings",
    )
    fingerprint_reconcile = subcommands.add_parser(
        "reconcile-rta-nav-fingerprint",
        help=(
            "Retire values backed only by withdrawn NAV-fingerprint identities and restore "
            "values they displaced"
        ),
    )
    fingerprint_reconcile.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the changes without committing them",
    )

    review_identifier = subcommands.add_parser(
        "review-distribution-identifier",
        help="Append an evidence-backed distribution identifier review",
    )
    review_identifier.add_argument("--source-option-id", required=True)
    review_identifier.add_argument("--status", choices=sorted(REVIEW_STATUSES), required=True)
    review_identifier.add_argument("--evidence-batch-id", required=True)
    review_identifier.add_argument("--matched-amfi-scheme-code")
    review_identifier.add_argument("--evidence-details", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        settings = get_settings()
        settings.ensure_local_directories()
        configure_logging(settings.log_level)
        with SessionFactory() as session:
            repository = IngestionRepository(session)
            artifacts = ArtifactStore(settings.raw_data_path)
            downloader = SourceDownloader(
                timeout_seconds=settings.download_timeout_seconds,
                max_bytes=settings.max_download_bytes,
                retry_attempts=settings.download_retry_attempts,
                retry_backoff_seconds=settings.download_retry_backoff_seconds,
            )
            service = SourceIngestionService(
                repository,
                artifacts,
                downloader,
            )
            benchmark_downloader = OfficialBenchmarkDownloader(
                timeout_seconds=settings.download_timeout_seconds,
                max_bytes=settings.max_download_bytes,
                retry_attempts=settings.download_retry_attempts,
                retry_backoff_seconds=settings.download_retry_backoff_seconds,
            )
            benchmark_service = BenchmarkAcquisitionService(
                session, repository, artifacts, benchmark_downloader
            )
            if arguments.command == "sync-nifty-benchmarks":
                output = asdict(
                    benchmark_service.sync_nifty_indices(
                        index_names=tuple(arguments.indices),
                        start_date=arguments.start_date,
                        end_date=arguments.end_date,
                        mode=arguments.mode,
                    )
                )
            elif arguments.command == "sync-etf-prices":
                output = asdict(
                    benchmark_service.sync_exchange_etfs(
                        start_date=arguments.start_date,
                        end_date=arguments.end_date,
                        exchanges=(
                            tuple(arguments.exchanges) if arguments.exchanges else ("NSE", "BSE")
                        ),
                        mode=arguments.mode,
                    )
                )
            elif arguments.command == "benchmark-report":
                output = asdict(benchmark_service.coverage_report())
            elif arguments.command == "sync-scheme-lifecycle":
                output = asdict(
                    SchemeLifecycleSyncService(
                        session,
                        repository,
                        artifacts,
                        downloader,
                        request_delay_seconds=arguments.delay_seconds,
                    ).sync(
                        mode=arguments.mode,
                        fund_ids=tuple(arguments.fund_ids) if arguments.fund_ids else None,
                    )
                )
            elif arguments.command == "scheme-lifecycle-report":
                output = asdict(
                    SchemeLifecycleSyncService(
                        session, repository, artifacts, downloader
                    ).coverage_report()
                )
            elif arguments.command == "data-quality-report":
                output = asdict(DataQualityReportService(session).build_report())
            elif arguments.command == "classification-reference-report":
                output = asdict(
                    ClassificationReferenceService(session).build_report(
                        proposal_threshold=arguments.proposal_threshold
                    )
                )
            elif arguments.command == "acquire-advisorkhoj-distributions":
                output = asdict(
                    AdvisorkhojDistributionPilotService(
                        session, repository, artifacts
                    ).acquire_catalog(arguments.catalog_file, arguments.capture_file)
                )
            elif arguments.command == "import-advisorkhoj-distributions":
                output = asdict(
                    AdvisorkhojDistributionPilotService(session, repository, artifacts).import_file(
                        arguments.capture_file
                    )
                )
            elif arguments.command == "refresh-advisorkhoj-mappings":
                output = asdict(
                    AdvisorkhojDistributionPilotService(
                        session, repository, artifacts
                    ).refresh_mappings(arguments.capture_file)
                )
            elif arguments.command == "publish-pending-advisorkhoj-distributions":
                output = asdict(
                    AdvisorkhojDistributionPilotService(
                        session, repository, artifacts
                    ).publish_pending(option_limit=arguments.option_limit)
                )
            elif arguments.command == "import-rta-distributions":
                output = asdict(
                    RtaDistributionImportService(session, repository, artifacts).import_file(
                        arguments.capture_file
                    )
                )
            elif arguments.command == "resume-rta-distribution-import":
                output = asdict(
                    RtaDistributionImportService(session, repository, artifacts).resume_file(
                        arguments.capture_file
                    )
                )
            elif arguments.command == "review-rta-scheme-mapping":
                rta_review = RtaDistributionImportService(
                    session, repository, artifacts
                ).record_manual_mapping(
                    scheme_capture_id=arguments.scheme_capture_id,
                    amfi_scheme_code=arguments.amfi_scheme_code,
                    evidence_details=arguments.evidence_details,
                )
                output = {
                    "review_id": rta_review.id,
                    "scheme_capture_id": rta_review.scheme_capture_id,
                    "status": rta_review.status,
                    "amfi_scheme_code": rta_review.amfi_scheme_code,
                    "mapping_method": rta_review.mapping_method,
                }
            elif arguments.command == "publish-pending-rta-distributions":
                output = asdict(
                    RtaDistributionImportService(session, repository, artifacts).publish_pending()
                )
            elif arguments.command == "reconcile-rta-nav-fingerprint":
                output = asdict(
                    RtaFingerprintReconciliationService(session).reconcile(
                        dry_run=arguments.dry_run
                    )
                )
            elif arguments.command == "publish-hdfc-distribution-notice":
                output = asdict(
                    HdfcDistributionNoticeService(session, service, repository, artifacts).publish(
                        notice_url=arguments.notice_url,
                        scheme_summary_url=arguments.scheme_summary_url,
                    )
                )
            elif arguments.command == "assess-distribution-coverage":
                output = asdict(DistributionCoverageAssessmentService(session).assess())
            elif arguments.command == "distribution-blocker-report":
                output = asdict(
                    DistributionCoverageAssessmentService(session).blocked_source_report()
                )
            elif arguments.command == "distribution-identity-backlog-report":
                output = asdict(
                    DistributionIdentityBacklogService(session).build_report(
                        ranked_capture_limit=arguments.limit
                    )
                )
            elif arguments.command == "distribution-payout-gap-report":
                output = asdict(
                    DistributionPayoutGapService(session).build_report(
                        since=arguments.since,
                        missing_option_limit=arguments.missing_limit,
                    )
                )
            elif arguments.command == "normalize-distributions":
                output = asdict(DistributionNormalizationService(session).normalize())
            elif arguments.command == "survey-distribution-identifiers":
                output = asdict(
                    DistributionIdentifierReviewService(
                        session
                    ).survey_unmatched_source_identifiers()
                )
            elif arguments.command == "review-distribution-identifier":
                identifier_review, inserted = DistributionIdentifierReviewService(
                    session
                ).record_review(
                    source_option_id=arguments.source_option_id,
                    status=arguments.status,
                    evidence_batch_id=arguments.evidence_batch_id,
                    evidence_details=arguments.evidence_details,
                    matched_amfi_scheme_code=arguments.matched_amfi_scheme_code,
                )
                output = {
                    "review_id": identifier_review.id,
                    "source_option_id": identifier_review.source_option_id,
                    "status": identifier_review.status,
                    "matched_amfi_scheme_code": identifier_review.matched_amfi_scheme_code,
                    "evidence_batch_id": identifier_review.evidence_batch_id,
                    "inserted": inserted,
                }
            elif arguments.command == "reconcile-stale-batch":
                batch = repository.reconcile_stale_batch(
                    arguments.batch_id,
                    stale_before=arguments.stale_before,
                    reason=arguments.reason,
                )
                output = {
                    "batch_id": batch.id,
                    "status": batch.status,
                    "started_at": batch.started_at.isoformat(),
                    "completed_at": (
                        batch.completed_at.isoformat() if batch.completed_at is not None else None
                    ),
                    "error_details": batch.error_details,
                }
            elif arguments.command == "sync-nav":
                end_date = arguments.end_date or datetime.now(ZoneInfo("Asia/Kolkata")).date()
                output = asdict(
                    NavSyncService(session, service, repository, artifacts).synchronize(
                        mode=arguments.mode,
                        requested_start_date=arguments.start_date,
                        requested_end_date=end_date,
                        overlap_days=arguments.overlap_days,
                        fund_ids=frozenset(arguments.fund_ids) if arguments.fund_ids else None,
                        include_current_feed=not arguments.skip_current_feed,
                    )
                )
            elif arguments.command == "sync-distributions":
                output = asdict(
                    DistributionSyncService(session, service, repository, artifacts).synchronize(
                        mode=arguments.mode,
                        fund_ids=frozenset(arguments.fund_ids) if arguments.fund_ids else None,
                        scheme_ids=(
                            frozenset(arguments.scheme_ids) if arguments.scheme_ids else None
                        ),
                        quarantine_record_errors=arguments.quarantine_record_errors,
                        retry_quarantined=arguments.retry_quarantined,
                    )
                )
            else:
                output = asdict(service.ingest(_source_request(arguments)))
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print("operation interrupted; committed data was preserved", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"operation failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _source_request(arguments: argparse.Namespace) -> AmfiSourceRequest:
    if arguments.source == "fund-list":
        return fund_list_request()
    if arguments.source == "current-nav":
        return current_nav_request()
    if arguments.source == "historical-nav":
        return historical_nav_request(
            mutual_fund_id=arguments.fund_id,
            from_date=arguments.from_date,
            to_date=arguments.to_date,
            scheme_type=SchemeType(arguments.scheme_type),
        )
    if arguments.source == "scheme-list":
        return scheme_list_request(arguments.fund_id)
    if arguments.source == "scheme-details":
        return scheme_details_request(arguments.fund_id, arguments.scheme_id)
    if arguments.source == "distributions":
        return distribution_request(arguments.fund_id, arguments.scheme_id, arguments.year)
    raise ValueError(f"unsupported ingestion source {arguments.source}")


def _timezone_aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a timezone offset")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
