from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSchemeRecord,
    AdvisorkhojCatalogSnapshotRecord,
    AdvisorkhojDistributionIssueRecord,
    AdvisorkhojDistributionRecord,
    AdvisorkhojDistributionRecordSource,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    SchemeMetadataVersionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.ingestion.advisorkhoj import (
    PARSER_VERSION,
    AdvisorkhojCaptureParser,
    AdvisorkhojCatalog,
    AdvisorkhojDistributionRow,
    AdvisorkhojSchemeCapture,
    parse_catalog,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.distribution_precedence import (
    DistributionSourceTier,
    canonical_revision_source_tier,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.advisorkhoj.com/mutual-funds-research/mutual-funds-historical-dividends/"
_EARLIEST_PLAUSIBLE_RECORD_DATE = date(1964, 1, 1)
_MINIMUM_NAV_MATCHES = 3
_MINIMUM_EVIDENCE_SPAN_DAYS = 30
ADVISORKHOJ_NORMALIZATION_VERSION = "advisorkhoj-distribution-2026.08.2"
_EVENT_TYPE = "idcw_cash"
_NON_IDENTITY_WORDS = frozenset(
    {
        "annual",
        "daily",
        "direct",
        "fortnightly",
        "fund",
        "halfyearly",
        "regular",
        "plan",
        "option",
        "idcw",
        "dividend",
        "monthly",
        "payout",
        "quarterly",
        "reinvestment",
        "reinvest",
        "reinv",
        "weekly",
    }
)
_TOKEN_EXPANSIONS = {
    "advtg": "advantage",
    "dir": "direct",
    "fortn": "fortnightly",
    "gr": "growth",
    "hly": "halfyearly",
    "mkt": "market",
    "mly": "monthly",
    "qly": "quarterly",
    "reg": "regular",
    "wly": "weekly",
}


@dataclass(frozen=True)
class AdvisorkhojNavEvidence:
    nav_date: str
    nav_value: str


@dataclass(frozen=True)
class AdvisorkhojNavConflict:
    nav_date: str
    source_nav_value: str
    amfi_nav_value: str


@dataclass(frozen=True)
class AdvisorkhojMappingCandidate:
    amfi_scheme_code: str
    amfi_scheme_names: tuple[str, ...]
    amfi_plan_types: tuple[str, ...]
    comparable_nav_points: int
    exact_nav_matches: int
    nav_conflicts: int
    missing_amfi_nav_points: int
    evidence_span_days: int
    qualifying: bool
    matched_nav_evidence: tuple[AdvisorkhojNavEvidence, ...]
    conflict_evidence: tuple[AdvisorkhojNavConflict, ...]


@dataclass(frozen=True)
class AdvisorkhojMappingAssessment:
    requested_scheme_name: str
    display_scheme_name: str
    source_url: str
    source_payload_sha256: str
    plan_type: str
    option_variant: str
    source_frequency: str
    display_frequency: str
    frequency_conflict: bool
    source_rows: int
    positive_source_rows: int
    zero_source_rows: int
    source_name_core: str
    status: str
    amfi_scheme_code: str | None
    mapping_method: str
    conclusion: str
    candidates: tuple[AdvisorkhojMappingCandidate, ...]


@dataclass(frozen=True)
class AdvisorkhojPilotImportResult:
    ingestion_batch_id: str
    artifact_sha256: str
    artifact_reused: bool
    parser_version: str
    mapping_policy: str
    amfi_nav_max_observed_at: str | None
    captures_received: int
    source_rows_received: int
    positive_source_rows: int
    zero_source_rows: int
    mapped_captures: int
    unresolved_captures: int
    ambiguous_captures: int
    canonical_events_published: int
    assessments: tuple[AdvisorkhojMappingAssessment, ...]


@dataclass(frozen=True)
class AdvisorkhojAcquisitionResult:
    catalog_ingestion_batch_id: str
    capture_ingestion_batch_id: str
    catalog_artifact_sha256: str
    capture_artifact_sha256: str
    catalog_amcs: int
    catalog_category_queries: int
    catalog_schemes: int
    captures_imported: int
    captures_inserted: int
    source_rows_received: int
    source_rows_inserted: int
    positive_source_rows: int
    zero_source_rows: int
    negative_source_rows: int
    zero_reference_nav_rows: int
    implausible_historical_date_rows: int
    future_dated_source_rows: int
    mapped_captures: int
    unresolved_captures: int
    ambiguous_captures: int
    canonical_events_published: int
    unresolved_schemes: tuple[str, ...]
    ambiguous_schemes: tuple[str, ...]


@dataclass(frozen=True)
class AdvisorkhojMappingRefreshResult:
    captures_reviewed: int
    mapped_captures: int
    unresolved_captures: int
    ambiguous_captures: int
    canonical_events_published: int


@dataclass(frozen=True)
class AdvisorkhojPublicationResult:
    normalization_run_id: str
    status: str
    options_processed: int
    source_rows_examined: int
    events_inserted: int
    revisions_inserted: int
    revisions_retired_for_conflict: int
    source_rows_linked: int
    source_rows_blocked: int
    higher_priority_conflicts: int
    same_priority_conflicts: int


class AdvisorkhojDistributionPilotService:
    """Retain, map, and publish AdvisorKhoj only as a tertiary fallback source."""

    def __init__(
        self,
        session: Session,
        repository: IngestionRepository,
        artifacts: ArtifactStore,
    ) -> None:
        self._session = session
        self._repository = repository
        self._artifacts = artifacts
        self._metadata_by_amc: dict[str, tuple[SchemeMetadataVersionRecord, ...]] | None = None

    def publish_pending(self, *, option_limit: int | None = None) -> AdvisorkhojPublicationResult:
        """Publish mapped positive rows, committing each AMFI option for resumability."""
        if option_limit is not None and option_limit < 1:
            raise ValueError("option_limit must be positive")
        run = DistributionNormalizationRunRecord(
            status="running", normalization_version=ADVISORKHOJ_NORMALIZATION_VERSION
        )
        self._session.add(run)
        self._session.commit()
        counters = {
            "options_processed": 0,
            "source_rows_examined": 0,
            "events_inserted": 0,
            "revisions_inserted": 0,
            "revisions_retired_for_conflict": 0,
            "source_rows_linked": 0,
            "source_rows_blocked": 0,
            "higher_priority_conflicts": 0,
            "same_priority_conflicts": 0,
        }
        try:
            reviews_by_code = self._latest_mapped_reviews_by_code()
            for scheme_code in sorted(reviews_by_code):
                pending = self._pending_rows(reviews_by_code[scheme_code])
                if not pending:
                    continue
                if option_limit is not None and counters["options_processed"] >= option_limit:
                    break
                counters["options_processed"] += 1
                counters["source_rows_examined"] += len(pending)
                self._publish_option_rows(run, scheme_code, pending, counters)
                self._update_publication_run(run, counters)
                self._session.commit()
            run.status = "completed"
            run.completed_at = utc_now()
            self._update_publication_run(run, counters)
            self._session.commit()
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed = self._session.get(DistributionNormalizationRunRecord, run.id)
            if failed is not None:
                failed.status = "failed"
                failed.completed_at = utc_now()
                failed.error_details = f"{type(error).__name__}: {error!s}"[:4000]
                self._session.commit()
            raise
        return AdvisorkhojPublicationResult(
            normalization_run_id=run.id,
            status=run.status,
            **counters,
        )

    def _latest_mapped_reviews_by_code(
        self,
    ) -> dict[str, tuple[AdvisorkhojSchemeMappingReviewRecord, ...]]:
        latest: dict[str, AdvisorkhojSchemeMappingReviewRecord] = {}
        for review in self._session.scalars(
            select(AdvisorkhojSchemeMappingReviewRecord).order_by(
                AdvisorkhojSchemeMappingReviewRecord.reviewed_at,
                AdvisorkhojSchemeMappingReviewRecord.id,
            )
        ):
            latest[review.scheme_capture_id] = review
        grouped: dict[str, list[AdvisorkhojSchemeMappingReviewRecord]] = {}
        for review in latest.values():
            if review.status == "mapped" and review.amfi_scheme_code is not None:
                grouped.setdefault(review.amfi_scheme_code, []).append(review)
        return {code: tuple(reviews) for code, reviews in grouped.items()}

    def _pending_rows(
        self, reviews: tuple[AdvisorkhojSchemeMappingReviewRecord, ...]
    ) -> tuple[tuple[AdvisorkhojDistributionRecord, AdvisorkhojSchemeMappingReviewRecord], ...]:
        review_by_capture = {review.scheme_capture_id: review for review in reviews}
        records = tuple(
            self._session.scalars(
                select(AdvisorkhojDistributionRecord).where(
                    AdvisorkhojDistributionRecord.scheme_capture_id.in_(review_by_capture),
                    AdvisorkhojDistributionRecord.quality_status == "valid",
                    AdvisorkhojDistributionRecord.is_positive_cash_distribution.is_(True),
                )
            )
        )
        if not records:
            return ()
        record_ids = tuple(record.id for record in records)
        linked_ids = set(
            self._session.scalars(
                select(
                    DistributionEventRevisionAdvisorkhojSourceRecord.advisorkhoj_distribution_record_id
                ).where(
                    DistributionEventRevisionAdvisorkhojSourceRecord.advisorkhoj_distribution_record_id.in_(
                        record_ids
                    )
                )
            )
        )
        issue_pairs = set(
            self._session.execute(
                select(
                    AdvisorkhojDistributionIssueRecord.advisorkhoj_distribution_record_id,
                    AdvisorkhojDistributionIssueRecord.mapping_review_id,
                ).where(
                    AdvisorkhojDistributionIssueRecord.advisorkhoj_distribution_record_id.in_(
                        record_ids
                    )
                )
            ).tuples()
        )
        return tuple(
            (record, review_by_capture[record.scheme_capture_id])
            for record in records
            if record.id not in linked_ids
            and (record.id, review_by_capture[record.scheme_capture_id].id) not in issue_pairs
        )

    def _publish_option_rows(
        self,
        run: DistributionNormalizationRunRecord,
        scheme_code: str,
        pending: tuple[
            tuple[AdvisorkhojDistributionRecord, AdvisorkhojSchemeMappingReviewRecord], ...
        ],
        counters: dict[str, int],
    ) -> None:
        plausible: list[
            tuple[AdvisorkhojDistributionRecord, AdvisorkhojSchemeMappingReviewRecord]
        ] = []
        for source, review in pending:
            if source.record_date < _EARLIEST_PLAUSIBLE_RECORD_DATE:
                self._block_advisorkhoj_rows(
                    run,
                    [(source, review)],
                    "implausible_record_date",
                    "AdvisorKhoj row was retained but not published because its literal "
                    "record date precedes Indian mutual funds: "
                    f"record_date={source.record_date}",
                )
                counters["source_rows_blocked"] += 1
            else:
                plausible.append((source, review))
        rows_by_date: dict[
            date,
            list[tuple[AdvisorkhojDistributionRecord, AdvisorkhojSchemeMappingReviewRecord]],
        ] = {}
        for source, review in plausible:
            rows_by_date.setdefault(source.record_date, []).append((source, review))
        for record_date, sources in sorted(rows_by_date.items()):
            amounts = {source.amount_per_unit_inr for source, _ in sources}
            if len(amounts) != 1:
                self._block_advisorkhoj_rows(
                    run,
                    sources,
                    "same_priority_conflict",
                    f"AdvisorKhoj rows disagree for AMFI code={scheme_code}, "
                    f"record_date={record_date}, amounts={sorted(map(str, amounts))}",
                )
                counters["source_rows_blocked"] += len(sources)
                counters["same_priority_conflicts"] += len(sources)
                continue
            amount = next(iter(amounts))
            event = self._session.scalar(
                select(DistributionEventRecord).where(
                    DistributionEventRecord.amfi_scheme_code == scheme_code,
                    DistributionEventRecord.record_date == record_date,
                    DistributionEventRecord.event_type == _EVENT_TYPE,
                )
            )
            current = None
            if event is not None:
                current = self._session.scalar(
                    select(DistributionEventRevisionRecord).where(
                        DistributionEventRevisionRecord.distribution_event_id == event.id,
                        DistributionEventRevisionRecord.is_current.is_(True),
                    )
                )
            if current is not None and current.amount_per_unit_inr != amount:
                tier = canonical_revision_source_tier(self._session, current.id)
                issue_code = (
                    "higher_priority_conflict"
                    if tier < DistributionSourceTier.ADVISORKHOJ
                    else "same_priority_conflict"
                )
                self._block_advisorkhoj_rows(
                    run,
                    sources,
                    issue_code,
                    f"AdvisorKhoj amount conflicts with current tier={tier.name.lower()} "
                    f"canonical value: AMFI code={scheme_code}, record_date={record_date}, "
                    f"canonical={current.amount_per_unit_inr}, advisorkhoj={amount}",
                )
                counters["source_rows_blocked"] += len(sources)
                counters[f"{issue_code}s"] += len(sources)
                if tier == DistributionSourceTier.ADVISORKHOJ:
                    current.is_current = False
                    counters["revisions_retired_for_conflict"] += 1
                continue
            if current is None and event is not None:
                self._block_advisorkhoj_rows(
                    run,
                    sources,
                    "same_priority_conflict",
                    f"No canonical value exists after a prior peer-source conflict: "
                    f"AMFI code={scheme_code}, record_date={record_date}",
                )
                counters["source_rows_blocked"] += len(sources)
                counters["same_priority_conflicts"] += len(sources)
                continue
            if event is None:
                event = DistributionEventRecord(
                    id=new_id(),
                    amfi_scheme_code=scheme_code,
                    record_date=record_date,
                    event_type=_EVENT_TYPE,
                )
                self._session.add(event)
                self._session.flush()
                counters["events_inserted"] += 1
            if current is None:
                revision_number = (
                    self._session.scalar(
                        select(func.max(DistributionEventRevisionRecord.revision_number)).where(
                            DistributionEventRevisionRecord.distribution_event_id == event.id
                        )
                    )
                    or 0
                ) + 1
                current = DistributionEventRevisionRecord(
                    id=new_id(),
                    distribution_event_id=event.id,
                    amount_per_unit_inr=amount,
                    revision_number=revision_number,
                    content_signature=_canonical_revision_signature(
                        scheme_code, record_date, amount, revision_number
                    ),
                    normalization_version=ADVISORKHOJ_NORMALIZATION_VERSION,
                    normalization_run_id=run.id,
                    is_current=True,
                )
                self._session.add(current)
                self._session.flush()
                counters["revisions_inserted"] += 1
            for source, review in sources:
                self._session.add(
                    DistributionEventRevisionAdvisorkhojSourceRecord(
                        distribution_event_revision_id=current.id,
                        advisorkhoj_distribution_record_id=source.id,
                        mapping_review_id=review.id,
                    )
                )
                counters["source_rows_linked"] += 1

    def _block_advisorkhoj_rows(
        self,
        run: DistributionNormalizationRunRecord,
        sources: list[tuple[AdvisorkhojDistributionRecord, AdvisorkhojSchemeMappingReviewRecord]],
        issue_code: str,
        details: str,
    ) -> None:
        for source, review in sources:
            self._session.add(
                AdvisorkhojDistributionIssueRecord(
                    advisorkhoj_distribution_record_id=source.id,
                    mapping_review_id=review.id,
                    normalization_run_id=run.id,
                    issue_code=issue_code,
                    details=details,
                )
            )

    @staticmethod
    def _update_publication_run(
        run: DistributionNormalizationRunRecord, counters: dict[str, int]
    ) -> None:
        run.source_rows_examined = counters["source_rows_examined"]
        run.candidate_rows = counters["source_rows_linked"]
        run.blocked_rows = counters["source_rows_blocked"]
        run.events_inserted = counters["events_inserted"]
        run.revisions_inserted = counters["revisions_inserted"]
        run.rows_unchanged = counters["source_rows_linked"] - counters["revisions_inserted"]

    def acquire_catalog(
        self, catalog_file: Path, capture_file: Path
    ) -> AdvisorkhojAcquisitionResult:
        catalog_payload = catalog_file.read_bytes()
        capture_payload = capture_file.read_bytes()
        catalog_sha256 = hashlib.sha256(catalog_payload).hexdigest()
        catalog = parse_catalog(catalog_payload)
        catalog_batch = self._repository.start_batch(
            provider="advisorkhoj",
            source_type="secondary_distribution_catalog",
            source_url=catalog.source_url,
            request_parameters={"capture_format": "advisorkhoj-catalog-v2"},
            parser_version=PARSER_VERSION,
        )
        try:
            catalog_stored = self._artifacts.store(catalog_payload)
            catalog_attachment = self._repository.attach_artifact(
                catalog_batch,
                catalog_stored,
                media_type="application/json; charset=utf-8",
                http_status=200,
                final_url=catalog.source_url,
            )
            _, catalog_schemes = self._persist_catalog(catalog, catalog_sha256, catalog_batch)
            self._repository.complete_batch(catalog_batch, len(catalog.schemes))
        except (Exception, KeyboardInterrupt) as error:
            self._repository.fail_batch(catalog_batch, f"{type(error).__name__}: {error!s}")
            raise

        capture_batch = self._repository.start_batch(
            provider="advisorkhoj",
            source_type="secondary_distribution_capture",
            source_url=_BASE_URL,
            request_parameters={
                "capture_format": "advisorkhoj-jsonl-v2",
                "catalog_sha256": catalog_sha256,
                "catalog_batch_id": catalog_batch.id,
            },
            parser_version=PARSER_VERSION,
        )
        try:
            capture_stored = self._artifacts.store(capture_payload)
            capture_attachment = self._repository.attach_artifact(
                capture_batch,
                capture_stored,
                media_type="application/x-ndjson; charset=utf-8",
                http_status=200,
                final_url=_BASE_URL,
            )
            captures = AdvisorkhojCaptureParser().parse_records(capture_payload)
            self._validate_complete_capture(catalog, catalog_sha256, catalog_schemes, captures)
            stored_captures, captures_inserted = self._persist_full_captures(
                captures, catalog_schemes, capture_batch
            )
            source_rows_inserted = self._persist_full_rows(captures, stored_captures, capture_batch)
            conclusions = tuple(
                self._record_full_mapping(source, stored)
                for source, stored in zip(captures, stored_captures, strict=True)
            )
            source_rows = sum(len(capture.rows) for capture in captures)
            self._repository.complete_batch(capture_batch, source_rows)
        except (Exception, KeyboardInterrupt) as error:
            self._repository.fail_batch(capture_batch, f"{type(error).__name__}: {error!s}")
            raise

        result = AdvisorkhojAcquisitionResult(
            catalog_ingestion_batch_id=catalog_batch.id,
            capture_ingestion_batch_id=capture_batch.id,
            catalog_artifact_sha256=catalog_attachment.artifact.sha256,
            capture_artifact_sha256=capture_attachment.artifact.sha256,
            catalog_amcs=len(catalog.amcs),
            catalog_category_queries=catalog.category_queries,
            catalog_schemes=len(catalog.schemes),
            captures_imported=len(captures),
            captures_inserted=captures_inserted,
            source_rows_received=source_rows,
            source_rows_inserted=source_rows_inserted,
            positive_source_rows=sum(
                row.is_positive_cash_distribution for capture in captures for row in capture.rows
            ),
            zero_source_rows=sum(
                row.amount_per_unit_inr == 0 for capture in captures for row in capture.rows
            ),
            negative_source_rows=sum(
                row.amount_per_unit_inr < 0 for capture in captures for row in capture.rows
            ),
            zero_reference_nav_rows=sum(
                row.reference_nav == 0 for capture in captures for row in capture.rows
            ),
            implausible_historical_date_rows=sum(
                row.record_date < _EARLIEST_PLAUSIBLE_RECORD_DATE
                for capture in captures
                for row in capture.rows
            ),
            future_dated_source_rows=sum(
                row.record_date > capture.captured_at.date()
                for capture in captures
                for row in capture.rows
            ),
            mapped_captures=sum(item[0] == "mapped" for item in conclusions),
            unresolved_captures=sum(item[0] == "unresolved" for item in conclusions),
            ambiguous_captures=sum(item[0] == "ambiguous" for item in conclusions),
            canonical_events_published=0,
            unresolved_schemes=tuple(
                source.requested_scheme_name
                for source, conclusion in zip(captures, conclusions, strict=True)
                if conclusion[0] == "unresolved"
            ),
            ambiguous_schemes=tuple(
                source.requested_scheme_name
                for source, conclusion in zip(captures, conclusions, strict=True)
                if conclusion[0] == "ambiguous"
            ),
        )
        logger.info(
            "advisorkhoj_acquisition_completed",
            extra={
                "event_data": {
                    key: value
                    for key, value in result.__dict__.items()
                    if key not in {"unresolved_schemes", "ambiguous_schemes"}
                }
            },
        )
        return result

    def _persist_catalog(
        self,
        catalog: AdvisorkhojCatalog,
        catalog_sha256: str,
        batch: IngestionBatchRecord,
    ) -> tuple[
        AdvisorkhojCatalogSnapshotRecord,
        dict[tuple[str, str], AdvisorkhojCatalogSchemeRecord],
    ]:
        snapshot = self._session.scalar(
            select(AdvisorkhojCatalogSnapshotRecord).where(
                AdvisorkhojCatalogSnapshotRecord.catalog_sha256 == catalog_sha256
            )
        )
        if snapshot is None:
            snapshot = AdvisorkhojCatalogSnapshotRecord(
                ingestion_batch_id=batch.id,
                catalog_sha256=catalog_sha256,
                amc_count=len(catalog.amcs),
                category_query_count=catalog.category_queries,
                scheme_count=len(catalog.schemes),
                captured_at=catalog.captured_at,
            )
            self._session.add(snapshot)
            self._session.flush()
            for scheme in catalog.schemes:
                self._session.add(
                    AdvisorkhojCatalogSchemeRecord(
                        catalog_snapshot_id=snapshot.id,
                        amc_name=scheme.amc_name,
                        category=scheme.category,
                        scheme_name=scheme.scheme_name,
                        content_signature=_signature(
                            "catalog-scheme",
                            catalog_sha256,
                            scheme.amc_name,
                            scheme.category,
                            scheme.scheme_name,
                        ),
                    )
                )
            self._session.commit()
        rows = tuple(
            self._session.scalars(
                select(AdvisorkhojCatalogSchemeRecord).where(
                    AdvisorkhojCatalogSchemeRecord.catalog_snapshot_id == snapshot.id
                )
            ).all()
        )
        if len(rows) != len(catalog.schemes):
            raise RuntimeError("stored AdvisorKhoj catalog scheme count is inconsistent")
        return snapshot, {(row.amc_name, row.scheme_name): row for row in rows}

    @staticmethod
    def _validate_complete_capture(
        catalog: AdvisorkhojCatalog,
        catalog_sha256: str,
        catalog_schemes: dict[tuple[str, str], AdvisorkhojCatalogSchemeRecord],
        captures: tuple[AdvisorkhojSchemeCapture, ...],
    ) -> None:
        expected = {(item.amc_name, item.scheme_name) for item in catalog.schemes}
        received = {(item.amc_name, item.requested_scheme_name) for item in captures}
        if len(received) != len(captures):
            raise ValueError("AdvisorKhoj capture contains duplicate AMC/scheme entries")
        if expected != received or set(catalog_schemes) != expected:
            missing = sorted(expected - received)
            extra = sorted(received - expected)
            raise ValueError(
                "AdvisorKhoj capture is incomplete for its catalog: "
                f"missing={len(missing)}, extra={len(extra)}, "
                f"first_missing={missing[:3]!r}, first_extra={extra[:3]!r}"
            )
        if any(item.catalog_sha256 != catalog_sha256 for item in captures):
            raise ValueError("AdvisorKhoj capture references a different catalog artifact")

    def _persist_full_captures(
        self,
        captures: tuple[AdvisorkhojSchemeCapture, ...],
        catalog_schemes: dict[tuple[str, str], AdvisorkhojCatalogSchemeRecord],
        batch: IngestionBatchRecord,
    ) -> tuple[tuple[AdvisorkhojSchemeCaptureRecord, ...], int]:
        stored: list[AdvisorkhojSchemeCaptureRecord] = []
        inserted = 0
        for source in captures:
            catalog_scheme = catalog_schemes[(source.amc_name, source.requested_scheme_name)]
            signature = _signature(
                "capture",
                catalog_scheme.id,
                source.source_payload_sha256,
            )
            record = self._session.scalar(
                select(AdvisorkhojSchemeCaptureRecord).where(
                    AdvisorkhojSchemeCaptureRecord.capture_signature == signature
                )
            )
            if record is None:
                record = AdvisorkhojSchemeCaptureRecord(
                    catalog_scheme_id=catalog_scheme.id,
                    source_url=source.source_url,
                    source_payload_sha256=source.source_payload_sha256,
                    plan_type=source.plan_type,
                    option_variant=source.option_variant,
                    source_frequency=source.source_frequency,
                    source_row_count=len(source.rows),
                    capture_signature=signature,
                    ingestion_batch_id=batch.id,
                    captured_at=source.captured_at,
                )
                self._session.add(record)
                self._session.flush()
                inserted += 1
            stored.append(record)
        self._session.commit()
        return tuple(stored), inserted

    def _persist_full_rows(
        self,
        captures: tuple[AdvisorkhojSchemeCapture, ...],
        stored_captures: tuple[AdvisorkhojSchemeCaptureRecord, ...],
        batch: IngestionBatchRecord,
    ) -> int:
        existing_by_signature = dict(
            self._session.execute(
                select(
                    AdvisorkhojDistributionRecord.content_signature,
                    AdvisorkhojDistributionRecord.id,
                )
            )
            .tuples()
            .all()
        )
        inserted = 0
        pending = 0
        for source, capture in zip(captures, stored_captures, strict=True):
            for row in source.rows:
                signature = _signature(
                    "row",
                    source.amc_name,
                    source.requested_scheme_name,
                    row.record_date.isoformat(),
                    row.raw_amount_per_unit_inr,
                    row.raw_reference_nav,
                    row.raw_yield_percent,
                )
                record_id = existing_by_signature.get(signature)
                if record_id is None:
                    record_id = new_id()
                    self._session.add(
                        AdvisorkhojDistributionRecord(
                            id=record_id,
                            scheme_capture_id=capture.id,
                            record_date=row.record_date,
                            raw_amount_per_unit_inr=row.raw_amount_per_unit_inr,
                            amount_per_unit_inr=row.amount_per_unit_inr,
                            raw_reference_nav=row.raw_reference_nav,
                            reference_nav=row.reference_nav,
                            raw_yield_percent=row.raw_yield_percent,
                            yield_percent=row.yield_percent,
                            is_positive_cash_distribution=row.is_positive_cash_distribution,
                            quality_status=(
                                "zero_reference_nav"
                                if row.reference_nav == 0
                                else (
                                    "valid"
                                    if row.amount_per_unit_inr > 0
                                    else (
                                        "zero_amount"
                                        if row.amount_per_unit_inr == 0
                                        else "negative_amount"
                                    )
                                )
                            ),
                            content_signature=signature,
                            first_observed_batch_id=batch.id,
                        )
                    )
                    existing_by_signature[signature] = record_id
                    inserted += 1
                self._session.add(
                    AdvisorkhojDistributionRecordSource(
                        advisorkhoj_distribution_record_id=record_id,
                        ingestion_batch_id=batch.id,
                    )
                )
                pending += 1
                if pending >= 5000:
                    self._session.commit()
                    pending = 0
        self._session.commit()
        return inserted

    def _record_full_mapping(
        self,
        source: AdvisorkhojSchemeCapture,
        capture: AdvisorkhojSchemeCaptureRecord,
    ) -> tuple[str, str | None]:
        metadata_rows = self._metadata_candidates(source)
        source_core = _scheme_core(source.source_scheme_name)
        exact_codes = {
            item.amfi_scheme_code
            for item in metadata_rows
            if _scheme_core(item.scheme_name) == source_core
        }
        candidate_codes = exact_codes or {item.amfi_scheme_code for item in metadata_rows}
        sampled_rows = _sample_rows(source)
        nav_rows = (
            tuple(
                self._session.scalars(
                    select(NavRevisionRecord).where(
                        NavRevisionRecord.amfi_scheme_code.in_(tuple(candidate_codes)),
                        NavRevisionRecord.nav_date.in_(
                            tuple(row.record_date for row in sampled_rows)
                        ),
                        NavRevisionRecord.is_current.is_(True),
                        NavRevisionRecord.quality_status == "valid",
                    )
                ).all()
            )
            if candidate_codes and sampled_rows
            else ()
        )
        nav_by_code: dict[str, dict[date, Decimal]] = {}
        for nav in nav_rows:
            nav_by_code.setdefault(nav.amfi_scheme_code, {})[nav.nav_date] = nav.nav_value
        candidates: list[dict[str, object]] = []
        qualifying_codes: list[str] = []
        for code in sorted(candidate_codes):
            matches: list[date] = []
            conflicts: list[dict[str, str]] = []
            candidate_nav = nav_by_code.get(code, {})
            for row in sampled_rows:
                amfi_nav = candidate_nav.get(row.record_date)
                if amfi_nav is None:
                    continue
                if amfi_nav == row.reference_nav:
                    matches.append(row.record_date)
                else:
                    conflicts.append(
                        {
                            "date": row.record_date.isoformat(),
                            "source": str(row.reference_nav),
                            "amfi": str(amfi_nav),
                        }
                    )
            span = (max(matches) - min(matches)).days if len(matches) >= 2 else 0
            exact_name = code in exact_codes
            qualifies = not conflicts and (
                (len(matches) >= _MINIMUM_NAV_MATCHES and span >= _MINIMUM_EVIDENCE_SPAN_DAYS)
                or (exact_name and len(matches) >= 1)
            )
            if qualifies:
                qualifying_codes.append(code)
            candidates.append(
                {
                    "amfi_scheme_code": code,
                    "exact_name_core": exact_name,
                    "matches": [item.isoformat() for item in matches],
                    "conflicts": conflicts,
                    "evidence_span_days": span,
                    "qualifying": qualifies,
                }
            )
        if len(qualifying_codes) == 1:
            status = "mapped"
            scheme_code: str | None = qualifying_codes[0]
            mapping_method = "nav_fingerprint"
        elif qualifying_codes:
            status = "ambiguous"
            scheme_code = None
            mapping_method = "none"
        else:
            status = "unresolved"
            scheme_code = None
            mapping_method = "none"
        evidence = json.dumps(
            {
                "policy": (
                    "unique AMC/plan/explicit-option-qualifier IDCW candidate with either 3 "
                    "exact NAV matches over 30 days or exact normalized name plus at least 1 "
                    "exact NAV; any comparable NAV conflict blocks the candidate"
                ),
                "source_amc": source.amc_name,
                "source_scheme_name": source.source_scheme_name,
                "source_scheme_core": source_core,
                "source_plan_type": source.plan_type,
                "source_rows": len(source.rows),
                "sampled_nav_points": len(sampled_rows),
                "candidate_codes": sorted(candidate_codes),
                "qualifying_codes": qualifying_codes,
                "candidates": candidates,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        signature = _signature(
            "mapping",
            capture.id,
            status,
            scheme_code or "",
            mapping_method,
            evidence,
        )
        review = self._session.scalar(
            select(AdvisorkhojSchemeMappingReviewRecord).where(
                AdvisorkhojSchemeMappingReviewRecord.review_signature == signature
            )
        )
        if review is None:
            self._session.add(
                AdvisorkhojSchemeMappingReviewRecord(
                    scheme_capture_id=capture.id,
                    status=status,
                    amfi_scheme_code=scheme_code,
                    mapping_method=mapping_method,
                    evidence_details=evidence,
                    review_signature=signature,
                )
            )
            self._session.commit()
        return status, scheme_code

    def _metadata_candidates(
        self, source: AdvisorkhojSchemeCapture
    ) -> tuple[SchemeMetadataVersionRecord, ...]:
        if self._metadata_by_amc is None:
            by_amc: dict[str, list[SchemeMetadataVersionRecord]] = {}
            for metadata in self._session.scalars(
                select(SchemeMetadataVersionRecord).where(
                    SchemeMetadataVersionRecord.option_type == "idcw"
                )
            ):
                by_amc.setdefault(_amc_core(metadata.fund_house_name), []).append(metadata)
            self._metadata_by_amc = {amc: tuple(rows) for amc, rows in by_amc.items()}
        rows = self._metadata_by_amc.get(_amc_core(source.amc_name), ())
        return tuple(
            item
            for item in rows
            if _plan_compatible(source.plan_type, item.plan_type)
            and _option_qualifiers_compatible(source, item.scheme_name)
        )

    def refresh_mappings(self, capture_file: Path) -> AdvisorkhojMappingRefreshResult:
        """Re-run identity evidence without ingesting or duplicating source observations."""

        captures = AdvisorkhojCaptureParser().parse_records(capture_file.read_bytes())
        stored_by_identity = {
            (catalog.amc_name, catalog.scheme_name, stored.source_payload_sha256): stored
            for stored, catalog in self._session.execute(
                select(
                    AdvisorkhojSchemeCaptureRecord,
                    AdvisorkhojCatalogSchemeRecord,
                ).join(
                    AdvisorkhojCatalogSchemeRecord,
                    AdvisorkhojCatalogSchemeRecord.id
                    == AdvisorkhojSchemeCaptureRecord.catalog_scheme_id,
                )
            )
        }
        stored_captures: list[AdvisorkhojSchemeCaptureRecord] = []
        missing: list[tuple[str, str]] = []
        for source in captures:
            stored = stored_by_identity.get(
                (source.amc_name, source.requested_scheme_name, source.source_payload_sha256)
            )
            if stored is None:
                missing.append((source.amc_name, source.requested_scheme_name))
            else:
                stored_captures.append(stored)
        if missing:
            raise ValueError(
                "AdvisorKhoj mapping refresh requires an already imported capture: "
                f"missing={len(missing)}, first_missing={missing[:3]!r}"
            )
        conclusions = tuple(
            self._record_full_mapping(source, stored)
            for source, stored in zip(captures, stored_captures, strict=True)
        )
        return AdvisorkhojMappingRefreshResult(
            captures_reviewed=len(captures),
            mapped_captures=sum(item[0] == "mapped" for item in conclusions),
            unresolved_captures=sum(item[0] == "unresolved" for item in conclusions),
            ambiguous_captures=sum(item[0] == "ambiguous" for item in conclusions),
            canonical_events_published=0,
        )

    def import_file(self, capture_file: Path) -> AdvisorkhojPilotImportResult:
        payload = capture_file.read_bytes()
        batch = self._start_capture_batch(capture_file)
        try:
            stored = self._artifacts.store(payload)
            attachment = self._repository.attach_artifact(
                batch,
                stored,
                media_type="application/x-ndjson; charset=utf-8",
                http_status=200,
                final_url=_BASE_URL,
            )
            captures = AdvisorkhojCaptureParser().parse_records(payload)
            assessments = tuple(self._assess_mapping(capture) for capture in captures)
            source_rows = sum(len(capture.rows) for capture in captures)
            positive_source_rows = sum(
                row.is_positive_cash_distribution for capture in captures for row in capture.rows
            )
            zero_source_rows = source_rows - positive_source_rows
            self._repository.complete_batch(batch, source_rows)
        except (Exception, KeyboardInterrupt) as error:
            self._repository.fail_batch(batch, f"{type(error).__name__}: {error!s}")
            raise

        max_observed_at = self._session.scalar(
            select(NavRevisionRecord.observed_at)
            .order_by(NavRevisionRecord.observed_at.desc())
            .limit(1)
        )
        result = AdvisorkhojPilotImportResult(
            ingestion_batch_id=batch.id,
            artifact_sha256=attachment.artifact.sha256,
            artifact_reused=attachment.reused,
            parser_version=PARSER_VERSION,
            mapping_policy=(
                "exact normalized scheme name + compatible explicit plan + at least "
                f"{_MINIMUM_NAV_MATCHES} conflict-free exact AMFI NAV matches spanning at "
                f"least {_MINIMUM_EVIDENCE_SPAN_DAYS} days; secondary rows are not published"
            ),
            amfi_nav_max_observed_at=(
                max_observed_at.isoformat() if isinstance(max_observed_at, datetime) else None
            ),
            captures_received=len(captures),
            source_rows_received=source_rows,
            positive_source_rows=positive_source_rows,
            zero_source_rows=zero_source_rows,
            mapped_captures=sum(item.status == "mapped" for item in assessments),
            unresolved_captures=sum(item.status == "unresolved" for item in assessments),
            ambiguous_captures=sum(item.status == "ambiguous" for item in assessments),
            canonical_events_published=0,
            assessments=assessments,
        )
        logger.info(
            "advisorkhoj_pilot_imported",
            extra={
                "event_data": {
                    "ingestion_batch_id": result.ingestion_batch_id,
                    "artifact_sha256": result.artifact_sha256,
                    "captures_received": result.captures_received,
                    "source_rows_received": result.source_rows_received,
                    "positive_source_rows": result.positive_source_rows,
                    "zero_source_rows": result.zero_source_rows,
                    "mapped_captures": result.mapped_captures,
                    "unresolved_captures": result.unresolved_captures,
                    "ambiguous_captures": result.ambiguous_captures,
                    "canonical_events_published": result.canonical_events_published,
                }
            },
        )
        return result

    def _start_capture_batch(self, capture_file: Path) -> IngestionBatchRecord:
        return self._repository.start_batch(
            provider="advisorkhoj",
            source_type="secondary_distribution_capture",
            source_url=_BASE_URL,
            request_parameters={
                "capture_format": "advisorkhoj-jsonl-v1",
                "capture_file_name": capture_file.name,
                "publication_policy": "mapping_report_only",
            },
            parser_version=PARSER_VERSION,
        )

    def _assess_mapping(self, capture: AdvisorkhojSchemeCapture) -> AdvisorkhojMappingAssessment:
        source_core = _scheme_core(capture.display_scheme_name)
        metadata_rows = tuple(
            self._session.scalars(
                select(SchemeMetadataVersionRecord).where(
                    SchemeMetadataVersionRecord.fund_house_name == capture.amc_name,
                    SchemeMetadataVersionRecord.option_type == "idcw",
                )
            ).all()
        )
        candidate_metadata: dict[str, list[SchemeMetadataVersionRecord]] = {}
        for metadata in metadata_rows:
            if _scheme_core(metadata.scheme_name) != source_core:
                continue
            if not _plan_compatible(capture.plan_type, metadata.plan_type):
                continue
            candidate_metadata.setdefault(metadata.amfi_scheme_code, []).append(metadata)

        source_nav_by_date = {row.record_date: row.reference_nav for row in capture.rows}
        candidate_codes = tuple(sorted(candidate_metadata))
        nav_rows = (
            tuple(
                self._session.scalars(
                    select(NavRevisionRecord).where(
                        NavRevisionRecord.amfi_scheme_code.in_(candidate_codes),
                        NavRevisionRecord.nav_date.in_(tuple(source_nav_by_date)),
                        NavRevisionRecord.is_current.is_(True),
                        NavRevisionRecord.quality_status == "valid",
                    )
                ).all()
            )
            if candidate_codes and source_nav_by_date
            else ()
        )
        nav_by_candidate: dict[str, dict[date, Decimal]] = {}
        for nav in nav_rows:
            nav_by_candidate.setdefault(nav.amfi_scheme_code, {})[nav.nav_date] = nav.nav_value

        candidates = tuple(
            self._candidate_assessment(
                scheme_code,
                candidate_metadata[scheme_code],
                source_nav_by_date,
                nav_by_candidate.get(scheme_code, {}),
            )
            for scheme_code in candidate_codes
        )
        qualifying = tuple(item for item in candidates if item.qualifying)
        if capture.frequency_conflict:
            status = "unresolved"
            amfi_scheme_code = None
            conclusion = (
                "source short name and rendered display name disagree on distribution "
                f"frequency: {capture.source_frequency} != {capture.display_frequency}"
            )
            mapping_method = "none"
        elif len(qualifying) == 1:
            status = "mapped"
            amfi_scheme_code = qualifying[0].amfi_scheme_code
            conclusion = (
                f"one candidate passed the name/plan gate and NAV fingerprint policy: "
                f"{amfi_scheme_code}"
            )
            mapping_method = "exact_name_plan_nav_fingerprint"
        elif len(qualifying) > 1:
            status = "ambiguous"
            amfi_scheme_code = None
            conclusion = "multiple AMFI codes passed the NAV fingerprint policy: " + ", ".join(
                item.amfi_scheme_code for item in qualifying
            )
            mapping_method = "none"
        else:
            status = "unresolved"
            amfi_scheme_code = None
            if not candidate_codes:
                conclusion = "no AMFI IDCW metadata matched the normalized name and explicit plan"
            elif len(capture.rows) < _MINIMUM_NAV_MATCHES:
                conclusion = (
                    f"source exposes only {len(capture.rows)} reference NAV point(s); "
                    f"at least {_MINIMUM_NAV_MATCHES} are required"
                )
            else:
                conclusion = "no name-compatible candidate passed the NAV fingerprint policy"
            mapping_method = "none"
        return AdvisorkhojMappingAssessment(
            requested_scheme_name=capture.requested_scheme_name,
            display_scheme_name=capture.display_scheme_name,
            source_url=capture.source_url,
            source_payload_sha256=capture.source_payload_sha256,
            plan_type=capture.plan_type,
            option_variant=capture.option_variant,
            source_frequency=capture.source_frequency,
            display_frequency=capture.display_frequency,
            frequency_conflict=capture.frequency_conflict,
            source_rows=len(capture.rows),
            positive_source_rows=sum(row.is_positive_cash_distribution for row in capture.rows),
            zero_source_rows=sum(not row.is_positive_cash_distribution for row in capture.rows),
            source_name_core=source_core,
            status=status,
            amfi_scheme_code=amfi_scheme_code,
            mapping_method=mapping_method,
            conclusion=conclusion,
            candidates=candidates,
        )

    @staticmethod
    def _candidate_assessment(
        scheme_code: str,
        metadata_rows: list[SchemeMetadataVersionRecord],
        source_nav_by_date: dict[date, Decimal],
        amfi_nav_by_date: dict[date, Decimal],
    ) -> AdvisorkhojMappingCandidate:
        matches: list[AdvisorkhojNavEvidence] = []
        conflicts: list[AdvisorkhojNavConflict] = []
        for nav_date, source_nav in sorted(source_nav_by_date.items()):
            amfi_nav = amfi_nav_by_date.get(nav_date)
            if amfi_nav is None:
                continue
            if amfi_nav == source_nav:
                matches.append(
                    AdvisorkhojNavEvidence(nav_date=nav_date.isoformat(), nav_value=str(source_nav))
                )
            else:
                conflicts.append(
                    AdvisorkhojNavConflict(
                        nav_date=nav_date.isoformat(),
                        source_nav_value=str(source_nav),
                        amfi_nav_value=str(amfi_nav),
                    )
                )
        evidence_span_days = 0
        if len(matches) >= 2:
            evidence_span_days = (
                date.fromisoformat(matches[-1].nav_date) - date.fromisoformat(matches[0].nav_date)
            ).days
        comparable = len(matches) + len(conflicts)
        qualifying = (
            len(matches) >= _MINIMUM_NAV_MATCHES
            and evidence_span_days >= _MINIMUM_EVIDENCE_SPAN_DAYS
            and not conflicts
        )
        return AdvisorkhojMappingCandidate(
            amfi_scheme_code=scheme_code,
            amfi_scheme_names=tuple(sorted({item.scheme_name for item in metadata_rows})),
            amfi_plan_types=tuple(sorted({item.plan_type for item in metadata_rows})),
            comparable_nav_points=comparable,
            exact_nav_matches=len(matches),
            nav_conflicts=len(conflicts),
            missing_amfi_nav_points=len(source_nav_by_date) - comparable,
            evidence_span_days=evidence_span_days,
            qualifying=qualifying,
            matched_nav_evidence=tuple(matches),
            conflict_evidence=tuple(conflicts),
        )


def _plan_compatible(source_plan: str, amfi_plan: str) -> bool:
    return source_plan == "unknown" or amfi_plan == "unknown" or source_plan == amfi_plan


def _option_qualifiers_compatible(source: AdvisorkhojSchemeCapture, candidate_name: str) -> bool:
    candidate_variant = _name_option_variant(candidate_name)
    if (
        source.option_variant not in {"unknown", "mixed"}
        and candidate_variant not in {"unknown", "mixed"}
        and source.option_variant != candidate_variant
    ):
        return False
    candidate_frequency = _name_frequency(candidate_name)
    return not (
        source.source_frequency not in {"unknown", "multiple"}
        and candidate_frequency not in {"unknown", "multiple"}
        and source.source_frequency != candidate_frequency
    )


def _name_option_variant(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    reinvestment = any(token.startswith("reinvest") or token == "reinv" for token in tokens)
    payout = any(token in {"pay", "payment", "payout"} for token in tokens)
    if reinvestment and payout:
        return "mixed"
    if reinvestment:
        return "reinvestment"
    if payout:
        return "payout"
    return "unknown"


def _name_frequency(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    joined = " ".join(tokens)
    mapping = {
        "annual": "annual",
        "annually": "annual",
        "daily": "daily",
        "fortn": "fortnightly",
        "fortnightly": "fortnightly",
        "hly": "half_yearly",
        "monthly": "monthly",
        "mly": "monthly",
        "qly": "quarterly",
        "quarterly": "quarterly",
        "weekly": "weekly",
        "wly": "weekly",
    }
    frequencies = {mapping[token] for token in tokens if token in mapping}
    if "half yearly" in joined or "halfyearly" in joined:
        frequencies.add("half_yearly")
    if len(frequencies) > 1:
        return "multiple"
    return next(iter(frequencies), "unknown")


def _scheme_core(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    expanded = (_TOKEN_EXPANSIONS.get(token, token) for token in tokens)
    return " ".join(token for token in expanded if token not in _NON_IDENTITY_WORDS)


def _amc_core(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    return " ".join(token for token in tokens if token not in {"mutual", "fund"})


def _sample_rows(
    capture: AdvisorkhojSchemeCapture, maximum_points: int = 12
) -> tuple[AdvisorkhojDistributionRow, ...]:
    rows = tuple(
        row
        for row in capture.rows
        if row.reference_nav > 0 and row.record_date <= capture.captured_at.date()
    )
    if len(rows) <= maximum_points:
        return rows
    indexes = {
        round(index * (len(rows) - 1) / (maximum_points - 1)) for index in range(maximum_points)
    }
    return tuple(rows[index] for index in sorted(indexes))


def _signature(*values: str) -> str:
    encoded = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_revision_signature(
    scheme_code: str, record_date: date, amount: Decimal, revision_number: int
) -> str:
    return _signature(
        scheme_code,
        record_date.isoformat(),
        _EVENT_TYPE,
        str(amount),
        str(revision_number),
    )
