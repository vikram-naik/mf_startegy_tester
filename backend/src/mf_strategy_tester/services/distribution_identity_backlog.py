from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AdvisorkhojCatalogSchemeRecord,
    AdvisorkhojSchemeCaptureRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    RtaSchemeCaptureRecord,
    RtaSchemeMappingReviewRecord,
    utc_now,
)

_BACKLOG_STATUSES = frozenset({"unresolved", "ambiguous"})
_RTA_FINGERPRINT_BACKLOG_REASONS = {
    "unresolved": frozenset(
        {
            "insufficient_nav_fingerprint_evidence",
            "no_nav_fingerprint_candidate",
            "nav_fingerprint_conflict",
            "insufficient_nav_fingerprint_matches",
        }
    ),
    "ambiguous": frozenset({"multiple_nav_fingerprint_candidates"}),
}


@dataclass(frozen=True)
class IdentityBacklogProviderSummary:
    provider: str
    unresolved_nonempty_captures: int
    unresolved_source_rows: int
    ambiguous_nonempty_captures: int
    ambiguous_source_rows: int
    empty_unresolved_captures: int
    empty_ambiguous_captures: int


@dataclass(frozen=True)
class IdentityBacklogReasonSummary:
    provider: str
    status: str
    reason: str
    captures: int
    source_rows: int


@dataclass(frozen=True)
class DistributionIdentityBacklogItem:
    provider: str
    capture_id: str
    mapping_review_id: str
    fund_name: str
    source_scheme_code: str | None
    source_scheme_name: str
    plan_type: str
    option_variant: str
    source_row_count: int
    status: str
    reason: str
    candidate_count: int
    qualifying_candidate_codes: tuple[str, ...]


@dataclass(frozen=True)
class DistributionIdentityBacklogReport:
    generated_at: str
    provider_summaries: tuple[IdentityBacklogProviderSummary, ...]
    reason_summaries: tuple[IdentityBacklogReasonSummary, ...]
    nonempty_backlog_captures: int
    nonempty_backlog_source_rows: int
    ranked_capture_limit: int
    ranked_captures: tuple[DistributionIdentityBacklogItem, ...]


@dataclass(frozen=True)
class _EvidenceAssessment:
    reason: str
    candidate_count: int
    qualifying_candidate_codes: tuple[str, ...]


