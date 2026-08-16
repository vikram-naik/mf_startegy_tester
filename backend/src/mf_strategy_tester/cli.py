import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
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
from mf_strategy_tester.ingestion.http import SourceDownloader
from mf_strategy_tester.observability.logging import configure_logging
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.nav_sync import EARLIEST_AMFI_NAV_DATE, NavSyncService
from mf_strategy_tester.services.source_ingestion import SourceIngestionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mfst", description="MF Strategy Tester operations")
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
            service = SourceIngestionService(
                repository,
                artifacts,
                SourceDownloader(
                    timeout_seconds=settings.download_timeout_seconds,
                    max_bytes=settings.max_download_bytes,
                    retry_attempts=settings.download_retry_attempts,
                    retry_backoff_seconds=settings.download_retry_backoff_seconds,
                ),
            )
            if arguments.command == "sync-nav":
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
            else:
                output = asdict(service.ingest(_source_request(arguments)))
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print("ingestion interrupted; committed checkpoints were preserved", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"ingestion failed: {type(error).__name__}: {error}", file=sys.stderr)
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


if __name__ == "__main__":
    raise SystemExit(main())
