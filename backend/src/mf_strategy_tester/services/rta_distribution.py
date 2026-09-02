from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionNormalizationRunRecord,
    IngestionBatchRecord,
    NavRevisionRecord,
    RtaDistributionIssueRecord,
    RtaDistributionRecord,
    RtaDistributionRecordSource,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.ingestion.artifacts import ArtifactStore
from mf_strategy_tester.ingestion.rta import (
    PARSER_VERSION,
    RtaCaptureParser,
    RtaDistributionRow,
    RtaSchemeCapture,
)
from mf_strategy_tester.repositories.ingestion import IngestionRepository
from mf_strategy_tester.services.distribution_precedence import (
    DistributionSourceTier,
    canonical_revision_source_tier,
    event_highest_source_tier,
)

logger = logging.getLogger(__name__)

RTA_NORMALIZATION_VERSION = "rta-distribution-2026.08.6"
_EVENT_TYPE = "idcw_cash"
_EARLIEST_PLAUSIBLE_RECORD_DATE = date(1964, 1, 1)
_BASE_URLS = {
    "cams": "https://www.camsonline.com/InvestorServices/COL_ISNAV.aspx",
    "kfintech": "https://mfs.kfintech.com/mfs/InvestorServices/NAVDividend/NAV_Dividend.aspx",
}
_NON_IDENTITY_WORDS = frozenset(
    {
        "direct",
        "regular",
        "plan",
        "option",
        "idcw",
        "dividend",
        "payout",
        "reinvestment",
        "reinvest",
        "reinv",
        "reinve",
        "exchange",
        "exch",
        "ex",
        "re",
        "rein",
        "dir",
        "reg",
        "pl",
        "and",
        "the",
        "income",
        "distribution",
        "cum",
        "capital",
        "withdrawal",
    }
)


@dataclass(frozen=True)
class RtaDistributionImportResult:
    ingestion_batch_id: str
    normalization_run_id: str
    provider: str
    captures_received: int
    captures_inserted: int
    source_rows_received: int
    source_rows_inserted: int
    mapped_captures: int
    unresolved_captures: int
    ambiguous_captures: int
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int
    rows_blocked: int
    amount_conflicts: int


@dataclass(frozen=True)
class RtaDistributionResumeResult:
    normalization_run_id: str
    provider: str
    captures_received: int
    source_rows_received: int
    mapped_captures: int
    unresolved_captures: int
    ambiguous_captures: int
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int
    rows_blocked: int
    amount_conflicts: int


@dataclass(frozen=True)
class _MappingConclusion:
    status: str
    amfi_scheme_code: str | None
    mapping_method: str
    evidence_details: str
    review_id: str | None = None


@dataclass(frozen=True)
class _PublicationResult:
    run_id: str
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int
    rows_blocked: int
    amount_conflicts: int