class DistributionIdentityBacklogService:
    """Report unique latest identity conclusions without changing append-only reviews."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build_report(self, *, ranked_capture_limit: int = 100) -> DistributionIdentityBacklogReport:
        if ranked_capture_limit < 1 or ranked_capture_limit > 1000:
            raise ValueError("ranked capture limit must be between 1 and 1000")

        latest_rta_reviews: dict[str, RtaSchemeMappingReviewRecord] = {}
        for rta_review in self._session.scalars(
            select(RtaSchemeMappingReviewRecord).order_by(
                RtaSchemeMappingReviewRecord.reviewed_at,
                RtaSchemeMappingReviewRecord.id,
            )
        ):
            latest_rta_reviews[rta_review.scheme_capture_id] = rta_review

        latest_advisorkhoj_reviews: dict[str, AdvisorkhojSchemeMappingReviewRecord] = {}
        for advisorkhoj_review in self._session.scalars(
            select(AdvisorkhojSchemeMappingReviewRecord).order_by(
                AdvisorkhojSchemeMappingReviewRecord.reviewed_at,
                AdvisorkhojSchemeMappingReviewRecord.id,
            )
        ):
            latest_advisorkhoj_reviews[advisorkhoj_review.scheme_capture_id] = advisorkhoj_review

        items: list[DistributionIdentityBacklogItem] = []
        empty_counts: dict[tuple[str, str], int] = {}
        for capture in self._session.scalars(select(RtaSchemeCaptureRecord)):
            latest_rta_review = latest_rta_reviews.get(capture.id)
            if latest_rta_review is None or latest_rta_review.status not in _BACKLOG_STATUSES:
                continue
            if capture.source_row_count == 0:
                key = (capture.provider, latest_rta_review.status)
                empty_counts[key] = empty_counts.get(key, 0) + 1
                continue
            evidence = _assess_rta_evidence(latest_rta_review)
            items.append(
                DistributionIdentityBacklogItem(
                    provider=capture.provider,
                    capture_id=capture.id,
                    mapping_review_id=latest_rta_review.id,
                    fund_name=capture.rta_fund_name,
                    source_scheme_code=capture.rta_scheme_code,
                    source_scheme_name=capture.source_scheme_name,
                    plan_type=capture.plan_type,
                    option_variant=capture.option_variant,
                    source_row_count=capture.source_row_count,
                    status=latest_rta_review.status,
                    reason=evidence.reason,
                    candidate_count=evidence.candidate_count,
                    qualifying_candidate_codes=evidence.qualifying_candidate_codes,
                )
            )

        advisorkhoj_captures = self._session.execute(
            select(AdvisorkhojSchemeCaptureRecord, AdvisorkhojCatalogSchemeRecord).join(
                AdvisorkhojCatalogSchemeRecord,
                AdvisorkhojCatalogSchemeRecord.id
                == AdvisorkhojSchemeCaptureRecord.catalog_scheme_id,
            )
        )
        for capture, catalog_scheme in advisorkhoj_captures:
            latest_advisorkhoj_review = latest_advisorkhoj_reviews.get(capture.id)
            if (
                latest_advisorkhoj_review is None
                or latest_advisorkhoj_review.status not in _BACKLOG_STATUSES
            ):
                continue
            if capture.source_row_count == 0:
                key = ("advisorkhoj", latest_advisorkhoj_review.status)
                empty_counts[key] = empty_counts.get(key, 0) + 1
                continue
            evidence = _assess_advisorkhoj_evidence(latest_advisorkhoj_review)
            items.append(
                DistributionIdentityBacklogItem(
                    provider="advisorkhoj",
                    capture_id=capture.id,
                    mapping_review_id=latest_advisorkhoj_review.id,
                    fund_name=catalog_scheme.amc_name,
                    source_scheme_code=None,
                    source_scheme_name=catalog_scheme.scheme_name,
                    plan_type=capture.plan_type,
                    option_variant=capture.option_variant,
                    source_row_count=capture.source_row_count,
                    status=latest_advisorkhoj_review.status,
                    reason=evidence.reason,
                    candidate_count=evidence.candidate_count,
                    qualifying_candidate_codes=evidence.qualifying_candidate_codes,
                )
            )

        items.sort(
            key=lambda item: (
                -item.source_row_count,
                item.provider,
                item.fund_name,
                item.source_scheme_name,
                item.capture_id,
            )
        )
        providers = ("cams", "kfintech", "advisorkhoj")
        provider_summaries = tuple(
            IdentityBacklogProviderSummary(
                provider=provider,
                unresolved_nonempty_captures=sum(
                    item.provider == provider and item.status == "unresolved" for item in items
                ),
                unresolved_source_rows=sum(
                    item.source_row_count
                    for item in items
                    if item.provider == provider and item.status == "unresolved"
                ),
                ambiguous_nonempty_captures=sum(
                    item.provider == provider and item.status == "ambiguous" for item in items
                ),
                ambiguous_source_rows=sum(
                    item.source_row_count
                    for item in items
                    if item.provider == provider and item.status == "ambiguous"
                ),
                empty_unresolved_captures=empty_counts.get((provider, "unresolved"), 0),
                empty_ambiguous_captures=empty_counts.get((provider, "ambiguous"), 0),
            )
            for provider in providers
        )
        reason_keys = sorted({(item.provider, item.status, item.reason) for item in items})
        reason_summaries = tuple(
            IdentityBacklogReasonSummary(
                provider=provider,
                status=status,
                reason=reason,
                captures=sum(
                    item.provider == provider and item.status == status and item.reason == reason
                    for item in items
                ),
                source_rows=sum(
                    item.source_row_count
                    for item in items
                    if item.provider == provider and item.status == status and item.reason == reason
                ),
            )
            for provider, status, reason in reason_keys
        )
        return DistributionIdentityBacklogReport(
            generated_at=utc_now().isoformat(),
            provider_summaries=provider_summaries,
            reason_summaries=reason_summaries,
            nonempty_backlog_captures=len(items),
            nonempty_backlog_source_rows=sum(item.source_row_count for item in items),
            ranked_capture_limit=ranked_capture_limit,
            ranked_captures=tuple(items[:ranked_capture_limit]),
        )


def _assess_rta_evidence(review: RtaSchemeMappingReviewRecord) -> _EvidenceAssessment:
    evidence = _parse_evidence(review.evidence_details, review.id)
    candidate_codes = _string_list(evidence, "candidate_codes", review.id)
    if review.status == "unresolved":
        if candidate_codes:
            raise RuntimeError(f"unresolved RTA review {review.id} has candidate codes")
        reason = "no_exact_name_plan_nav_candidate"
    else:
        if len(candidate_codes) < 2:
            raise RuntimeError(f"ambiguous RTA review {review.id} has fewer than two candidates")
        reason = "multiple_exact_name_plan_nav_candidates"
    fingerprint = evidence.get("nav_fingerprint")
    if fingerprint is not None:
        fingerprint_reason = fingerprint.get("reason") if isinstance(fingerprint, dict) else None
        if fingerprint_reason not in _RTA_FINGERPRINT_BACKLOG_REASONS[review.status]:
            raise RuntimeError(
                f"{review.status} RTA review {review.id} has invalid NAV fingerprint reason"
            )
        reason = str(fingerprint_reason)
    return _EvidenceAssessment(reason, len(candidate_codes), candidate_codes)


def _assess_advisorkhoj_evidence(
    review: AdvisorkhojSchemeMappingReviewRecord,
) -> _EvidenceAssessment:
    evidence = _parse_evidence(review.evidence_details, review.id)
    candidate_codes = _string_list(evidence, "candidate_codes", review.id)
    qualifying_codes = _string_list(evidence, "qualifying_codes", review.id)
    if review.status == "ambiguous":
        if len(qualifying_codes) < 2:
            raise RuntimeError(
                f"ambiguous AdvisorKhoj review {review.id} has fewer than two qualifying candidates"
            )
        reason = "multiple_qualifying_nav_candidates"
    else:
        if qualifying_codes:
            raise RuntimeError(
                f"unresolved AdvisorKhoj review {review.id} has qualifying candidates"
            )
        if not candidate_codes:
            reason = "no_metadata_candidate"
        else:
            candidates = evidence.get("candidates")
            if not isinstance(candidates, list):
                raise RuntimeError(f"mapping review {review.id} has invalid candidates evidence")
            has_conflict = any(
                isinstance(candidate, dict)
                and isinstance(candidate.get("conflicts"), list)
                and bool(candidate["conflicts"])
                for candidate in candidates
            )
            reason = "comparable_nav_conflict" if has_conflict else "insufficient_nav_evidence"
    return _EvidenceAssessment(reason, len(candidate_codes), qualifying_codes)


def _parse_evidence(value: str, review_id: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"mapping review {review_id} has invalid JSON evidence") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"mapping review {review_id} evidence must be a JSON object")
    return parsed


def _string_list(evidence: dict[str, object], key: str, review_id: str) -> tuple[str, ...]:
    value = evidence.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"mapping review {review_id} has invalid {key} evidence")
    if len(value) != len(set(value)):
        raise RuntimeError(f"mapping review {review_id} has duplicate {key} evidence")
    return tuple(value)
