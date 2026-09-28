"""Reconcile canonical IDCW values touched by RTA NAV-fingerprint identities.

A fingerprint identity can later be withdrawn by a stricter rule. Rows it published must then
stop backing current values, and values it displaced must return. Only events on dates where a
fingerprint-identified capture had rows are examined. Revisions are never deleted; only their
current flag changes, and every changed revision is listed in the returned report.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from mf_strategy_tester.db.models import (
    AdvisorkhojDistributionIssueRecord,
    AdvisorkhojDistributionRecord,
    AdvisorkhojSchemeMappingReviewRecord,
    DistributionEventRecord,
    DistributionEventRevisionAdvisorkhojSourceRecord,
    DistributionEventRevisionOfficialSourceRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionRtaSourceRecord,
    DistributionEventRevisionSourceRecord,
    RtaDistributionIssueRecord,
    RtaDistributionRecord,
    RtaSchemeMappingReviewRecord,
)

logger = logging.getLogger(__name__)

RECONCILIATION_VERSION = "rta-nav-fingerprint-reconciliation-2026.09.1"
_EVENT_TYPE = "idcw_cash"
_IN_CHUNK = 500


@dataclass(frozen=True)
class FingerprintReconciliationResult:
    reconciliation_version: str
    dry_run: bool
    fingerprint_captures: int
    fingerprint_captures_still_mapped: int
    events_examined: int
    events_unchanged: int
    events_withheld_for_same_priority_conflict: int
    events_left_without_current: int
    revisions_retired: tuple[str, ...]
    revisions_restored: tuple[str, ...]


@dataclass(frozen=True)
class _Revision:
    id: str
    revision_number: int
    is_current: bool


class RtaFingerprintReconciliationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reconcile(self, *, dry_run: bool) -> FingerprintReconciliationResult:
        latest_review: dict[str, RtaSchemeMappingReviewRecord] = {}
        mapped_codes: dict[str, set[str]] = {}
        fingerprint_codes: dict[str, set[str]] = {}
        for review in self._session.scalars(
            select(RtaSchemeMappingReviewRecord).order_by(
                RtaSchemeMappingReviewRecord.reviewed_at, RtaSchemeMappingReviewRecord.id
            )
        ):
            latest_review[review.scheme_capture_id] = review
            if review.status == "mapped" and review.amfi_scheme_code is not None:
                mapped_codes.setdefault(review.scheme_capture_id, set()).add(
                    review.amfi_scheme_code
                )
                if review.mapping_method == "nav_fingerprint":
                    fingerprint_codes.setdefault(review.scheme_capture_id, set()).add(
                        review.amfi_scheme_code
                    )
        accepted_code = {
            capture_id: review.amfi_scheme_code
            for capture_id, review in latest_review.items()
            if review.status == "mapped" and review.amfi_scheme_code is not None
        }
        codes = sorted({code for values in fingerprint_codes.values() for code in values})
        scope: set[tuple[str, date]] = set()
        for capture_id, record_date in self._capture_row_dates(sorted(fingerprint_codes)):
            scope.update((code, record_date) for code in fingerprint_codes[capture_id])

        events = self._scoped_events(codes, scope)
        revisions_by_event = self._revisions(codes, events)
        valid_revisions = self._valid_revisions(codes, events, accepted_code)
        same_priority_conflicts = (
            self._rta_peer_conflicts(fingerprint_codes, mapped_codes)
            | self._advisorkhoj_peer_conflicts()
        )

        retired: list[str] = []
        restored: list[str] = []
        unchanged = withheld = left_empty = 0
        for event_id in sorted(events):
            revisions = revisions_by_event.get(event_id, [])
            current = next((item for item in revisions if item.is_current), None)
            if current is not None and current.id in valid_revisions:
                unchanged += 1
                continue
            conflicted = events[event_id] in same_priority_conflicts
            replacement = (
                None
                if conflicted
                else max(
                    (item for item in revisions if item.id in valid_revisions),
                    key=lambda item: item.revision_number,
                    default=None,
                )
            )
            if current is None and replacement is None:
                withheld += conflicted
                unchanged += not conflicted
                continue
            if current is not None:
                retired.append(current.id)
                if not dry_run:
                    self._set_current(current.id, False)
            if replacement is None:
                withheld += conflicted
                left_empty += 1
                continue
            restored.append(replacement.id)
            if not dry_run:
                self._set_current(replacement.id, True)
        if dry_run:
            self._session.rollback()
        else:
            self._session.commit()
        result = FingerprintReconciliationResult(
            reconciliation_version=RECONCILIATION_VERSION,
            dry_run=dry_run,
            fingerprint_captures=len(fingerprint_codes),
            fingerprint_captures_still_mapped=sum(
                latest_review[capture_id].mapping_method == "nav_fingerprint"
                and latest_review[capture_id].status == "mapped"
                for capture_id in fingerprint_codes
            ),
            events_examined=len(events),
            events_unchanged=unchanged,
            events_withheld_for_same_priority_conflict=withheld,
            events_left_without_current=left_empty,
            revisions_retired=tuple(retired),
            revisions_restored=tuple(restored),
        )
        summary: dict[str, object] = asdict(result)
        summary["revisions_retired"] = len(retired)
        summary["revisions_restored"] = len(restored)
        logger.info("rta_fingerprint_reconciled", extra={"event_data": summary})
        return result

    def _set_current(self, revision_id: str, is_current: bool) -> None:
        revision = self._session.get_one(DistributionEventRevisionRecord, revision_id)
        revision.is_current = is_current
        # The partial unique index allows one current revision per event at each flush.
        self._session.flush()

    def _capture_row_dates(self, capture_ids: list[str]) -> Iterable[tuple[str, date]]:
        for start in range(0, len(capture_ids), _IN_CHUNK):
            yield from self._session.execute(
                select(RtaDistributionRecord.scheme_capture_id, RtaDistributionRecord.record_date)
                .distinct()
                .where(
                    RtaDistributionRecord.scheme_capture_id.in_(
                        capture_ids[start : start + _IN_CHUNK]
                    )
                )
            ).tuples()

    def _scoped_events(
        self, codes: list[str], scope: set[tuple[str, date]]
    ) -> dict[str, tuple[str, date]]:
        events: dict[str, tuple[str, date]] = {}
        for start in range(0, len(codes), _IN_CHUNK):
            for event_id, code, record_date in self._session.execute(
                select(
                    DistributionEventRecord.id,
                    DistributionEventRecord.amfi_scheme_code,
                    DistributionEventRecord.record_date,
                ).where(
                    DistributionEventRecord.amfi_scheme_code.in_(codes[start : start + _IN_CHUNK]),
                    DistributionEventRecord.event_type == _EVENT_TYPE,
                )
            ).tuples():
                if (code, record_date) in scope:
                    events[event_id] = (code, record_date)
        return events

    def _revisions(
        self, codes: list[str], events: dict[str, tuple[str, date]]
    ) -> dict[str, list[_Revision]]:
        revisions: dict[str, list[_Revision]] = {}
        for start in range(0, len(codes), _IN_CHUNK):
            for revision_id, event_id, revision_number, is_current in self._session.execute(
                select(
                    DistributionEventRevisionRecord.id,
                    DistributionEventRevisionRecord.distribution_event_id,
                    DistributionEventRevisionRecord.revision_number,
                    DistributionEventRevisionRecord.is_current,
                )
                .join(
                    DistributionEventRecord,
                    DistributionEventRecord.id
                    == DistributionEventRevisionRecord.distribution_event_id,
                )
                .where(
                    DistributionEventRecord.amfi_scheme_code.in_(codes[start : start + _IN_CHUNK])
                )
            ).tuples():
                if event_id in events:
                    revisions.setdefault(event_id, []).append(
                        _Revision(revision_id, revision_number, is_current)
                    )
        return revisions

    def _valid_revisions(
        self,
        codes: list[str],
        events: dict[str, tuple[str, date]],
        accepted_code: dict[str, str],
    ) -> set[str]:
        """A revision stays valid while any source behind it is still accepted for its option."""
        valid: set[str] = set()
        independent_links: tuple[InstrumentedAttribute[str], ...] = (
            DistributionEventRevisionSourceRecord.distribution_event_revision_id,
            DistributionEventRevisionOfficialSourceRecord.distribution_event_revision_id,
            DistributionEventRevisionAdvisorkhojSourceRecord.distribution_event_revision_id,
        )
        for start in range(0, len(codes), _IN_CHUNK):
            chunk = codes[start : start + _IN_CHUNK]
            for link_column in independent_links:
                for revision_id, event_id in self._session.execute(
                    select(link_column, DistributionEventRevisionRecord.distribution_event_id)
                    .join(
                        DistributionEventRevisionRecord,
                        DistributionEventRevisionRecord.id == link_column,
                    )
                    .join(
                        DistributionEventRecord,
                        DistributionEventRecord.id
                        == DistributionEventRevisionRecord.distribution_event_id,
                    )
                    .where(DistributionEventRecord.amfi_scheme_code.in_(chunk))
                ).tuples():
                    if event_id in events:
                        valid.add(revision_id)
            for revision_id, event_id, capture_id in self._session.execute(
                select(
                    DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id,
                    DistributionEventRevisionRecord.distribution_event_id,
                    RtaDistributionRecord.scheme_capture_id,
                )
                .join(
                    DistributionEventRevisionRecord,
                    DistributionEventRevisionRecord.id
                    == DistributionEventRevisionRtaSourceRecord.distribution_event_revision_id,
                )
                .join(
                    DistributionEventRecord,
                    DistributionEventRecord.id
                    == DistributionEventRevisionRecord.distribution_event_id,
                )
                .join(
                    RtaDistributionRecord,
                    RtaDistributionRecord.id
                    == DistributionEventRevisionRtaSourceRecord.rta_distribution_record_id,
                )
                .where(DistributionEventRecord.amfi_scheme_code.in_(chunk))
            ).tuples():
                event = events.get(event_id)
                if event is not None and accepted_code.get(capture_id) == event[0]:
                    valid.add(revision_id)
        return valid

    def _rta_peer_conflicts(
        self,
        fingerprint_codes: dict[str, set[str]],
        mapped_codes: dict[str, set[str]],
    ) -> set[tuple[str, date]]:
        """Events whose missing value came from a CAMS/KFintech conflict between accepted rows."""
        conflicts: set[tuple[str, date]] = set()
        for capture_id, record_date in self._session.execute(
            select(RtaDistributionRecord.scheme_capture_id, RtaDistributionRecord.record_date)
            .distinct()
            .join(
                RtaDistributionIssueRecord,
                RtaDistributionIssueRecord.rta_distribution_record_id == RtaDistributionRecord.id,
            )
            .where(
                RtaDistributionIssueRecord.issue_code == "amount_conflict",
                or_(
                    RtaDistributionIssueRecord.details.contains("tier=rta"),
                    RtaDistributionIssueRecord.details.contains(
                        "existing equal-priority source conflict"
                    ),
                ),
            )
        ).tuples():
            if capture_id not in fingerprint_codes:
                conflicts.update((code, record_date) for code in mapped_codes.get(capture_id, ()))
        return conflicts

    def _advisorkhoj_peer_conflicts(self) -> set[tuple[str, date]]:
        """Events whose missing value came from disagreeing AdvisorKhoj rows."""
        return {
            (code, record_date)
            for code, record_date in self._session.execute(
                select(
                    AdvisorkhojSchemeMappingReviewRecord.amfi_scheme_code,
                    AdvisorkhojDistributionRecord.record_date,
                )
                .distinct()
                .join(
                    AdvisorkhojDistributionIssueRecord,
                    AdvisorkhojDistributionIssueRecord.mapping_review_id
                    == AdvisorkhojSchemeMappingReviewRecord.id,
                )
                .join(
                    AdvisorkhojDistributionRecord,
                    AdvisorkhojDistributionRecord.id
                    == AdvisorkhojDistributionIssueRecord.advisorkhoj_distribution_record_id,
                )
                .where(AdvisorkhojDistributionIssueRecord.issue_code == "same_priority_conflict")
            ).tuples()
            if code is not None
        }
