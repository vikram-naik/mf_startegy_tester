from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AdvisorkhojDistributionRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    AmfiDistributionRecord,
    DistributionCoverageAssessmentRecord,
    DistributionCoverageRunRecord,
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionEventRevisionSourceRecord,
    NavRevisionRecord,
    OfficialDistributionRecord,
    RtaDistributionRecord,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    utc_now,
)
from mf_strategy_tester.services.distribution_quality import (
    DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES,
    classify_distribution_record,
)

logger = logging.getLogger(__name__)

DISTRIBUTION_COVERAGE_ASSESSMENT_VERSION = "distribution-coverage-2026.08.6"


@dataclass(frozen=True)
class DistributionCoverageResult:
    run_id: str
    status: str
    assessment_version: str
    options_examined: int
    events_present_options: int
    blocked_source_options: int
    unverified_empty_options: int


@dataclass(frozen=True)
class DistributionBlockerGateSummary:
    category: str
    row_count: int
    option_count: int


@dataclass(frozen=True)
class BlockedDistributionOption:
    amfi_scheme_code: str
    scheme_name: str
    source_row_count: int
    blocked_source_row_count: int
    amfi_source_row_count: int
    other_accepted_source_row_count: int
    first_source_record_date: str
    last_source_record_date: str
    amfi_gate_counts: dict[str, int]


@dataclass(frozen=True)
class DistributionBlockerReport:
    coverage_run_id: str
    assessment_version: str
    blocked_options: int
    blocked_source_rows: int
    amfi_source_rows: int
    other_accepted_source_rows: int
    amfi_gate: tuple[DistributionBlockerGateSummary, ...]
    options: tuple[BlockedDistributionOption, ...]


@dataclass(frozen=True)
class _SourceStats:
    row_count: int
    first_record_date: date
    last_record_date: date


