from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TypeVar
from urllib.error import HTTPError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    BenchmarkExchangeListingRecord,
    BenchmarkInstrumentRecord,
    BenchmarkIssueRecord,
    BenchmarkObservationRecord,
    BenchmarkObservationSourceRecord,
    BenchmarkSyncCheckpointRecord,
    BenchmarkSyncRunRecord,
    IngestionBatchRecord,
    utc_now,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.benchmarks import (
    BENCHMARK_PARSER_VERSION,
    UDIFF_START_DATE,
    BenchmarkDownloader,
    BenchmarkRequest,
    BseEtfMarketParser,
    BseEtfMarketRow,
    EtfMasterRow,
    ExchangePriceRow,
    LegacyBseBhavcopyParser,
    LegacyNseBhavcopyParser,
    NiftyIndexMapping,
    NiftyIndexMappingParser,
    NiftyPriceParser,
    NiftyPriceRow,
    NiftyTotalReturnParser,
    NiftyTotalReturnRow,
    NseEtfDailyParser,
    NseEtfDailyRow,
    NseEtfMasterParser,
    UdIffBhavcopyParser,
    bhavcopy_request,
    bse_etf_market_request,
    nifty_history_request,
    nifty_mapping_request,
    nse_etf_daily_request,
    nse_etf_master_request,
)
from mf_strategy_tester.ingestion.errors import SourceNotPublishedError, SourceParseError
from mf_strategy_tester.repositories.ingestion import IngestionRepository

T = TypeVar("T")


@dataclass(frozen=True)
class BenchmarkSyncResult:
    run_id: str
    source_family: str
    mode: str
    status: str
    requests_total: int
    requests_completed: int
    requests_skipped: int
    requests_failed: int
    rows_received: int
    rows_inserted: int
    rows_unchanged: int
    issue_counts: dict[str, int]


@dataclass(frozen=True)
class BenchmarkCoverageReport:
    parser_version: str
    instrument_counts: dict[str, int]
    observation_counts: dict[str, int]
    exchange_observation_counts: dict[str, int]
    observation_date_ranges: dict[str, dict[str, str | None]]
    provisional_bse_observations: int
    issue_counts: dict[str, int]
    latest_sync_runs: dict[str, dict[str, object]]


class _CaptureFailure(RuntimeError):
    def __init__(self, batch: IngestionBatchRecord, error: BaseException) -> None:
        super().__init__(f"{type(error).__name__}: {error!s}")
        self.batch = batch
        self.error = error


