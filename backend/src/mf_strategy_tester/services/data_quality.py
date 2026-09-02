from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AdvisorkhojDistributionIssueRecord,
    AmfiDistributionRecord,
    DataQualityIssueRecord,
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionEventRevisionSourceRecord,
    DistributionIdentifierReviewRecord,
    DistributionNormalizationRunRecord,
    DistributionParseIssueRecord,
    IngestionBatchRecord,
    RtaDistributionIssueRecord,
    RtaSchemeMappingReviewRecord,
    SchemeOptionRecord,
    utc_now,
)
from mf_strategy_tester.services.distribution_quality import (
    DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES,
    classify_distribution_label,
    classify_distribution_record,
)


@dataclass(frozen=True)
class NavIssueSummary:
    issue_code: str
    severity: str
    distinct_observations: int
    occurrence_count: int
    affected_schemes: int
    earliest_nav_date: str
    latest_nav_date: str


@dataclass(frozen=True)
class UnresolvedDistributionIdentifier:
    source_option_id: str
    record_count: int
    mutual_fund_ids: tuple[str, ...]
    source_scheme_ids: tuple[str, ...]
    scheme_names: tuple[str, ...]
    nav_names: tuple[str, ...]
    earliest_record_date: str
    latest_record_date: str
    review_status: str | None
    review_id: str | None
    evidence_batch_id: str | None
    matched_amfi_scheme_code: str | None


@dataclass(frozen=True)
class RunningIngestionBatch:
    batch_id: str
    source_type: str
    started_at: str
    request_parameters: dict[str, object]


@dataclass(frozen=True)
class DistributionNormalizationGateSummary:
    category: str
    row_count: int
    identifier_count: int


@dataclass(frozen=True)
class DistributionLabelReviewItem:
    classification: str
    source_option_id: str
    record_count: int
    scheme_names: tuple[str, ...]
    nav_names: tuple[str, ...]


@dataclass(frozen=True)
class DistributionNormalizationRunSummary:
    run_id: str
    status: str
    normalization_version: str
    source_rows_examined: int
    candidate_rows: int
    blocked_rows: int
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int
    started_at: str
    completed_at: str | None
    error_details: str | None


@dataclass(frozen=True)
class DataQualityReport:
    generated_at: str
    nav_issues: tuple[NavIssueSummary, ...]
    distribution_parse_issues_open: int
    distribution_parse_issues_resolved: int
    non_positive_distribution_value_rows: int
    non_positive_distribution_value_identifiers: int
    unresolved_distribution_rows: int
    distribution_identifier_review_counts: dict[str, int]
    rta_scheme_mapping_review_counts: dict[str, int]
    rta_distribution_issue_counts: dict[str, int]
    advisorkhoj_distribution_issue_counts: dict[str, int]
    unresolved_distribution_identifiers: tuple[UnresolvedDistributionIdentifier, ...]
    distribution_normalization_gate: tuple[DistributionNormalizationGateSummary, ...]
    distribution_label_review_items: tuple[DistributionLabelReviewItem, ...]
    canonical_distribution_events: int
    canonical_distribution_revisions: int
    canonical_distribution_source_links: int
    latest_distribution_normalization_run: DistributionNormalizationRunSummary | None
    running_ingestion_batches: tuple[RunningIngestionBatch, ...]


@dataclass
class _DistributionIdentifierAccumulator:
    record_count: int = 0
    mutual_fund_ids: set[str] = field(default_factory=set)
    source_scheme_ids: set[str] = field(default_factory=set)
    scheme_names: set[str] = field(default_factory=set)
    nav_names: set[str] = field(default_factory=set)
    earliest_record_date: date | None = None
    latest_record_date: date | None = None


@dataclass
class _DistributionLabelAccumulator:
    record_count: int = 0
    scheme_names: set[str] = field(default_factory=set)
    nav_names: set[str] = field(default_factory=set)