class DistributionCoverageAssessmentService:
    """Snapshot what the current evidence can and cannot establish for every IDCW option."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def assess(self) -> DistributionCoverageResult:
        self._fail_stale_runs()
        run = DistributionCoverageRunRecord(
            status="running",
            assessment_version=DISTRIBUTION_COVERAGE_ASSESSMENT_VERSION,
        )
        self._session.add(run)
        self._session.commit()
        try:
            result = self._assess(run)
            self._session.commit()
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed_run = self._session.get(DistributionCoverageRunRecord, run.id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.completed_at = utc_now()
                message = str(error) or "interrupted by user"
                failed_run.error_details = f"{type(error).__name__}: {message}"[:4000]
                self._session.commit()
            raise
        logger.info("distribution_coverage_assessed", extra={"event_data": result.__dict__})
        return result

    def blocked_source_report(self) -> DistributionBlockerReport:
        """Explain the latest completed coverage snapshot without changing source evidence."""

        latest_run = self._session.scalar(
            select(DistributionCoverageRunRecord)
            .where(DistributionCoverageRunRecord.status == "completed")
            .order_by(
                DistributionCoverageRunRecord.completed_at.desc(),
                DistributionCoverageRunRecord.started_at.desc(),
                DistributionCoverageRunRecord.id.desc(),
            )
        )
        if latest_run is None:
            raise LookupError("no completed distribution coverage assessment exists")

        assessment_rows = self._session.execute(
            select(
                DistributionCoverageAssessmentRecord,
                SchemeMetadataVersionRecord.scheme_name,
            )
            .join(
                SchemeMetadataVersionRecord,
                SchemeMetadataVersionRecord.id
                == DistributionCoverageAssessmentRecord.metadata_version_id,
            )
            .where(
                DistributionCoverageAssessmentRecord.coverage_run_id == latest_run.id,
                DistributionCoverageAssessmentRecord.coverage_status == "blocked_source_rows",
            )
            .order_by(
                DistributionCoverageAssessmentRecord.blocked_source_row_count.desc(),
                DistributionCoverageAssessmentRecord.amfi_scheme_code,
            )
        ).all()
        if len(assessment_rows) != latest_run.blocked_source_options:
            raise RuntimeError(
                "latest distribution coverage blocker count does not match its assessments"
            )

        scheme_codes = tuple(row.amfi_scheme_code for row, _ in assessment_rows)
        gate_counts_by_option: dict[str, dict[str, int]] = {
            scheme_code: {} for scheme_code in scheme_codes
        }
        gate_option_ids: dict[str, set[str]] = {
            category: set() for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES
        }
        gate_row_counts = {category: 0 for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES}
        if scheme_codes:
            source_rows = self._session.execute(
                select(
                    AmfiDistributionRecord.source_option_id,
                    AmfiDistributionRecord.scheme_name,
                    AmfiDistributionRecord.nav_name,
                    AmfiDistributionRecord.source_unit,
                    AmfiDistributionRecord.source_value,
                ).where(AmfiDistributionRecord.source_option_id.in_(scheme_codes))
            ).all()
            for source_row in source_rows:
                category = classify_distribution_record(
                    source_option_is_exact=True,
                    source_unit=source_row.source_unit,
                    source_value=source_row.source_value,
                    scheme_name=source_row.scheme_name,
                    nav_name=source_row.nav_name,
                )
                option_counts = gate_counts_by_option[source_row.source_option_id]
                option_counts[category] = option_counts.get(category, 0) + 1
                gate_row_counts[category] += 1
                gate_option_ids[category].add(source_row.source_option_id)

        options: list[BlockedDistributionOption] = []
        amfi_source_rows = 0
        other_accepted_source_rows = 0
        for assessment, scheme_name in assessment_rows:
            if (
                assessment.first_source_record_date is None
                or assessment.last_source_record_date is None
            ):
                raise RuntimeError(
                    f"blocked option {assessment.amfi_scheme_code} has no source date range"
                )
            option_gate_counts = gate_counts_by_option[assessment.amfi_scheme_code]
            option_amfi_rows = sum(option_gate_counts.values())
            option_other_rows = assessment.source_row_count - option_amfi_rows
            if option_other_rows < 0:
                raise RuntimeError(
                    "AMFI source rows exceed accepted rows for option "
                    f"{assessment.amfi_scheme_code}"
                )
            amfi_source_rows += option_amfi_rows
            other_accepted_source_rows += option_other_rows
            options.append(
                BlockedDistributionOption(
                    amfi_scheme_code=assessment.amfi_scheme_code,
                    scheme_name=scheme_name,
                    source_row_count=assessment.source_row_count,
                    blocked_source_row_count=assessment.blocked_source_row_count,
                    amfi_source_row_count=option_amfi_rows,
                    other_accepted_source_row_count=option_other_rows,
                    first_source_record_date=assessment.first_source_record_date.isoformat(),
                    last_source_record_date=assessment.last_source_record_date.isoformat(),
                    amfi_gate_counts={
                        category: option_gate_counts[category]
                        for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES
                        if option_gate_counts.get(category, 0) > 0
                    },
                )
            )

        blocked_source_rows = sum(row.blocked_source_row_count for row, _ in assessment_rows)
        if amfi_source_rows + other_accepted_source_rows != sum(
            row.source_row_count for row, _ in assessment_rows
        ):
            raise RuntimeError("distribution blocker source partition is incomplete")
        return DistributionBlockerReport(
            coverage_run_id=latest_run.id,
            assessment_version=latest_run.assessment_version,
            blocked_options=len(options),
            blocked_source_rows=blocked_source_rows,
            amfi_source_rows=amfi_source_rows,
            other_accepted_source_rows=other_accepted_source_rows,
            amfi_gate=tuple(
                DistributionBlockerGateSummary(
                    category=category,
                    row_count=gate_row_counts[category],
                    option_count=len(gate_option_ids[category]),
                )
                for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES
            ),
            options=tuple(options),
        )

    def _fail_stale_runs(self) -> None:
        stale_runs = self._session.scalars(
            select(DistributionCoverageRunRecord).where(
                DistributionCoverageRunRecord.status == "running"
            )
        ).all()
        for stale_run in stale_runs:
            stale_run.status = "failed"
            stale_run.completed_at = utc_now()
            stale_run.error_details = (
                "Interrupted before completion; superseded by a later coverage assessment"
            )
        self._session.commit()

    def _assess(self, run: DistributionCoverageRunRecord) -> DistributionCoverageResult:
        idcw_options = self._latest_idcw_options()
        scheme_codes = tuple(idcw_options)
        source_stats = self._source_stats(scheme_codes)
        canonical_source_counts = self._canonical_source_counts(scheme_codes)
        canonical_event_counts = self._canonical_event_counts(scheme_codes)

        status_counts = {
            "events_present": 0,
            "blocked_source_rows": 0,
            "unverified_empty": 0,
        }
        assessed_at = utc_now()
        rows: list[dict[str, object]] = []
        for scheme_code, metadata_version_id in idcw_options.items():
            source = source_stats.get(scheme_code)
            source_row_count = source.row_count if source is not None else 0
            canonical_source_row_count = canonical_source_counts.get(scheme_code, 0)
            canonical_event_count = canonical_event_counts.get(scheme_code, 0)
            if canonical_source_row_count > source_row_count:
                raise RuntimeError(
                    f"canonical source links exceed source rows for option {scheme_code}"
                )
            if canonical_event_count > canonical_source_row_count:
                raise RuntimeError(f"canonical events exceed source links for option {scheme_code}")
            if canonical_event_count > 0:
                coverage_status = "events_present"
            elif source_row_count > 0:
                coverage_status = "blocked_source_rows"
            else:
                coverage_status = "unverified_empty"
            status_counts[coverage_status] += 1
            rows.append(
                {
                    "coverage_run_id": run.id,
                    "amfi_scheme_code": scheme_code,
                    "metadata_version_id": metadata_version_id,
                    "coverage_status": coverage_status,
                    "source_row_count": source_row_count,
                    "canonical_source_row_count": canonical_source_row_count,
                    "blocked_source_row_count": source_row_count - canonical_source_row_count,
                    "canonical_event_count": canonical_event_count,
                    "first_source_record_date": (
                        source.first_record_date if source is not None else None
                    ),
                    "last_source_record_date": (
                        source.last_record_date if source is not None else None
                    ),
                    "assessed_at": assessed_at,
                }
            )

        if rows:
            self._session.execute(insert(DistributionCoverageAssessmentRecord), rows)
        run.status = "completed"
        run.options_examined = len(rows)
        run.events_present_options = status_counts["events_present"]
        run.blocked_source_options = status_counts["blocked_source_rows"]
        run.unverified_empty_options = status_counts["unverified_empty"]
        run.completed_at = utc_now()
        return DistributionCoverageResult(
            run_id=run.id,
            status=run.status,
            assessment_version=run.assessment_version,
            options_examined=run.options_examined,
            events_present_options=run.events_present_options,
            blocked_source_options=run.blocked_source_options,
            unverified_empty_options=run.unverified_empty_options,
        )

    def _latest_idcw_options(self) -> dict[str, str]:
        latest_metadata_id = (
            select(NavRevisionRecord.metadata_version_id)
            .where(
                NavRevisionRecord.amfi_scheme_code == SchemeOptionRecord.amfi_scheme_code,
                NavRevisionRecord.nav_date == SchemeOptionRecord.last_observed_nav_date,
                NavRevisionRecord.is_current.is_(True),
            )
            .correlate(SchemeOptionRecord)
            .scalar_subquery()
        )
        option_metadata_rows = self._session.execute(
            select(SchemeOptionRecord.amfi_scheme_code, latest_metadata_id)
        ).tuples()
        metadata_option_types = dict(
            self._session.execute(
                select(
                    SchemeMetadataVersionRecord.id,
                    SchemeMetadataVersionRecord.option_type,
                )
            )
            .tuples()
            .all()
        )
        return {
            scheme_code: metadata_version_id
            for scheme_code, metadata_version_id in option_metadata_rows
            if metadata_version_id is not None
            and metadata_option_types.get(metadata_version_id) == "idcw"
        }

    def _source_stats(self, scheme_codes: tuple[str, ...]) -> dict[str, _SourceStats]:
        if not scheme_codes:
            return {}
        stats = {
            scheme_code: _SourceStats(
                row_count=row_count,
                first_record_date=first_record_date,
                last_record_date=last_record_date,
            )
            for scheme_code, row_count, first_record_date, last_record_date in (
                self._session.execute(
                    select(
                        AmfiDistributionRecord.source_option_id,
                        func.count(),
                        func.min(AmfiDistributionRecord.record_date),
                        func.max(AmfiDistributionRecord.record_date),
                    )
                    .where(AmfiDistributionRecord.source_option_id.in_(scheme_codes))
                    .group_by(AmfiDistributionRecord.source_option_id)
                ).tuples()
            )
        }
        official_rows = self._session.execute(
            select(
                OfficialDistributionRecord.amfi_scheme_code,
                func.count(),
                func.min(OfficialDistributionRecord.record_date),
                func.max(OfficialDistributionRecord.record_date),
            )
            .where(OfficialDistributionRecord.amfi_scheme_code.in_(scheme_codes))
            .group_by(OfficialDistributionRecord.amfi_scheme_code)
        ).tuples()
        for scheme_code, row_count, first_record_date, last_record_date in official_rows:
            existing = stats.get(scheme_code)
            stats[scheme_code] = _SourceStats(
                row_count=row_count + (existing.row_count if existing else 0),
                first_record_date=(
                    min(first_record_date, existing.first_record_date)
                    if existing
                    else first_record_date
                ),
                last_record_date=(
                    max(last_record_date, existing.last_record_date)
                    if existing
                    else last_record_date
                ),
            )
        latest_mappings: dict[str, RtaSchemeMappingReviewRecord] = {}
        for review in self._session.scalars(
            select(RtaSchemeMappingReviewRecord).order_by(
                RtaSchemeMappingReviewRecord.reviewed_at,
                RtaSchemeMappingReviewRecord.id,
            )
        ):
            latest_mappings[review.scheme_capture_id] = review
        rta_rows: dict[str, list[date]] = {}
        for record, capture in self._session.execute(
            select(RtaDistributionRecord, RtaSchemeCaptureRecord).join(
                RtaSchemeCaptureRecord,
                RtaSchemeCaptureRecord.id == RtaDistributionRecord.scheme_capture_id,
            )
        ):
            mapping = latest_mappings.get(capture.id)
            if (
                mapping is not None
                and mapping.status == "mapped"
                and mapping.amfi_scheme_code in scheme_codes
            ):
                rta_rows.setdefault(mapping.amfi_scheme_code, []).append(record.record_date)
        for scheme_code, record_dates in rta_rows.items():
            existing = stats.get(scheme_code)
            first_record_date = min(record_dates)
            last_record_date = max(record_dates)
            stats[scheme_code] = _SourceStats(
                row_count=len(record_dates) + (existing.row_count if existing else 0),
                first_record_date=(
                    min(first_record_date, existing.first_record_date)
                    if existing
                    else first_record_date
                ),
                last_record_date=(
                    max(last_record_date, existing.last_record_date)
                    if existing
                    else last_record_date
                ),
            )
        latest_advisorkhoj_mappings: dict[str, AdvisorkhojSchemeMappingReviewRecord] = {}
        for advisorkhoj_review in self._session.scalars(
            select(AdvisorkhojSchemeMappingReviewRecord).order_by(
                AdvisorkhojSchemeMappingReviewRecord.reviewed_at,
                AdvisorkhojSchemeMappingReviewRecord.id,
            )
        ):
            latest_advisorkhoj_mappings[advisorkhoj_review.scheme_capture_id] = advisorkhoj_review
        advisorkhoj_rows: dict[str, _SourceStats] = {}
        capture_source_rows = self._session.execute(
            select(
                AdvisorkhojDistributionRecord.scheme_capture_id,
                func.count(),
                func.min(AdvisorkhojDistributionRecord.record_date),
                func.max(AdvisorkhojDistributionRecord.record_date),
            ).group_by(AdvisorkhojDistributionRecord.scheme_capture_id)
        ).tuples()
        for capture_id, row_count, first_record_date, last_record_date in capture_source_rows:
            advisorkhoj_mapping = latest_advisorkhoj_mappings.get(capture_id)
            if (
                advisorkhoj_mapping is not None
                and advisorkhoj_mapping.status == "mapped"
                and advisorkhoj_mapping.amfi_scheme_code in scheme_codes
            ):
                scheme_code = advisorkhoj_mapping.amfi_scheme_code
                current = advisorkhoj_rows.get(scheme_code)
                advisorkhoj_rows[scheme_code] = _SourceStats(
                    row_count=row_count + (current.row_count if current else 0),
                    first_record_date=(
                        min(first_record_date, current.first_record_date)
                        if current
                        else first_record_date
                    ),
                    last_record_date=(
                        max(last_record_date, current.last_record_date)
                        if current
                        else last_record_date
                    ),
                )
        for scheme_code, advisorkhoj_source in advisorkhoj_rows.items():
            existing = stats.get(scheme_code)
            stats[scheme_code] = _SourceStats(
                row_count=advisorkhoj_source.row_count + (existing.row_count if existing else 0),
                first_record_date=(
                    min(advisorkhoj_source.first_record_date, existing.first_record_date)
                    if existing
                    else advisorkhoj_source.first_record_date
                ),
                last_record_date=(
                    max(advisorkhoj_source.last_record_date, existing.last_record_date)
                    if existing
                    else advisorkhoj_source.last_record_date
                ),
            )
        return stats

    def _canonical_source_counts(self, scheme_codes: tuple[str, ...]) -> dict[str, int]:
        if not scheme_codes:
            return {}
        counts = dict(
            self._session.execute(
                select(
                    AmfiDistributionRecord.source_option_id,
                    func.count(DistributionEventRevisionSourceRecord.source_distribution_record_id),
                )
                .join(
                    DistributionEventRevisionSourceRecord,
                    DistributionEventRevisionSourceRecord.source_distribution_record_id
                    == AmfiDistributionRecord.id,
                )
                .join(
                    DistributionEventRevisionRecord,
                    DistributionEventRevisionRecord.id
                    == DistributionEventRevisionSourceRecord.distribution_event_revision_id,
                )
                .where(
                    AmfiDistributionRecord.source_option_id.in_(scheme_codes),
                    DistributionEventRevisionRecord.is_current.is_(True),
                )
                .group_by(AmfiDistributionRecord.source_option_id)
            )
            .tuples()
            .all()
        )
        official_counts = self._session.execute(
            select(
                OfficialDistributionRecord.amfi_scheme_code,
                func.count(
                    DistributionEventRevisionOfficialSourceRecord.official_distribution_record_id
                ),
            )
            .join(
                DistributionEventRevisionOfficialSourceRecord,
                DistributionEventRevisionOfficialSourceRecord.official_distribution_record_id
                == OfficialDistributionRecord.id,
            )
            .join(
                DistributionEventRevisionRecord,
                DistributionEventRevisionRecord.id
                == DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id,
            )
            .where(
                OfficialDistributionRecord.amfi_scheme_code.in_(scheme_codes),
                DistributionEventRevisionRecord.is_current.is_(True),
            )
            .group_by(OfficialDistributionRecord.amfi_scheme_code)
        ).tuples()
        for scheme_code, count in official_counts:
            counts[scheme_code] = counts.get(scheme_code, 0) + count
        rta_counts = self._session.execute(
            select(
                RtaSchemeMappingReviewRecord.amfi_scheme_code,
                func.count(DistributionEventRevisionRtaSourceRecord.rta_distribution_record_id),
            )
            .join(
                DistributionEventRevisionRtaSourceRecord,
                DistributionEventRevisionRtaSourceRecord.mapping_review_id
                == RtaSchemeMappingReviewRecord.id,
            )
            .join(
                DistributionEventRevisionRecord,
                DistributionEventRevisionRecord.id
                == DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id,
            )
            .where(
                RtaSchemeMappingReviewRecord.status == "mapped",
                RtaSchemeMappingReviewRecord.amfi_scheme_code.in_(scheme_codes),
                DistributionEventRevisionRecord.is_current.is_(True),
            )
            .group_by(RtaSchemeMappingReviewRecord.amfi_scheme_code)
        ).tuples()
        for rta_scheme_code, count in rta_counts:
            if rta_scheme_code is not None:
                counts[rta_scheme_code] = counts.get(rta_scheme_code, 0) + count
        advisorkhoj_counts = self._session.execute(
            select(
                AdvisorkhojSchemeMappingReviewRecord.amfi_scheme_code,
                func.count(
                    DistributionEventRevisionAdvisorkhojSourceRecord.advisorkhoj_distribution_record_id
                ),
            )
            .join(
                DistributionEventRevisionAdvisorkhojSourceRecord,
                DistributionEventRevisionAdvisorkhojSourceRecord.mapping_review_id
                == AdvisorkhojSchemeMappingReviewRecord.id,
            )
            .join(
                DistributionEventRevisionRecord,
                DistributionEventRevisionRecord.id
                == DistributionEventRevisionAdvisorkhojSourceRecord.distribution_event_revision_id,
            )
            .where(
                AdvisorkhojSchemeMappingReviewRecord.status == "mapped",
                AdvisorkhojSchemeMappingReviewRecord.amfi_scheme_code.in_(scheme_codes),
                DistributionEventRevisionRecord.is_current.is_(True),
            )
            .group_by(AdvisorkhojSchemeMappingReviewRecord.amfi_scheme_code)
        ).tuples()
        for advisorkhoj_scheme_code, count in advisorkhoj_counts:
            if advisorkhoj_scheme_code is not None:
                counts[advisorkhoj_scheme_code] = counts.get(advisorkhoj_scheme_code, 0) + count
        return counts

    def _canonical_event_counts(self, scheme_codes: tuple[str, ...]) -> dict[str, int]:
        if not scheme_codes:
            return {}
        return dict(
            self._session.execute(
                select(DistributionEventRecord.amfi_scheme_code, func.count())
                .join(
                    DistributionEventRevisionRecord,
                    DistributionEventRevisionRecord.distribution_event_id
                    == DistributionEventRecord.id,
                )
                .where(DistributionEventRecord.amfi_scheme_code.in_(scheme_codes))
                .where(DistributionEventRevisionRecord.is_current.is_(True))
                .group_by(DistributionEventRecord.amfi_scheme_code)
            )
            .tuples()
            .all()
        )