class RtaDistributionImportService:
    """Import auditable RTA captures and publish only evidence-mapped cash payouts."""

    def __init__(
        self,
        session: Session,
        repository: IngestionRepository,
        artifacts: ArtifactStore,
    ) -> None:
        self._session = session
        self._repository = repository
        self._artifacts = artifacts
        self._idcw_codes_by_core: dict[str, frozenset[str]] | None = None
        self._idcw_codes_by_core_plan: dict[tuple[str, str], frozenset[str]] | None = None

    def import_file(self, capture_file: Path) -> RtaDistributionImportResult:
        payload = capture_file.read_bytes()
        captures = RtaCaptureParser().parse_records(payload)
        provider = captures[0].provider
        batch = self._capture_batch(provider, payload, len(captures))
        try:
            stored_captures, captures_inserted = self._persist_captures(captures, batch)
            rows, rows_inserted = self._persist_rows(captures, stored_captures, batch)
            conclusions = self._record_mappings(captures, stored_captures, provider)
            publication = self._publish(rows, stored_captures, conclusions)
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            self._repository.fail_batch(batch, f"{type(error).__name__}: {error!s}")
            raise

        result = RtaDistributionImportResult(
            ingestion_batch_id=batch.id,
            normalization_run_id=publication.run_id,
            provider=provider,
            captures_received=len(captures),
            captures_inserted=captures_inserted,
            source_rows_received=len(rows),
            source_rows_inserted=rows_inserted,
            mapped_captures=sum(item.status == "mapped" for item in conclusions),
            unresolved_captures=sum(item.status == "unresolved" for item in conclusions),
            ambiguous_captures=sum(item.status == "ambiguous" for item in conclusions),
            events_inserted=publication.events_inserted,
            revisions_inserted=publication.revisions_inserted,
            rows_unchanged=publication.rows_unchanged,
            rows_blocked=publication.rows_blocked,
            amount_conflicts=publication.amount_conflicts,
        )
        logger.info("rta_distributions_imported", extra={"event_data": asdict(result)})
        return result

    def resume_file(self, capture_file: Path) -> RtaDistributionResumeResult:
        """Resume mapping/publication after source captures and rows were committed."""
        captures = RtaCaptureParser().parse_records(capture_file.read_bytes())
        provider = captures[0].provider
        signatures = tuple(_capture_signature(capture) for capture in captures)
        stored_by_signature = {
            capture.capture_signature: capture
            for capture in self._session.scalars(
                select(RtaSchemeCaptureRecord).where(
                    RtaSchemeCaptureRecord.capture_signature.in_(signatures)
                )
            )
        }
        missing_captures = [
            (capture.rta_fund_code, capture.rta_scheme_code)
            for capture, signature in zip(captures, signatures, strict=True)
            if signature not in stored_by_signature
        ]
        if missing_captures:
            raise ValueError(
                "RTA resume requires every capture to be imported first: "
                f"missing={len(missing_captures)}, first_missing={missing_captures[:3]!r}"
            )
        stored_captures = tuple(stored_by_signature[signature] for signature in signatures)
        stored_rows_by_signature = {
            row.content_signature: row
            for row in self._session.scalars(
                select(RtaDistributionRecord).where(
                    RtaDistributionRecord.scheme_capture_id.in_(
                        tuple(capture.id for capture in stored_captures)
                    )
                )
            )
        }
        rows: list[RtaDistributionRecord] = []
        missing_rows: list[tuple[str, str, str]] = []
        for source, stored_capture in zip(captures, stored_captures, strict=True):
            for source_row in source.rows:
                signature = _row_signature(source, source_row)
                stored_row = stored_rows_by_signature.get(signature)
                if stored_row is None or stored_row.scheme_capture_id != stored_capture.id:
                    missing_rows.append(
                        (
                            source.rta_fund_code,
                            source.rta_scheme_code,
                            source_row.record_date.isoformat(),
                        )
                    )
                else:
                    rows.append(stored_row)
        if missing_rows:
            raise ValueError(
                "RTA resume requires every source row to be imported first: "
                f"missing={len(missing_rows)}, first_missing={missing_rows[:3]!r}"
            )
        conclusions = self._record_mappings(captures, stored_captures, provider)
        publication = self._publish(tuple(rows), stored_captures, conclusions)
        result = RtaDistributionResumeResult(
            normalization_run_id=publication.run_id,
            provider=provider,
            captures_received=len(captures),
            source_rows_received=len(rows),
            mapped_captures=sum(item.status == "mapped" for item in conclusions),
            unresolved_captures=sum(item.status == "unresolved" for item in conclusions),
            ambiguous_captures=sum(item.status == "ambiguous" for item in conclusions),
            events_inserted=publication.events_inserted,
            revisions_inserted=publication.revisions_inserted,
            rows_unchanged=publication.rows_unchanged,
            rows_blocked=publication.rows_blocked,
            amount_conflicts=publication.amount_conflicts,
        )
        logger.info("rta_distribution_import_resumed", extra={"event_data": asdict(result)})
        return result

    def _record_mappings(
        self,
        captures: tuple[RtaSchemeCapture, ...],
        stored_captures: tuple[RtaSchemeCaptureRecord, ...],
        provider: str,
    ) -> tuple[_MappingConclusion, ...]:
        conclusions: list[_MappingConclusion] = []
        for index, (capture, stored_capture) in enumerate(
            zip(captures, stored_captures, strict=True), start=1
        ):
            latest = self._latest_mapping(stored_capture.id)
            conclusions.append(
                latest
                if latest.mapping_method == "manual"
                else self._record_mapping(capture, stored_capture)
            )
            if index % 25 == 0 or index == len(captures):
                logger.info(
                    "rta_mapping_progress",
                    extra={
                        "event_data": {
                            "provider": provider,
                            "captures_completed": index,
                            "captures_total": len(captures),
                        }
                    },
                )
        return tuple(conclusions)

    def record_manual_mapping(
        self,
        *,
        scheme_capture_id: str,
        amfi_scheme_code: str,
        evidence_details: str,
    ) -> RtaSchemeMappingReviewRecord:
        capture = self._session.get(RtaSchemeCaptureRecord, scheme_capture_id)
        if capture is None:
            raise LookupError(f"RTA scheme capture {scheme_capture_id} does not exist")
        option = self._session.get(SchemeOptionRecord, amfi_scheme_code)
        if option is None:
            raise LookupError(f"AMFI scheme code {amfi_scheme_code} does not exist")
        normalized_details = evidence_details.strip()
        if not normalized_details:
            raise ValueError("manual mapping evidence details cannot be empty")
        self._validate_manual_option(capture, amfi_scheme_code)
        conclusion = _MappingConclusion(
            status="mapped",
            amfi_scheme_code=amfi_scheme_code,
            mapping_method="manual",
            evidence_details=normalized_details,
        )
        review = self._persist_mapping_review(capture, conclusion)
        self._session.commit()
        return review

    def publish_pending(self) -> _PublicationResult:
        captures = tuple(
            self._session.scalars(
                select(RtaSchemeCaptureRecord).order_by(RtaSchemeCaptureRecord.imported_at)
            ).all()
        )
        latest = tuple(self._latest_mapping(capture.id) for capture in captures)
        rows = tuple(
            self._session.scalars(
                select(RtaDistributionRecord).order_by(
                    RtaDistributionRecord.observed_at, RtaDistributionRecord.id
                )
            ).all()
        )
        return self._publish(rows, captures, latest)

    def _capture_batch(
        self, provider: str, payload: bytes, rows_received: int
    ) -> IngestionBatchRecord:
        batch = self._repository.start_batch(
            provider=provider,
            source_type="rta_distribution_capture",
            source_url=_BASE_URLS[provider],
            request_parameters={"capture_format": "jsonl-v1"},
            parser_version=PARSER_VERSION,
        )
        try:
            stored = self._artifacts.store(payload)
            self._repository.attach_artifact(
                batch,
                stored,
                media_type="application/x-ndjson; charset=utf-8",
                http_status=200,
                final_url=_BASE_URLS[provider],
            )
            self._repository.complete_batch(batch, rows_received)
        except (Exception, KeyboardInterrupt) as error:
            self._repository.fail_batch(batch, f"{type(error).__name__}: {error!s}")
            raise
        return batch

    def _persist_captures(
        self,
        captures: tuple[RtaSchemeCapture, ...],
        batch: IngestionBatchRecord,
    ) -> tuple[tuple[RtaSchemeCaptureRecord, ...], int]:
        records: list[RtaSchemeCaptureRecord] = []
        inserted = 0
        for capture in captures:
            signature = _capture_signature(capture)
            record = self._session.scalar(
                select(RtaSchemeCaptureRecord).where(
                    RtaSchemeCaptureRecord.capture_signature == signature
                )
            )
            if record is None:
                record = RtaSchemeCaptureRecord(
                    provider=capture.provider,
                    rta_fund_code=capture.rta_fund_code,
                    rta_fund_name=capture.rta_fund_name,
                    rta_scheme_code=capture.rta_scheme_code,
                    source_scheme_name=capture.source_scheme_name,
                    source_url=capture.source_url,
                    source_payload_sha256=capture.source_payload_sha256,
                    plan_type=capture.plan_type,
                    option_variant=capture.option_variant,
                    latest_nav_date=capture.latest_nav_date,
                    latest_nav_value=capture.latest_nav_value,
                    source_row_count=len(capture.rows),
                    capture_signature=signature,
                    ingestion_batch_id=batch.id,
                    captured_at=capture.captured_at,
                )
                self._session.add(record)
                self._session.flush()
                inserted += 1
            records.append(record)
        self._session.commit()
        return tuple(records), inserted

    def _persist_rows(
        self,
        captures: tuple[RtaSchemeCapture, ...],
        stored_captures: tuple[RtaSchemeCaptureRecord, ...],
        batch: IngestionBatchRecord,
    ) -> tuple[tuple[RtaDistributionRecord, ...], int]:
        records: list[RtaDistributionRecord] = []
        inserted = 0
        examined = 0
        total = sum(len(capture.rows) for capture in captures)
        for capture, stored_capture in zip(captures, stored_captures, strict=True):
            for row in capture.rows:
                examined += 1
                signature = _row_signature(capture, row)
                record = self._session.scalar(
                    select(RtaDistributionRecord).where(
                        RtaDistributionRecord.content_signature == signature
                    )
                )
                if record is None:
                    record = RtaDistributionRecord(
                        scheme_capture_id=stored_capture.id,
                        record_date=row.record_date,
                        raw_individual_amount=row.raw_individual_amount,
                        individual_amount_per_unit_inr=row.individual_amount_per_unit_inr,
                        raw_non_individual_amount=row.raw_non_individual_amount,
                        non_individual_amount_per_unit_inr=(row.non_individual_amount_per_unit_inr),
                        ex_nav=row.ex_nav,
                        cum_nav=row.cum_nav,
                        source_unit="inr_per_unit",
                        source_terminology=row.source_terminology,
                        content_signature=signature,
                        first_observed_batch_id=batch.id,
                    )
                    self._session.add(record)
                    self._session.flush()
                    inserted += 1
                if self._session.get(RtaDistributionRecordSource, (record.id, batch.id)) is None:
                    self._session.add(
                        RtaDistributionRecordSource(
                            rta_distribution_record_id=record.id,
                            ingestion_batch_id=batch.id,
                        )
                    )
                records.append(record)
                if examined % 10_000 == 0 or examined == total:
                    logger.info(
                        "rta_distribution_row_progress",
                        extra={
                            "event_data": {
                                "rows_completed": examined,
                                "rows_total": total,
                                "rows_inserted": inserted,
                            }
                        },
                    )
        self._session.commit()
        return tuple(records), inserted

    def _record_mapping(
        self, source: RtaSchemeCapture, capture: RtaSchemeCaptureRecord
    ) -> _MappingConclusion:
        evidence = _nav_evidence(source)
        evidence_by_point: dict[tuple[date, Decimal], set[str]] = {}
        for nav_date, nav_value, evidence_type in evidence:
            evidence_by_point.setdefault((nav_date, nav_value), set()).add(evidence_type)
        metadata_candidate_codes = self._metadata_candidate_codes(source)
        candidate_codes: set[str] = set()
        matched_evidence: list[dict[str, str]] = []
        rows = (
            self._session.execute(
                select(NavRevisionRecord, SchemeMetadataVersionRecord)
                .join(
                    SchemeMetadataVersionRecord,
                    SchemeMetadataVersionRecord.id == NavRevisionRecord.metadata_version_id,
                )
                .where(
                    NavRevisionRecord.amfi_scheme_code.in_(metadata_candidate_codes),
                    NavRevisionRecord.is_current.is_(True),
                    NavRevisionRecord.quality_status == "valid",
                )
            ).all()
            if metadata_candidate_codes and evidence_by_point
            else ()
        )
        source_core = _scheme_core(source.source_scheme_name)
        for nav, metadata in rows:
            evidence_types = evidence_by_point.get((nav.nav_date, nav.nav_value))
            if not evidence_types:
                continue
            if source.plan_type != "unknown" and metadata.plan_type != source.plan_type:
                continue
            if _scheme_core(metadata.scheme_name) != source_core:
                continue
            candidate_codes.add(nav.amfi_scheme_code)
            for evidence_type in sorted(evidence_types):
                matched_evidence.append(
                    {
                        "amfi_scheme_code": nav.amfi_scheme_code,
                        "evidence_type": evidence_type,
                        "nav_date": nav.nav_date.isoformat(),
                        "nav_value": str(nav.nav_value),
                    }
                )
        matched_evidence.sort(
            key=lambda item: (
                item["amfi_scheme_code"],
                item["nav_date"],
                item["nav_value"],
                item["evidence_type"],
            )
        )
        details = json.dumps(
            {
                "policy": "exact normalized scheme core + plan + exact current NAV evidence",
                "source_scheme_core": source_core,
                "source_plan_type": source.plan_type,
                "evidence_points": len(evidence),
                "metadata_candidate_codes": sorted(metadata_candidate_codes),
                "matches": matched_evidence,
                "candidate_codes": sorted(candidate_codes),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(candidate_codes) == 1:
            conclusion = _MappingConclusion(
                status="mapped",
                amfi_scheme_code=next(iter(candidate_codes)),
                mapping_method="exact_name_plan_nav",
                evidence_details=details,
            )
        elif candidate_codes:
            conclusion = _MappingConclusion(
                status="ambiguous",
                amfi_scheme_code=None,
                mapping_method="none",
                evidence_details=details,
            )
        else:
            conclusion = _MappingConclusion(
                status="unresolved",
                amfi_scheme_code=None,
                mapping_method="none",
                evidence_details=details,
            )
        review = self._persist_mapping_review(capture, conclusion)
        self._session.commit()
        return _MappingConclusion(
            conclusion.status,
            conclusion.amfi_scheme_code,
            conclusion.mapping_method,
            conclusion.evidence_details,
            review.id,
        )

    def _metadata_candidate_codes(self, source: RtaSchemeCapture) -> frozenset[str]:
        if self._idcw_codes_by_core is None or self._idcw_codes_by_core_plan is None:
            by_core: dict[str, set[str]] = {}
            by_core_plan: dict[tuple[str, str], set[str]] = {}
            for scheme_code, scheme_name, plan_type in self._session.execute(
                select(
                    SchemeMetadataVersionRecord.amfi_scheme_code,
                    SchemeMetadataVersionRecord.scheme_name,
                    SchemeMetadataVersionRecord.plan_type,
                ).where(SchemeMetadataVersionRecord.option_type == "idcw")
            ):
                core = _scheme_core(scheme_name)
                by_core.setdefault(core, set()).add(scheme_code)
                by_core_plan.setdefault((core, plan_type), set()).add(scheme_code)
            self._idcw_codes_by_core = {core: frozenset(codes) for core, codes in by_core.items()}
            self._idcw_codes_by_core_plan = {
                key: frozenset(codes) for key, codes in by_core_plan.items()
            }
        source_core = _scheme_core(source.source_scheme_name)
        if source.plan_type == "unknown":
            return self._idcw_codes_by_core.get(source_core, frozenset())
        return self._idcw_codes_by_core_plan.get((source_core, source.plan_type), frozenset())

    def _persist_mapping_review(
        self,
        capture: RtaSchemeCaptureRecord,
        conclusion: _MappingConclusion,
    ) -> RtaSchemeMappingReviewRecord:
        signature = _signature(
            "mapping",
            capture.id,
            conclusion.status,
            conclusion.amfi_scheme_code or "",
            conclusion.mapping_method,
            conclusion.evidence_details,
        )
        existing = self._session.scalar(
            select(RtaSchemeMappingReviewRecord).where(
                RtaSchemeMappingReviewRecord.review_signature == signature
            )
        )
        if existing is not None:
            return existing
        review = RtaSchemeMappingReviewRecord(
            scheme_capture_id=capture.id,
            status=conclusion.status,
            amfi_scheme_code=conclusion.amfi_scheme_code,
            mapping_method=conclusion.mapping_method,
            evidence_details=conclusion.evidence_details,
            review_signature=signature,
        )
        self._session.add(review)
        self._session.flush()
        return review

    def _validate_manual_option(
        self, capture: RtaSchemeCaptureRecord, amfi_scheme_code: str
    ) -> None:
        metadata = self._session.scalar(
            select(SchemeMetadataVersionRecord)
            .join(
                NavRevisionRecord,
                NavRevisionRecord.metadata_version_id == SchemeMetadataVersionRecord.id,
            )
            .where(
                NavRevisionRecord.amfi_scheme_code == amfi_scheme_code,
                NavRevisionRecord.is_current.is_(True),
            )
            .order_by(NavRevisionRecord.nav_date.desc())
            .limit(1)
        )
        if metadata is None or metadata.option_type != "idcw":
            raise ValueError(f"AMFI scheme code {amfi_scheme_code} is not a local IDCW option")
        if capture.plan_type != "unknown" and metadata.plan_type != capture.plan_type:
            raise ValueError(
                f"manual mapping plan conflict: RTA={capture.plan_type}, AMFI={metadata.plan_type}"
            )

    def _latest_mapping(self, capture_id: str) -> _MappingConclusion:
        review = self._session.scalar(
            select(RtaSchemeMappingReviewRecord)
            .where(RtaSchemeMappingReviewRecord.scheme_capture_id == capture_id)
            .order_by(
                RtaSchemeMappingReviewRecord.reviewed_at.desc(),
                RtaSchemeMappingReviewRecord.id.desc(),
            )
            .limit(1)
        )
        if review is None:
            return _MappingConclusion("unresolved", None, "none", "no mapping review", None)
        return _MappingConclusion(
            review.status,
            review.amfi_scheme_code,
            review.mapping_method,
            review.evidence_details,
            review.id,
        )

    def _publish(
        self,
        records: tuple[RtaDistributionRecord, ...],
        captures: tuple[RtaSchemeCaptureRecord, ...],
        mappings: tuple[_MappingConclusion, ...],
    ) -> _PublicationResult:
        run = DistributionNormalizationRunRecord(
            status="running", normalization_version=RTA_NORMALIZATION_VERSION
        )
        self._session.add(run)
        self._session.commit()
        try:
            mapping_by_capture = {
                capture.id: mapping for capture, mapping in zip(captures, mappings, strict=True)
            }
            result = self._publish_records(run, records, mapping_by_capture)
            self._session.commit()
            return result
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed = self._session.get(DistributionNormalizationRunRecord, run.id)
            if failed is not None:
                failed.status = "failed"
                failed.source_rows_examined = len(records)
                failed.candidate_rows = 0
                failed.blocked_rows = len(records)
                failed.completed_at = utc_now()
                failed.error_details = f"{type(error).__name__}: {error!s}"[:4000]
                self._session.commit()
            raise

    def _publish_records(
        self,
        run: DistributionNormalizationRunRecord,
        records: tuple[RtaDistributionRecord, ...],
        mapping_by_capture: dict[str, _MappingConclusion],
    ) -> _PublicationResult:
        linked_source_ids = set(
            self._session.scalars(
                select(DistributionEventRevisionRtaSourceRecord.rta_distribution_record_id)
            ).all()
        )
        events_inserted = revisions_inserted = rows_unchanged = 0
        rows_blocked = amount_conflicts = 0
        now = utc_now()
        for index, source in enumerate(records, start=1):
            if index % 10_000 == 0:
                logger.info(
                    "rta_publication_progress",
                    extra={
                        "event_data": {
                            "source_rows_started": index,
                            "source_rows_total": len(records),
                            "revisions_inserted": revisions_inserted,
                            "rows_blocked": rows_blocked,
                        }
                    },
                )
            if source.id in linked_source_ids:
                rows_unchanged += 1
                continue
            if source.record_date < _EARLIEST_PLAUSIBLE_RECORD_DATE:
                self._record_issue(
                    source,
                    run,
                    "implausible_record_date",
                    "RTA row was retained but not published because its literal record date "
                    f"precedes Indian mutual funds: record_date={source.record_date}",
                )
                rows_blocked += 1
                continue
            mapping = mapping_by_capture.get(source.scheme_capture_id)
            if mapping is None or mapping.status != "mapped":
                status = "unresolved" if mapping is None else mapping.status
                issue_code = "ambiguous_scheme" if status == "ambiguous" else "unmapped_scheme"
                self._record_issue(
                    source,
                    run,
                    issue_code,
                    "RTA row was retained but not published because its scheme identity "
                    f"is {status}",
                )
                rows_blocked += 1
                continue
            scheme_code = mapping.amfi_scheme_code
            if scheme_code is None:
                raise RuntimeError("mapped RTA review lacks an AMFI scheme code")
            if mapping.review_id is None:
                raise RuntimeError("mapped RTA conclusion lacks its audit review ID")
            event = self._session.scalar(
                select(DistributionEventRecord).where(
                    DistributionEventRecord.amfi_scheme_code == scheme_code,
                    DistributionEventRecord.record_date == source.record_date,
                    DistributionEventRecord.event_type == _EVENT_TYPE,
                )
            )
            if event is None:
                event = DistributionEventRecord(
                    id=new_id(),
                    amfi_scheme_code=scheme_code,
                    record_date=source.record_date,
                    event_type=_EVENT_TYPE,
                    created_at=now,
                )
                self._session.add(event)
                self._session.flush()
                events_inserted += 1
            current = self._session.scalar(
                select(DistributionEventRevisionRecord).where(
                    DistributionEventRevisionRecord.distribution_event_id == event.id,
                    DistributionEventRevisionRecord.is_current.is_(True),
                )
            )
            if (
                current is not None
                and current.amount_per_unit_inr != source.individual_amount_per_unit_inr
            ):
                current_tier = canonical_revision_source_tier(self._session, current.id)
                is_parser_revision = (
                    current_tier == DistributionSourceTier.RTA
                    and self._is_same_rta_source_parser_revision(current, source)
                )
                if current_tier <= DistributionSourceTier.RTA and not is_parser_revision:
                    self._record_issue(
                        source,
                        run,
                        "amount_conflict",
                        "RTA individual/retail amount conflicts with an equal- or "
                        "higher-priority canonical value: "
                        f"tier={current_tier.name.lower()}, AMFI code={scheme_code}, "
                        f"record_date={source.record_date}, "
                        f"canonical={current.amount_per_unit_inr}, "
                        f"RTA={source.individual_amount_per_unit_inr}",
                    )
                    if current_tier == DistributionSourceTier.RTA:
                        current.is_current = False
                        self._session.flush()
                    amount_conflicts += 1
                    rows_blocked += 1
                    continue
                if current_tier > DistributionSourceTier.RTA or is_parser_revision:
                    current.is_current = False
                    self._session.flush()
                    current = None
            else:
                is_parser_revision = False
            if current is None:
                historical_tier = event_highest_source_tier(self._session, event.id)
                if (
                    historical_tier is not None
                    and historical_tier <= DistributionSourceTier.RTA
                    and not is_parser_revision
                ):
                    self._record_issue(
                        source,
                        run,
                        "amount_conflict",
                        "RTA row cannot resolve an existing equal-priority source conflict: "
                        f"AMFI code={scheme_code}, record_date={source.record_date}, "
                        f"RTA={source.individual_amount_per_unit_inr}",
                    )
                    amount_conflicts += 1
                    rows_blocked += 1
                    continue
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
                    amount_per_unit_inr=source.individual_amount_per_unit_inr,
                    revision_number=revision_number,
                    content_signature=_revision_signature(
                        scheme_code,
                        source.record_date,
                        source.individual_amount_per_unit_inr,
                        revision_number,
                    ),
                    normalization_version=RTA_NORMALIZATION_VERSION,
                    normalization_run_id=run.id,
                    is_current=True,
                    normalized_at=now,
                )
                self._session.add(current)
                self._session.flush()
                revisions_inserted += 1
            else:
                rows_unchanged += 1
            self._session.add(
                DistributionEventRevisionRtaSourceRecord(
                    distribution_event_revision_id=current.id,
                    rta_distribution_record_id=source.id,
                    mapping_review_id=mapping.review_id,
                    linked_at=now,
                )
            )

        if records:
            logger.info(
                "rta_publication_progress",
                extra={
                    "event_data": {
                        "source_rows_completed": len(records),
                        "source_rows_total": len(records),
                        "revisions_inserted": revisions_inserted,
                        "rows_blocked": rows_blocked,
                    }
                },
            )

        run.status = "completed"
        run.source_rows_examined = len(records)
        run.candidate_rows = revisions_inserted + rows_unchanged
        run.blocked_rows = rows_blocked
        run.events_inserted = events_inserted
        run.revisions_inserted = revisions_inserted
        run.rows_unchanged = rows_unchanged
        run.completed_at = utc_now()
        return _PublicationResult(
            run_id=run.id,
            events_inserted=events_inserted,
            revisions_inserted=revisions_inserted,
            rows_unchanged=rows_unchanged,
            rows_blocked=rows_blocked,
            amount_conflicts=amount_conflicts,
        )

    def _is_same_rta_source_parser_revision(
        self,
        current: DistributionEventRevisionRecord,
        source: RtaDistributionRecord,
    ) -> bool:
        prior_sources = self._session.scalars(
            select(RtaDistributionRecord)
            .join(
                DistributionEventRevisionRtaSourceRecord,
                (
                    DistributionEventRevisionRtaSourceRecord.rta_distribution_record_id
                    == RtaDistributionRecord.id
                ),
            )
            .where(
                DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id
                == current.id
            )
        )
        return any(
            prior.scheme_capture_id == source.scheme_capture_id
            and prior.record_date == source.record_date
            and prior.raw_individual_amount == source.raw_individual_amount
            and prior.raw_non_individual_amount == source.raw_non_individual_amount
            and prior.ex_nav == source.ex_nav
            and prior.cum_nav == source.cum_nav
            and prior.individual_amount_per_unit_inr == current.amount_per_unit_inr
            for prior in prior_sources
        )

    def _record_issue(
        self,
        source: RtaDistributionRecord,
        run: DistributionNormalizationRunRecord,
        issue_code: str,
        details: str,
    ) -> None:
        self._session.add(
            RtaDistributionIssueRecord(
                rta_distribution_record_id=source.id,
                normalization_run_id=run.id,
                issue_code=issue_code,
                details=details,
            )
        )


def _nav_evidence(
    capture: RtaSchemeCapture,
) -> tuple[tuple[date, Decimal, str], ...]:
    evidence: set[tuple[date, Decimal, str]] = set()
    if capture.latest_nav_date is not None and capture.latest_nav_value is not None:
        evidence.add((capture.latest_nav_date, capture.latest_nav_value, "latest_nav"))
    for row in capture.rows:
        if row.ex_nav is not None:
            evidence.add((row.record_date, row.ex_nav, "ex_nav"))
        if row.cum_nav is not None:
            evidence.add((row.record_date, row.cum_nav, "cum_nav"))
    return tuple(sorted(evidence, key=lambda item: (item[0], item[2], item[1])))


def _scheme_core(value: str) -> str:
    current_name = re.split(
        r"\b(?:formerly(?:\s+known\s+as)?|erstwhile)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    current_name = re.sub(
        r"^\s*(?:aditya\s+)?birla\s+sun\s+life\b",
        "ABSL",
        current_name,
        flags=re.IGNORECASE,
    )
    current_name = current_name.replace("+", " plus ")
    current_name = re.sub(
        r"\bre[\s_-]+inv(?:e(?:st(?:ment)?)?)?\b", " ", current_name, flags=re.IGNORECASE
    )
    current_name = re.sub(r"\b(?:qidcw|qly|qtly)\b", "quarterly", current_name, flags=re.IGNORECASE)
    current_name = re.sub(r"\bhalfyerarly\b", "half yearly", current_name, flags=re.IGNORECASE)
    current_name = re.sub(r"\bsep(?:t)?\b", "september", current_name, flags=re.IGNORECASE)
    current_name = re.sub(r"\bindex\s+fund\b", "index", current_name, flags=re.IGNORECASE)
    tokens = re.findall(r"[a-z0-9]+", current_name.casefold())
    return " ".join(token for token in tokens if token not in _NON_IDENTITY_WORDS)


def _row_signature(capture: RtaSchemeCapture, row: RtaDistributionRow) -> str:
    signature_values = [
        "row",
        capture.provider,
        capture.rta_fund_code,
        capture.rta_scheme_code,
        capture.source_payload_sha256,
        row.record_date.isoformat(),
        row.raw_individual_amount,
        row.raw_non_individual_amount or "",
        str(row.ex_nav) if row.ex_nav is not None else "",
        str(row.cum_nav) if row.cum_nav is not None else "",
    ]
    raw_individual = Decimal(row.raw_individual_amount)
    raw_non_individual = (
        Decimal(row.raw_non_individual_amount)
        if row.raw_non_individual_amount is not None
        else None
    )
    if (
        raw_individual != row.individual_amount_per_unit_inr
        or raw_non_individual != row.non_individual_amount_per_unit_inr
    ):
        signature_values.extend(
            [
                "cams_decimal_noise_v1",
                str(row.individual_amount_per_unit_inr),
                (
                    str(row.non_individual_amount_per_unit_inr)
                    if row.non_individual_amount_per_unit_inr is not None
                    else ""
                ),
            ]
        )
    return _signature(*signature_values)


def _capture_signature(capture: RtaSchemeCapture) -> str:
    return _signature(
        "capture",
        capture.provider,
        capture.rta_fund_code,
        capture.rta_scheme_code,
        capture.source_payload_sha256,
    )


def _revision_signature(
    scheme_code: str,
    record_date: date,
    amount: Decimal,
    revision_number: int,
) -> str:
    return _signature(
        scheme_code,
        record_date.isoformat(),
        _EVENT_TYPE,
        str(amount),
        str(revision_number),
    )


def _signature(*values: str) -> str:
    encoded = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