class DataQualityReportService:
    """Build an audit report without mutating source or normalized observations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build_report(self) -> DataQualityReport:
        nav_issues = self._nav_issue_summaries()
        distribution_issue_counts: dict[str, int] = {
            status: int(issue_count)
            for status, issue_count in self._session.execute(
                select(
                    DistributionParseIssueRecord.status,
                    func.count(DistributionParseIssueRecord.id),
                ).group_by(DistributionParseIssueRecord.status)
            )
        }
        unresolved_identifiers = self._unresolved_distribution_identifiers()
        non_positive_distribution_counts = self._session.execute(
            select(
                func.count(AmfiDistributionRecord.id),
                func.count(func.distinct(AmfiDistributionRecord.source_option_id)),
            ).where(
                AmfiDistributionRecord.source_value.is_not(None),
                cast(AmfiDistributionRecord.source_value, Numeric) <= 0,
            )
        ).one()
        review_counts = {"unreviewed": 0, "source_only": 0, "mapped": 0, "source_error": 0}
        for identifier in unresolved_identifiers:
            review_counts[identifier.review_status or "unreviewed"] += 1
        rta_mapping_counts = {
            status: int(count)
            for status, count in self._session.execute(
                select(
                    RtaSchemeMappingReviewRecord.status,
                    func.count(RtaSchemeMappingReviewRecord.id),
                ).group_by(RtaSchemeMappingReviewRecord.status)
            )
        }
        rta_issue_counts = {
            issue_code: int(count)
            for issue_code, count in self._session.execute(
                select(
                    RtaDistributionIssueRecord.issue_code,
                    func.count(RtaDistributionIssueRecord.id),
                ).group_by(RtaDistributionIssueRecord.issue_code)
            )
        }
        advisorkhoj_issue_counts = {
            issue_code: int(count)
            for issue_code, count in self._session.execute(
                select(
                    AdvisorkhojDistributionIssueRecord.issue_code,
                    func.count(AdvisorkhojDistributionIssueRecord.id),
                ).group_by(AdvisorkhojDistributionIssueRecord.issue_code)
            )
        }
        normalization_gate, label_review_items = self._distribution_normalization_gate()
        canonical_counts = (
            self._session.scalar(select(func.count()).select_from(DistributionEventRecord)) or 0,
            self._session.scalar(select(func.count()).select_from(DistributionEventRevisionRecord))
            or 0,
            (
                self._session.scalar(
                    select(func.count()).select_from(DistributionEventRevisionSourceRecord)
                )
                or 0
            )
            + (
                self._session.scalar(
                    select(func.count()).select_from(DistributionEventRevisionOfficialSourceRecord)
                )
                or 0
            )
            + (
                self._session.scalar(
                    select(func.count()).select_from(DistributionEventRevisionRtaSourceRecord)
                )
                or 0
            )
            + (
                self._session.scalar(
                    select(func.count()).select_from(
                        DistributionEventRevisionAdvisorkhojSourceRecord
                    )
                )
                or 0
            ),
        )
        latest_normalization_run_record = self._session.scalar(
            select(DistributionNormalizationRunRecord).order_by(
                DistributionNormalizationRunRecord.started_at.desc(),
                DistributionNormalizationRunRecord.id.desc(),
            )
        )
        latest_normalization_run = (
            DistributionNormalizationRunSummary(
                run_id=latest_normalization_run_record.id,
                status=latest_normalization_run_record.status,
                normalization_version=latest_normalization_run_record.normalization_version,
                source_rows_examined=latest_normalization_run_record.source_rows_examined,
                candidate_rows=latest_normalization_run_record.candidate_rows,
                blocked_rows=latest_normalization_run_record.blocked_rows,
                events_inserted=latest_normalization_run_record.events_inserted,
                revisions_inserted=latest_normalization_run_record.revisions_inserted,
                rows_unchanged=latest_normalization_run_record.rows_unchanged,
                started_at=latest_normalization_run_record.started_at.isoformat(),
                completed_at=(
                    latest_normalization_run_record.completed_at.isoformat()
                    if latest_normalization_run_record.completed_at is not None
                    else None
                ),
                error_details=latest_normalization_run_record.error_details,
            )
            if latest_normalization_run_record is not None
            else None
        )
        running_batches = tuple(
            RunningIngestionBatch(
                batch_id=batch.id,
                source_type=batch.source_type,
                started_at=batch.started_at.isoformat(),
                request_parameters=dict(batch.request_parameters),
            )
            for batch in self._session.scalars(
                select(IngestionBatchRecord)
                .where(IngestionBatchRecord.status == "running")
                .order_by(IngestionBatchRecord.started_at, IngestionBatchRecord.id)
            ).all()
        )
        return DataQualityReport(
            generated_at=utc_now().isoformat(),
            nav_issues=nav_issues,
            distribution_parse_issues_open=int(distribution_issue_counts.get("open", 0)),
            distribution_parse_issues_resolved=int(distribution_issue_counts.get("resolved", 0)),
            non_positive_distribution_value_rows=int(non_positive_distribution_counts[0]),
            non_positive_distribution_value_identifiers=int(non_positive_distribution_counts[1]),
            unresolved_distribution_rows=sum(
                identifier.record_count for identifier in unresolved_identifiers
            ),
            distribution_identifier_review_counts=review_counts,
            rta_scheme_mapping_review_counts=rta_mapping_counts,
            rta_distribution_issue_counts=rta_issue_counts,
            advisorkhoj_distribution_issue_counts=advisorkhoj_issue_counts,
            unresolved_distribution_identifiers=unresolved_identifiers,
            distribution_normalization_gate=normalization_gate,
            distribution_label_review_items=label_review_items,
            canonical_distribution_events=int(canonical_counts[0]),
            canonical_distribution_revisions=int(canonical_counts[1]),
            canonical_distribution_source_links=int(canonical_counts[2]),
            latest_distribution_normalization_run=latest_normalization_run,
            running_ingestion_batches=running_batches,
        )

    def _distribution_normalization_gate(
        self,
    ) -> tuple[
        tuple[DistributionNormalizationGateSummary, ...],
        tuple[DistributionLabelReviewItem, ...],
    ]:
        exact_option_ids = set(
            self._session.scalars(select(SchemeOptionRecord.amfi_scheme_code)).all()
        )
        row_counts = {category: 0 for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES}
        identifiers: dict[str, set[str]] = {
            category: set() for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES
        }
        label_items: dict[tuple[str, str], _DistributionLabelAccumulator] = {}
        records = self._session.execute(
            select(
                AmfiDistributionRecord.source_option_id,
                AmfiDistributionRecord.scheme_name,
                AmfiDistributionRecord.nav_name,
                AmfiDistributionRecord.source_unit,
                AmfiDistributionRecord.source_value,
            )
        ).all()
        for record in records:
            category = classify_distribution_record(
                source_option_is_exact=record.source_option_id in exact_option_ids,
                source_unit=record.source_unit,
                source_value=record.source_value,
                scheme_name=record.scheme_name,
                nav_name=record.nav_name,
            )
            if category in {
                "explicit_bonus_option",
                "explicit_growth_or_cumulative_option",
                "ambiguous_bonus_or_distribution_option",
                "conflicting_option_labels",
                "unknown_option_label",
            }:
                assessment = classify_distribution_label(
                    scheme_name=record.scheme_name,
                    nav_name=record.nav_name,
                )
                item = label_items.setdefault(
                    (assessment.classification, record.source_option_id),
                    _DistributionLabelAccumulator(),
                )
                item.record_count += 1
                item.scheme_names.add(record.scheme_name)
                item.nav_names.add(record.nav_name)
            row_counts[category] += 1
            identifiers[category].add(record.source_option_id)

        if sum(row_counts.values()) != len(records):
            raise RuntimeError("distribution normalization gate did not partition every source row")
        summaries = tuple(
            DistributionNormalizationGateSummary(
                category=category,
                row_count=row_counts[category],
                identifier_count=len(identifiers[category]),
            )
            for category in DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES
        )
        review_items = tuple(
            DistributionLabelReviewItem(
                classification=classification,
                source_option_id=source_option_id,
                record_count=item.record_count,
                scheme_names=tuple(sorted(item.scheme_names)),
                nav_names=tuple(sorted(item.nav_names)),
            )
            for (classification, source_option_id), item in sorted(label_items.items())
        )
        return summaries, review_items

    def _nav_issue_summaries(self) -> tuple[NavIssueSummary, ...]:
        deduplicated = (
            select(
                DataQualityIssueRecord.issue_code.label("issue_code"),
                DataQualityIssueRecord.severity.label("severity"),
                DataQualityIssueRecord.amfi_scheme_code.label("amfi_scheme_code"),
                DataQualityIssueRecord.nav_date.label("nav_date"),
                func.count(DataQualityIssueRecord.id).label("occurrence_count"),
            )
            .group_by(
                DataQualityIssueRecord.issue_code,
                DataQualityIssueRecord.severity,
                DataQualityIssueRecord.amfi_scheme_code,
                DataQualityIssueRecord.nav_date,
            )
            .subquery()
        )
        rows = self._session.execute(
            select(
                deduplicated.c.issue_code,
                deduplicated.c.severity,
                func.count().label("distinct_observations"),
                func.sum(deduplicated.c.occurrence_count).label("occurrence_count"),
                func.count(func.distinct(deduplicated.c.amfi_scheme_code)).label(
                    "affected_schemes"
                ),
                func.min(deduplicated.c.nav_date).label("earliest_nav_date"),
                func.max(deduplicated.c.nav_date).label("latest_nav_date"),
            )
            .group_by(deduplicated.c.issue_code, deduplicated.c.severity)
            .order_by(deduplicated.c.severity, deduplicated.c.issue_code)
        ).all()
        return tuple(
            NavIssueSummary(
                issue_code=row.issue_code,
                severity=row.severity,
                distinct_observations=int(row.distinct_observations),
                occurrence_count=int(row.occurrence_count),
                affected_schemes=int(row.affected_schemes),
                earliest_nav_date=row.earliest_nav_date.isoformat(),
                latest_nav_date=row.latest_nav_date.isoformat(),
            )
            for row in rows
        )

    def _unresolved_distribution_identifiers(
        self,
    ) -> tuple[UnresolvedDistributionIdentifier, ...]:
        latest_reviews: dict[str, DistributionIdentifierReviewRecord] = {}
        for review in self._session.scalars(
            select(DistributionIdentifierReviewRecord).order_by(
                DistributionIdentifierReviewRecord.source_option_id,
                DistributionIdentifierReviewRecord.reviewed_at.desc(),
                DistributionIdentifierReviewRecord.id.desc(),
            )
        ).all():
            latest_reviews.setdefault(review.source_option_id, review)
        rows = self._session.execute(
            select(
                AmfiDistributionRecord.source_option_id,
                AmfiDistributionRecord.mutual_fund_id,
                AmfiDistributionRecord.source_scheme_id,
                AmfiDistributionRecord.scheme_name,
                AmfiDistributionRecord.nav_name,
                AmfiDistributionRecord.record_date,
            )
            .outerjoin(
                SchemeOptionRecord,
                SchemeOptionRecord.amfi_scheme_code == AmfiDistributionRecord.source_option_id,
            )
            .where(SchemeOptionRecord.amfi_scheme_code.is_(None))
            .order_by(
                AmfiDistributionRecord.source_option_id,
                AmfiDistributionRecord.record_date,
                AmfiDistributionRecord.id,
            )
        ).all()
        identifiers: dict[str, _DistributionIdentifierAccumulator] = {}
        for row in rows:
            item = identifiers.setdefault(
                row.source_option_id, _DistributionIdentifierAccumulator()
            )
            item.record_count += 1
            item.mutual_fund_ids.add(row.mutual_fund_id)
            item.source_scheme_ids.add(row.source_scheme_id)
            item.scheme_names.add(row.scheme_name)
            item.nav_names.add(row.nav_name)
            item.earliest_record_date = min(
                item.earliest_record_date or row.record_date, row.record_date
            )
            item.latest_record_date = max(
                item.latest_record_date or row.record_date, row.record_date
            )

        result: list[UnresolvedDistributionIdentifier] = []
        for source_option_id, item in identifiers.items():
            if item.earliest_record_date is None or item.latest_record_date is None:
                raise RuntimeError("unresolved distribution identifier has no record dates")
            latest_review = latest_reviews.get(source_option_id)
            result.append(
                UnresolvedDistributionIdentifier(
                    source_option_id=source_option_id,
                    record_count=item.record_count,
                    mutual_fund_ids=tuple(sorted(item.mutual_fund_ids)),
                    source_scheme_ids=tuple(sorted(item.source_scheme_ids)),
                    scheme_names=tuple(sorted(item.scheme_names)),
                    nav_names=tuple(sorted(item.nav_names)),
                    earliest_record_date=item.earliest_record_date.isoformat(),
                    latest_record_date=item.latest_record_date.isoformat(),
                    review_status=(latest_review.status if latest_review is not None else None),
                    review_id=latest_review.id if latest_review is not None else None,
                    evidence_batch_id=(
                        latest_review.evidence_batch_id if latest_review is not None else None
                    ),
                    matched_amfi_scheme_code=(
                        latest_review.matched_amfi_scheme_code
                        if latest_review is not None
                        else None
                    ),
                )
            )
        return tuple(result)