class BenchmarkAcquisitionService:
    """Acquire immutable official index and ETF prices with resumable checkpoints."""

    def __init__(
        self,
        session: Session,
        repository: IngestionRepository,
        artifacts: ArtifactStore,
        downloader: BenchmarkDownloader,
    ) -> None:
        self._session = session
        self._repository = repository
        self._artifacts = artifacts
        self._downloader = downloader

    def sync_nifty_indices(
        self,
        *,
        index_names: Sequence[str],
        start_date: date,
        end_date: date,
        mode: str = "full",
    ) -> BenchmarkSyncResult:
        self._validate_run(mode, start_date, end_date)
        requested_names = tuple(dict.fromkeys(name.strip() for name in index_names if name.strip()))
        if not requested_names:
            raise ValueError("at least one Nifty index name is required")
        run = self._start_run("nifty_indices", mode, start_date, end_date)
        issues: Counter[str] = Counter()
        warning_count = 0
        try:
            mappings, mapping_batch = self._capture(
                nifty_mapping_request(), NiftyIndexMappingParser().parse
            )
            by_display = {mapping.display_name.casefold(): mapping for mapping in mappings}
            by_trading = {mapping.trading_name.casefold(): mapping for mapping in mappings}
            selected: list[NiftyIndexMapping] = []
            for requested in requested_names:
                mapping = by_display.get(requested.casefold()) or by_trading.get(
                    requested.casefold()
                )
                if mapping is None:
                    available = sorted(item.display_name for item in mappings)
                    raise ValueError(
                        f"Nifty index {requested!r} is absent from the official mapping; "
                        f"sample available values={available[:10]}"
                    )
                selected.append(mapping)
            windows = _inclusive_windows(start_date, end_date, 365)
            run.requests_total = len(selected) * len(windows) * 2
            self._session.commit()
            for mapping in selected:
                instruments = self._ensure_nifty_instruments(mapping, mapping_batch)
                for period_start, period_end in windows:
                    for total_return in (False, True):
                        source_id = (
                            f"{mapping.trading_name}|{'total_return' if total_return else 'price'}"
                        )
                        checkpoint = self._checkpoint("nifty_indices", source_id, period_start)
                        if mode == "full" and checkpoint is not None:
                            run.requests_skipped += 1
                            self._session.commit()
                            continue
                        request = nifty_history_request(
                            mapping, period_start, period_end, total_return=total_return
                        )
                        try:
                            if total_return:
                                total_rows, batch = self._capture(
                                    request, NiftyTotalReturnParser().parse
                                )
                                rows: list[NiftyPriceRow] | list[NiftyTotalReturnRow] = total_rows
                            else:
                                price_rows, batch = self._capture(request, NiftyPriceParser().parse)
                                rows = price_rows
                        except _CaptureFailure as failure:
                            if isinstance(failure.error, SourceParseError):
                                raise
                            warning_count += 1
                            self._record_issue(
                                run,
                                "nifty_indices",
                                source_id,
                                period_start,
                                failure.batch.id,
                                "download_failure",
                                "error",
                                str(failure),
                                issues,
                            )
                            run.requests_failed += 1
                            self._session.commit()
                            continue
                        run.rows_received += len(rows)
                        if not rows:
                            self._record_issue(
                                run,
                                "nifty_indices",
                                source_id,
                                period_start,
                                batch.id,
                                "empty_source_period",
                                "info",
                                "official response contained no rows for "
                                f"{period_start}..{period_end}",
                                issues,
                            )
                        inserted, unchanged, revisions = self._persist_nifty_rows(
                            instruments, rows, batch, run, source_id, issues
                        )
                        run.rows_inserted += inserted
                        run.rows_unchanged += unchanged
                        warning_count += revisions + sum(
                            isinstance(row, NiftyPriceRow) and row.ohlc_issue is not None
                            for row in rows
                        )
                        run.requests_completed += 1
                        self._upsert_checkpoint(
                            run,
                            "nifty_indices",
                            source_id,
                            period_start,
                            period_end,
                            batch,
                            len(rows),
                        )
                        self._session.commit()
            self._complete_run(run, warning_count)
        except (Exception, KeyboardInterrupt) as error:
            self._fail_run(run, error)
            raise
        return self._result(run, issues)

    def sync_exchange_etfs(
        self,
        *,
        start_date: date,
        end_date: date,
        exchanges: Sequence[str] = ("NSE", "BSE"),
        mode: str = "full",
    ) -> BenchmarkSyncResult:
        self._validate_run(mode, start_date, end_date)
        normalized_exchanges = tuple(dict.fromkeys(item.upper() for item in exchanges))
        if not normalized_exchanges or any(
            item not in {"NSE", "BSE"} for item in normalized_exchanges
        ):
            raise ValueError("exchanges must contain NSE and/or BSE")
        run = self._start_run("exchange_etf", mode, start_date, end_date)
        issues: Counter[str] = Counter()
        warning_count = 0
        try:
            master_rows, master_batch = self._capture(
                nse_etf_master_request(), NseEtfMasterParser().parse
            )
            for master_row in master_rows:
                self._ensure_etf_instrument(master_row, master_batch)
            bse_market_rows: list[BseEtfMarketRow] = []
            bse_market_batch: IngestionBatchRecord | None = None
            if "BSE" in normalized_exchanges:
                bse_market_rows, bse_market_batch = self._capture(
                    bse_etf_market_request(), BseEtfMarketParser().parse
                )
                if bse_market_rows[0].roster_variant == "categorized_subset":
                    warning_count += 1
                    self._record_issue(
                        run,
                        "bse",
                        "BSE|etf_roster",
                        bse_market_rows[0].as_of_date,
                        bse_market_batch.id,
                        "partial_bse_roster",
                        "warning",
                        "BSE returned its categorized ETF subset schema "
                        f"({len(bse_market_rows)} rows); BSE-only current ETF coverage "
                        "may be incomplete",
                        issues,
                    )
            acquisition_dates = list(_dates_descending(start_date, end_date))
            run.requests_total = len(acquisition_dates) * len(normalized_exchanges)
            self._session.commit()
            for observation_date in acquisition_dates:
                if "NSE" in normalized_exchanges:
                    nse_warning = self._sync_nse_etf_date(run, observation_date, mode, issues)
                    warning_count += nse_warning
                if "BSE" in normalized_exchanges:
                    if bse_market_batch is None:
                        raise AssertionError("BSE market-watch batch was not captured")
                    bse_warning = self._sync_bse_etf_date(
                        run,
                        observation_date,
                        mode,
                        issues,
                        bse_market_rows,
                        bse_market_batch,
                    )
                    warning_count += bse_warning
            self._complete_run(run, warning_count)
        except (Exception, KeyboardInterrupt) as error:
            self._fail_run(run, error)
            raise
        return self._result(run, issues)

    def coverage_report(self) -> BenchmarkCoverageReport:
        instrument_counts = dict(
            self._session.execute(
                select(BenchmarkInstrumentRecord.instrument_type, func.count())
                .group_by(BenchmarkInstrumentRecord.instrument_type)
                .order_by(BenchmarkInstrumentRecord.instrument_type)
            )
            .tuples()
            .all()
        )
        observation_counts = dict(
            self._session.execute(
                select(BenchmarkInstrumentRecord.instrument_type, func.count())
                .join(
                    BenchmarkObservationRecord,
                    BenchmarkObservationRecord.benchmark_instrument_id
                    == BenchmarkInstrumentRecord.id,
                )
                .group_by(BenchmarkInstrumentRecord.instrument_type)
                .order_by(BenchmarkInstrumentRecord.instrument_type)
            )
            .tuples()
            .all()
        )
        exchange_counts = dict(
            self._session.execute(
                select(BenchmarkObservationRecord.exchange, func.count())
                .where(BenchmarkObservationRecord.exchange.is_not(None))
                .group_by(BenchmarkObservationRecord.exchange)
                .order_by(BenchmarkObservationRecord.exchange)
            )
            .tuples()
            .all()
        )
        date_ranges: dict[str, dict[str, str | None]] = {}
        for instrument_type, first_date, last_date in self._session.execute(
            select(
                BenchmarkInstrumentRecord.instrument_type,
                func.min(BenchmarkObservationRecord.observation_date),
                func.max(BenchmarkObservationRecord.observation_date),
            )
            .join(
                BenchmarkObservationRecord,
                BenchmarkObservationRecord.benchmark_instrument_id == BenchmarkInstrumentRecord.id,
            )
            .group_by(BenchmarkInstrumentRecord.instrument_type)
        ).tuples():
            date_ranges[instrument_type] = {
                "first_date": first_date.isoformat() if first_date is not None else None,
                "last_date": last_date.isoformat() if last_date is not None else None,
            }
        issue_counts = dict(
            self._session.execute(
                select(BenchmarkIssueRecord.issue_code, func.count())
                .group_by(BenchmarkIssueRecord.issue_code)
                .order_by(BenchmarkIssueRecord.issue_code)
            )
            .tuples()
            .all()
        )
        latest_runs: dict[str, dict[str, object]] = {}
        for source_family in ("nifty_indices", "exchange_etf"):
            latest = self._session.scalar(
                select(BenchmarkSyncRunRecord)
                .where(BenchmarkSyncRunRecord.source_family == source_family)
                .order_by(BenchmarkSyncRunRecord.started_at.desc())
                .limit(1)
            )
            if latest is not None:
                latest_issue_counts = Counter(
                    dict(
                        self._session.execute(
                            select(BenchmarkIssueRecord.issue_code, func.count())
                            .where(BenchmarkIssueRecord.sync_run_id == latest.id)
                            .group_by(BenchmarkIssueRecord.issue_code)
                        )
                        .tuples()
                        .all()
                    )
                )
                latest_runs[source_family] = {
                    key: value.isoformat() if isinstance(value, date) else value
                    for key, value in asdict(self._result(latest, latest_issue_counts)).items()
                }
        provisional = self._session.scalar(
            select(func.count())
            .select_from(BenchmarkObservationRecord)
            .where(BenchmarkObservationRecord.identity_status == "provisional_current_mapping")
        )
        return BenchmarkCoverageReport(
            parser_version=BENCHMARK_PARSER_VERSION,
            instrument_counts={str(key): int(value) for key, value in instrument_counts.items()},
            observation_counts={str(key): int(value) for key, value in observation_counts.items()},
            exchange_observation_counts={
                str(key): int(value) for key, value in exchange_counts.items()
            },
            observation_date_ranges=date_ranges,
            provisional_bse_observations=int(provisional or 0),
            issue_counts={str(key): int(value) for key, value in issue_counts.items()},
            latest_sync_runs=latest_runs,
        )

    def _sync_nse_etf_date(
        self,
        run: BenchmarkSyncRunRecord,
        observation_date: date,
        mode: str,
        issues: Counter[str],
    ) -> int:
        source_id = "NSE|etf_prices"
        checkpoint = self._checkpoint("nse", source_id, observation_date)
        if mode == "full" and checkpoint is not None:
            run.requests_skipped += 1
            self._session.commit()
            return 0
        try:
            daily_rows, daily_batch = self._capture(
                nse_etf_daily_request(observation_date),
                lambda content: NseEtfDailyParser().parse(content, expected_date=observation_date),
            )
        except _CaptureFailure as failure:
            return self._handle_daily_failure(
                run, "nse", source_id, observation_date, failure, issues
            )
        if not daily_rows:
            self._record_issue(
                run,
                "nse",
                source_id,
                observation_date,
                daily_batch.id,
                "empty_source_period",
                "info",
                "official NSE press report predates the dated ETF membership report; "
                "no ETF identity or price was inferred",
                issues,
            )
            run.requests_completed += 1
            self._upsert_checkpoint(
                run,
                "nse",
                source_id,
                observation_date,
                observation_date,
                daily_batch,
                0,
            )
            self._session.commit()
            return 0
        try:
            price_rows, price_batch = self._capture(
                bhavcopy_request("NSE", observation_date),
                lambda content: _parse_bhavcopy(content, "NSE", observation_date),
            )
        except _CaptureFailure as failure:
            return self._handle_daily_failure(
                run, "nse", source_id, observation_date, failure, issues
            )
        del daily_batch
        prices = {(row.symbol, row.series): row for row in price_rows}
        inserted = unchanged = warnings = 0
        for daily in daily_rows:
            price = prices.get((daily.symbol, daily.series))
            if price is None or price.isin is None:
                details = (
                    f"ETF report identity {(daily.symbol, daily.series)!r} is absent from bhavcopy"
                    if price is None
                    else f"ETF report identity {(daily.symbol, daily.series)!r} has no official "
                    "ISIN in the bhavcopy"
                )
                warnings += 1
                self._record_issue(
                    run,
                    "nse",
                    source_id,
                    observation_date,
                    price_batch.id,
                    "identity_unresolved",
                    "warning",
                    details,
                    issues,
                )
                continue
            instrument = self._ensure_daily_etf_instrument(daily, price, price_batch)
            warnings += self._upsert_listing(instrument, price, price_batch, run, source_id, issues)
            warnings += self._record_exchange_close_issue(
                price, price_batch, run, source_id, issues
            )
            warnings += self._record_exchange_ohlc_issue(price, price_batch, run, source_id, issues)
            if price.close_issue is not None:
                continue
            was_inserted, revised = self._persist_exchange_observation(
                instrument, price, price_batch, run, source_id, "official", issues
            )
            inserted += int(was_inserted)
            unchanged += int(not was_inserted)
            warnings += int(revised)
        run.requests_completed += 1
        run.rows_received += len(daily_rows)
        run.rows_inserted += inserted
        run.rows_unchanged += unchanged
        self._upsert_checkpoint(
            run, "nse", source_id, observation_date, observation_date, price_batch, len(daily_rows)
        )
        self._session.commit()
        return warnings

    def _sync_bse_etf_date(
        self,
        run: BenchmarkSyncRunRecord,
        observation_date: date,
        mode: str,
        issues: Counter[str],
        market_rows: Sequence[BseEtfMarketRow],
        market_batch: IngestionBatchRecord,
    ) -> int:
        source_id = "BSE|etf_prices"
        checkpoint = self._checkpoint("bse", source_id, observation_date)
        if mode == "full" and checkpoint is not None:
            run.requests_skipped += 1
            self._session.commit()
            return 0
        try:
            price_rows, batch = self._capture(
                bhavcopy_request("BSE", observation_date),
                lambda content: _parse_bhavcopy(content, "BSE", observation_date),
            )
        except _CaptureFailure as failure:
            return self._handle_daily_failure(
                run, "bse", source_id, observation_date, failure, issues
            )
        instruments_by_isin = {
            instrument.isin: instrument
            for instrument in self._session.scalars(
                select(BenchmarkInstrumentRecord).where(
                    BenchmarkInstrumentRecord.instrument_type == "etf",
                    BenchmarkInstrumentRecord.isin.is_not(None),
                )
            )
            if instrument.isin is not None
        }
        listings_by_security_id: dict[str, list[BenchmarkExchangeListingRecord]] = {}
        for observed_listing in self._session.scalars(
            select(BenchmarkExchangeListingRecord).where(
                BenchmarkExchangeListingRecord.exchange == "BSE"
            )
        ):
            listings_by_security_id.setdefault(observed_listing.source_security_id, []).append(
                observed_listing
            )
        market_by_security_id = {row.security_id: row for row in market_rows}
        market_as_of_date = market_rows[0].as_of_date
        inserted = unchanged = warnings = matched = 0
        provisional_used = False
        for price in price_rows:
            instrument = instruments_by_isin.get(price.isin) if price.isin is not None else None
            candidate_listings = listings_by_security_id.get(price.security_id, [])
            listing = candidate_listings[0] if len(candidate_listings) == 1 else None
            market_row = market_by_security_id.get(price.security_id)
            if instrument is None and listing is not None:
                instrument = self._session.get(
                    BenchmarkInstrumentRecord, listing.benchmark_instrument_id
                )
            if instrument is None and market_row is not None and price.isin is not None:
                instrument = self._ensure_bse_etf_instrument(market_row, price, market_batch)
            if instrument is None:
                if market_row is not None:
                    warnings += 1
                    self._record_issue(
                        run,
                        "bse",
                        source_id,
                        observation_date,
                        batch.id,
                        "identity_unresolved",
                        "warning",
                        f"BSE ETF scrip {price.security_id!r} has no contemporaneous ISIN "
                        "or prior official listing identity",
                        issues,
                    )
                continue
            identity_status = (
                "official"
                if market_row is not None and observation_date == market_as_of_date
                else "provisional_current_mapping"
            )
            provisional_used = provisional_used or identity_status != "official"
            matched += 1
            if price.isin is not None:
                warnings += self._upsert_listing(instrument, price, batch, run, source_id, issues)
            warnings += self._record_exchange_close_issue(price, batch, run, source_id, issues)
            warnings += self._record_exchange_ohlc_issue(price, batch, run, source_id, issues)
            if price.close_issue is not None:
                continue
            was_inserted, revised = self._persist_exchange_observation(
                instrument, price, batch, run, source_id, identity_status, issues
            )
            inserted += int(was_inserted)
            unchanged += int(not was_inserted)
            warnings += int(revised)
        if provisional_used:
            warnings += 1
            self._record_issue(
                run,
                "bse",
                source_id,
                observation_date,
                batch.id,
                "provisional_bse_identity",
                "warning",
                "BSE ETF identity was established from a later official BSE/NSE "
                "ETF identity mapping",
                issues,
            )
        run.requests_completed += 1
        run.rows_received += matched
        run.rows_inserted += inserted
        run.rows_unchanged += unchanged
        self._upsert_checkpoint(
            run, "bse", source_id, observation_date, observation_date, batch, matched
        )
        self._session.commit()
        return warnings

    def _handle_daily_failure(
        self,
        run: BenchmarkSyncRunRecord,
        provider: str,
        source_id: str,
        observation_date: date,
        failure: _CaptureFailure,
        issues: Counter[str],
    ) -> int:
        if isinstance(failure.error, SourceNotPublishedError):
            self._record_issue(
                run,
                provider,
                source_id,
                observation_date,
                failure.batch.id,
                "empty_trading_day",
                "info",
                f"{failure.error}; no missing price was fabricated",
                issues,
            )
            run.requests_completed += 1
            self._session.commit()
            return 0
        if isinstance(failure.error, SourceParseError):
            raise failure
        if isinstance(failure.error, HTTPError) and failure.error.code == 404:
            self._record_issue(
                run,
                provider,
                source_id,
                observation_date,
                failure.batch.id,
                "empty_trading_day",
                "info",
                "official daily file was not published (HTTP 404); no missing price was fabricated",
                issues,
            )
            run.requests_completed += 1
            self._session.commit()
            return 0
        self._record_issue(
            run,
            provider,
            source_id,
            observation_date,
            failure.batch.id,
            "download_failure",
            "error",
            str(failure),
            issues,
        )
        run.requests_failed += 1
        self._session.commit()
        return 1

    def _capture(
        self, request: BenchmarkRequest, parser: Callable[[bytes], list[T]]
    ) -> tuple[list[T], IngestionBatchRecord]:
        batch = self._repository.start_batch(
            provider=request.provider,
            source_type=request.source_type,
            source_url=request.url,
            request_parameters=request.parameters,
            parser_version=BENCHMARK_PARSER_VERSION,
        )
        try:
            downloaded = self._downloader.download(request)
            stored = self._artifacts.store(downloaded.content)
            self._repository.attach_artifact(
                batch,
                stored,
                media_type=downloaded.media_type,
                http_status=downloaded.status_code,
                final_url=downloaded.final_url,
            )
            rows = parser(downloaded.content)
            self._repository.complete_batch(batch, len(rows))
            return rows, batch
        except (Exception, KeyboardInterrupt) as error:
            self._repository.fail_batch(batch, f"{type(error).__name__}: {error!s}")
            raise _CaptureFailure(batch, error) from error

    def _ensure_nifty_instruments(
        self, mapping: NiftyIndexMapping, batch: IngestionBatchRecord
    ) -> dict[str, BenchmarkInstrumentRecord]:
        definitions = {
            "price": ("price_index", mapping.display_name),
            "gross": ("gross_total_return_index", f"{mapping.display_name} TRI"),
            "net": ("net_total_return_index", f"{mapping.display_name} NTR"),
        }
        result: dict[str, BenchmarkInstrumentRecord] = {}
        for key, (instrument_type, display_name) in definitions.items():
            instrument = self._session.scalar(
                select(BenchmarkInstrumentRecord).where(
                    BenchmarkInstrumentRecord.provider == "nifty_indices",
                    BenchmarkInstrumentRecord.instrument_type == instrument_type,
                    BenchmarkInstrumentRecord.source_identifier == mapping.trading_name,
                )
            )
            if instrument is None:
                instrument = BenchmarkInstrumentRecord(
                    provider="nifty_indices",
                    instrument_type=instrument_type,
                    source_identifier=mapping.trading_name,
                    display_name=display_name,
                    benchmark_family=mapping.display_name,
                    currency="INR",
                    first_observed_batch_id=batch.id,
                )
                self._session.add(instrument)
                self._session.flush()
            result[key] = instrument
        return result

    def _ensure_etf_instrument(
        self, row: EtfMasterRow, batch: IngestionBatchRecord
    ) -> BenchmarkInstrumentRecord:
        instrument = self._instrument_by_isin(row.isin)
        if instrument is None:
            instrument = BenchmarkInstrumentRecord(
                provider="nse",
                instrument_type="etf",
                source_identifier=row.isin,
                display_name=row.security_name,
                benchmark_family=row.underlying,
                currency="INR",
                isin=row.isin,
                source_symbol=row.symbol,
                source_series=None,
                first_observed_batch_id=batch.id,
            )
            self._session.add(instrument)
            self._session.flush()
        else:
            instrument.display_name = row.security_name
            instrument.benchmark_family = row.underlying
            instrument.source_symbol = row.symbol
        return instrument

    def _ensure_daily_etf_instrument(
        self, daily: NseEtfDailyRow, price: ExchangePriceRow, batch: IngestionBatchRecord
    ) -> BenchmarkInstrumentRecord:
        if price.isin is None:
            raise ValueError("official NSE ETF bhavcopy row must have an ISIN")
        instrument = self._instrument_by_isin(price.isin)
        if instrument is None:
            instrument = BenchmarkInstrumentRecord(
                provider="nse",
                instrument_type="etf",
                source_identifier=price.isin,
                display_name=daily.security_name,
                benchmark_family=daily.underlying,
                currency="INR",
                isin=price.isin,
                source_symbol=daily.symbol,
                source_series=daily.series,
                first_observed_batch_id=batch.id,
            )
            self._session.add(instrument)
            self._session.flush()
        return instrument

    def _ensure_bse_etf_instrument(
        self,
        market_row: BseEtfMarketRow,
        price: ExchangePriceRow,
        batch: IngestionBatchRecord,
    ) -> BenchmarkInstrumentRecord:
        if price.isin is None:
            raise ValueError("a new BSE ETF identity requires an official bhavcopy ISIN")
        instrument = self._instrument_by_isin(price.isin)
        if instrument is None:
            instrument = BenchmarkInstrumentRecord(
                provider="bse",
                instrument_type="etf",
                source_identifier=market_row.security_id,
                display_name=market_row.security_name,
                benchmark_family=(
                    f"BSE {market_row.category} ETF"
                    if market_row.category is not None
                    else "BSE ETF (underlying not published in market watch)"
                ),
                currency="INR",
                isin=price.isin,
                source_symbol=market_row.alias,
                source_series=price.series,
                first_observed_batch_id=batch.id,
            )
            self._session.add(instrument)
            self._session.flush()
        return instrument

    def _instrument_by_isin(self, isin: str) -> BenchmarkInstrumentRecord | None:
        return self._session.scalar(
            select(BenchmarkInstrumentRecord).where(
                BenchmarkInstrumentRecord.instrument_type == "etf",
                BenchmarkInstrumentRecord.isin == isin,
            )
        )

    def _upsert_listing(
        self,
        instrument: BenchmarkInstrumentRecord,
        price: ExchangePriceRow,
        batch: IngestionBatchRecord,
        run: BenchmarkSyncRunRecord,
        source_id: str,
        issues: Counter[str],
    ) -> int:
        if price.symbol is None:
            raise ValueError("an official ETF listing requires a non-empty source symbol")
        listing = self._session.scalar(
            select(BenchmarkExchangeListingRecord).where(
                BenchmarkExchangeListingRecord.exchange == price.exchange,
                BenchmarkExchangeListingRecord.source_security_id == price.security_id,
                BenchmarkExchangeListingRecord.benchmark_instrument_id == instrument.id,
            )
        )
        if listing is None:
            prior_listings = self._session.scalars(
                select(BenchmarkExchangeListingRecord).where(
                    BenchmarkExchangeListingRecord.exchange == price.exchange,
                    BenchmarkExchangeListingRecord.source_security_id == price.security_id,
                    BenchmarkExchangeListingRecord.benchmark_instrument_id != instrument.id,
                )
            ).all()
            listing = BenchmarkExchangeListingRecord(
                benchmark_instrument_id=instrument.id,
                exchange=price.exchange,
                source_security_id=price.security_id,
                source_symbol=price.symbol,
                source_series=price.series,
                isin=price.isin,
                first_observed_batch_id=batch.id,
                last_observed_batch_id=batch.id,
            )
            self._session.add(listing)
            if prior_listings:
                prior_identities = sorted(
                    f"symbol={prior.source_symbol}, isin={prior.isin or 'unavailable'}"
                    for prior in prior_listings
                )
                self._record_issue(
                    run,
                    price.exchange.lower(),
                    source_id,
                    price.observation_date,
                    batch.id,
                    "security_identity_change",
                    "warning",
                    f"{price.exchange} security ID {price.security_id} has multiple official "
                    f"ETF identities; histories remain separate: prior={prior_identities}, "
                    f"observed=symbol={price.symbol}, isin={price.isin or 'unavailable'}",
                    issues,
                )
                return 1
        else:
            listing.source_symbol = price.symbol
            listing.source_series = price.series
            listing.isin = price.isin or listing.isin
            listing.last_observed_batch_id = batch.id
            listing.last_observed_at = utc_now()
        return 0

    def _persist_nifty_rows(
        self,
        instruments: dict[str, BenchmarkInstrumentRecord],
        rows: Sequence[NiftyPriceRow] | Sequence[NiftyTotalReturnRow],
        batch: IngestionBatchRecord,
        run: BenchmarkSyncRunRecord,
        source_id: str,
        issues: Counter[str],
    ) -> tuple[int, int, int]:
        inserted = unchanged = revisions = 0
        for row in rows:
            if row.index_name.casefold() != instruments["price"].benchmark_family.casefold():
                raise SourceParseError(
                    "Nifty history response identity does not match the requested official index: "
                    f"requested={instruments['price'].benchmark_family!r}, "
                    f"returned={row.index_name!r}"
                )
            values: list[
                tuple[
                    BenchmarkInstrumentRecord,
                    Decimal | None,
                    Decimal | None,
                    Decimal | None,
                    Decimal,
                ]
            ]
            if isinstance(row, NiftyPriceRow):
                if row.ohlc_issue is not None:
                    self._record_issue(
                        run,
                        batch.provider,
                        source_id,
                        row.observation_date,
                        batch.id,
                        "invalid_ohlc",
                        "warning",
                        row.ohlc_issue,
                        issues,
                    )
                values = [
                    (
                        instruments["price"],
                        row.open_value,
                        row.high_value,
                        row.low_value,
                        row.close_value,
                    )
                ]
            else:
                values = [(instruments["gross"], None, None, None, row.gross_total_return)]
                if row.net_total_return is not None:
                    values.append((instruments["net"], None, None, None, row.net_total_return))
            for instrument, open_value, high_value, low_value, close_value in values:
                was_inserted, revised = self._persist_observation(
                    instrument=instrument,
                    exchange=None,
                    observation_date=row.observation_date,
                    open_value=open_value,
                    high_value=high_value,
                    low_value=low_value,
                    close_value=close_value,
                    last_value=None,
                    previous_close=None,
                    volume=None,
                    traded_value=None,
                    trade_count=None,
                    identity_status="official",
                    batch=batch,
                    run=run,
                    source_id=source_id,
                    issues=issues,
                )
                inserted += int(was_inserted)
                unchanged += int(not was_inserted)
                revisions += int(revised)
        return inserted, unchanged, revisions

    def _persist_exchange_observation(
        self,
        instrument: BenchmarkInstrumentRecord,
        price: ExchangePriceRow,
        batch: IngestionBatchRecord,
        run: BenchmarkSyncRunRecord,
        source_id: str,
        identity_status: str,
        issues: Counter[str],
    ) -> tuple[bool, bool]:
        if price.close_issue is not None or price.close_value <= 0:
            raise ValueError("an exchange observation requires a trustworthy positive close")
        return self._persist_observation(
            instrument=instrument,
            exchange=price.exchange,
            observation_date=price.observation_date,
            open_value=price.open_value,
            high_value=price.high_value,
            low_value=price.low_value,
            close_value=price.close_value,
            last_value=price.last_value,
            previous_close=price.previous_close,
            volume=price.volume,
            traded_value=price.traded_value,
            trade_count=price.trade_count,
            identity_status=identity_status,
            batch=batch,
            run=run,
            source_id=source_id,
            issues=issues,
        )

    def _record_exchange_close_issue(
        self,
        price: ExchangePriceRow,
        batch: IngestionBatchRecord,
        run: BenchmarkSyncRunRecord,
        source_id: str,
        issues: Counter[str],
    ) -> int:
        if price.close_issue is None:
            return 0
        self._record_issue(
            run,
            price.exchange.casefold(),
            source_id,
            price.observation_date,
            batch.id,
            "invalid_close",
            "error",
            price.close_issue,
            issues,
        )
        return 1

    def _record_exchange_ohlc_issue(
        self,
        price: ExchangePriceRow,
        batch: IngestionBatchRecord,
        run: BenchmarkSyncRunRecord,
        source_id: str,
        issues: Counter[str],
    ) -> int:
        if price.ohlc_issue is None:
            return 0
        self._record_issue(
            run,
            price.exchange.casefold(),
            source_id,
            price.observation_date,
            batch.id,
            "invalid_ohlc",
            "warning",
            price.ohlc_issue,
            issues,
        )
        return 1

    def _persist_observation(
        self,
        *,
        instrument: BenchmarkInstrumentRecord,
        exchange: str | None,
        observation_date: date,
        open_value: Decimal | None,
        high_value: Decimal | None,
        low_value: Decimal | None,
        close_value: Decimal,
        last_value: Decimal | None,
        previous_close: Decimal | None,
        volume: int | None,
        traded_value: Decimal | None,
        trade_count: int | None,
        identity_status: str,
        batch: IngestionBatchRecord,
        run: BenchmarkSyncRunRecord,
        source_id: str,
        issues: Counter[str],
    ) -> tuple[bool, bool]:
        values = {
            "instrument_id": instrument.id,
            "exchange": exchange,
            "observation_date": observation_date.isoformat(),
            "open": str(open_value) if open_value is not None else None,
            "high": str(high_value) if high_value is not None else None,
            "low": str(low_value) if low_value is not None else None,
            "close": str(close_value),
            "last": str(last_value) if last_value is not None else None,
            "previous_close": str(previous_close) if previous_close is not None else None,
            "volume": volume,
            "traded_value": str(traded_value) if traded_value is not None else None,
            "trade_count": trade_count,
            "identity_status": identity_status,
        }
        signature = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        observation = self._session.scalar(
            select(BenchmarkObservationRecord).where(
                BenchmarkObservationRecord.content_signature == signature
            )
        )
        inserted = observation is None
        revised = False
        if observation is None:
            prior_exists = self._session.scalar(
                select(func.count())
                .select_from(BenchmarkObservationRecord)
                .where(
                    BenchmarkObservationRecord.benchmark_instrument_id == instrument.id,
                    BenchmarkObservationRecord.exchange == exchange,
                    BenchmarkObservationRecord.observation_date == observation_date,
                )
            )
            observation = BenchmarkObservationRecord(
                benchmark_instrument_id=instrument.id,
                exchange=exchange,
                observation_date=observation_date,
                open_value=open_value,
                high_value=high_value,
                low_value=low_value,
                close_value=close_value,
                last_value=last_value,
                previous_close=previous_close,
                volume=volume,
                traded_value=traded_value,
                trade_count=trade_count,
                identity_status=identity_status,
                content_signature=signature,
                first_observed_batch_id=batch.id,
            )
            self._session.add(observation)
            self._session.flush()
            if prior_exists:
                revised = True
                self._record_issue(
                    run,
                    batch.provider,
                    source_id,
                    observation_date,
                    batch.id,
                    "observation_revision",
                    "warning",
                    "official source value differs from an earlier immutable observation",
                    issues,
                )
        source = self._session.get(BenchmarkObservationSourceRecord, (observation.id, batch.id))
        if source is None:
            self._session.add(
                BenchmarkObservationSourceRecord(
                    benchmark_observation_id=observation.id,
                    ingestion_batch_id=batch.id,
                )
            )
        return inserted, revised

    def _record_issue(
        self,
        run: BenchmarkSyncRunRecord,
        provider: str,
        source_identifier: str,
        observation_date: date | None,
        batch_id: str | None,
        issue_code: str,
        severity: str,
        details: str,
        counts: Counter[str],
    ) -> None:
        self._session.add(
            BenchmarkIssueRecord(
                sync_run_id=run.id,
                provider=provider,
                source_identifier=source_identifier,
                observation_date=observation_date,
                ingestion_batch_id=batch_id,
                issue_code=issue_code,
                severity=severity,
                details=details[:4000],
            )
        )
        counts[issue_code] += 1

    def _checkpoint(
        self, provider: str, source_identifier: str, period_start: date
    ) -> BenchmarkSyncCheckpointRecord | None:
        return self._session.get(
            BenchmarkSyncCheckpointRecord,
            (provider, source_identifier, period_start),
        )

    def _upsert_checkpoint(
        self,
        run: BenchmarkSyncRunRecord,
        provider: str,
        source_identifier: str,
        period_start: date,
        period_end: date,
        batch: IngestionBatchRecord,
        rows_received: int,
    ) -> None:
        checkpoint = self._checkpoint(provider, source_identifier, period_start)
        if checkpoint is None:
            checkpoint = BenchmarkSyncCheckpointRecord(
                provider=provider,
                source_identifier=source_identifier,
                period_start=period_start,
                period_end=period_end,
                last_batch_id=batch.id,
                last_sync_run_id=run.id,
                rows_received=rows_received,
            )
            self._session.add(checkpoint)
        else:
            checkpoint.period_end = period_end
            checkpoint.last_batch_id = batch.id
            checkpoint.last_sync_run_id = run.id
            checkpoint.rows_received = rows_received
            checkpoint.updated_at = utc_now()

    def _start_run(
        self, source_family: str, mode: str, start_date: date, end_date: date
    ) -> BenchmarkSyncRunRecord:
        self._fail_stale_runs(source_family)
        run = BenchmarkSyncRunRecord(
            source_family=source_family,
            mode=mode,
            status="running",
            requested_start_date=start_date,
            requested_end_date=end_date,
        )
        self._session.add(run)
        self._session.commit()
        return run

    def _fail_stale_runs(self, source_family: str) -> None:
        stale = self._session.scalars(
            select(BenchmarkSyncRunRecord).where(
                BenchmarkSyncRunRecord.source_family == source_family,
                BenchmarkSyncRunRecord.status == "running",
            )
        ).all()
        for run in stale:
            run.status = "failed"
            run.completed_at = utc_now()
            run.error_details = "StaleRunReconciled: superseded by a new benchmark sync"
        if stale:
            self._session.commit()

    def _complete_run(self, run: BenchmarkSyncRunRecord, warning_count: int) -> None:
        run.status = (
            "completed_with_issues" if warning_count > 0 or run.requests_failed > 0 else "completed"
        )
        run.completed_at = utc_now()
        self._session.commit()

    def _fail_run(self, run: BenchmarkSyncRunRecord, error: BaseException) -> None:
        self._session.rollback()
        failed = self._session.get(BenchmarkSyncRunRecord, run.id)
        if failed is not None:
            failed.status = "failed"
            failed.completed_at = utc_now()
            failed.error_details = f"{type(error).__name__}: {error!s}"[:4000]
            self._session.commit()

    @staticmethod
    def _validate_run(mode: str, start_date: date, end_date: date) -> None:
        if mode not in {"full", "refresh"}:
            raise ValueError("benchmark sync mode must be full or refresh")
        if end_date < start_date:
            raise ValueError("benchmark end date cannot precede start date")

    @staticmethod
    def _result(run: BenchmarkSyncRunRecord, issues: Counter[str]) -> BenchmarkSyncResult:
        return BenchmarkSyncResult(
            run_id=run.id,
            source_family=run.source_family,
            mode=run.mode,
            status=run.status,
            requests_total=run.requests_total,
            requests_completed=run.requests_completed,
            requests_skipped=run.requests_skipped,
            requests_failed=run.requests_failed,
            rows_received=run.rows_received,
            rows_inserted=run.rows_inserted,
            rows_unchanged=run.rows_unchanged,
            issue_counts=dict(sorted(issues.items())),
        )


def _parse_bhavcopy(
    content: bytes, exchange: str, observation_date: date
) -> list[ExchangePriceRow]:
    if observation_date >= UDIFF_START_DATE:
        return UdIffBhavcopyParser().parse(
            content, expected_exchange=exchange, expected_date=observation_date
        )
    if exchange == "NSE":
        return LegacyNseBhavcopyParser().parse(content, expected_date=observation_date)
    return LegacyBseBhavcopyParser().parse(content, expected_date=observation_date)


def _inclusive_windows(
    start_date: date, end_date: date, maximum_days: int
) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        window_end = min(cursor + timedelta(days=maximum_days - 1), end_date)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _dates_descending(start_date: date, end_date: date) -> list[date]:
    return [end_date - timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]
