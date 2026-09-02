from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    DistributionEventRecord,
    DistributionEventRevisionRecord,
    DistributionEventRevisionSourceRecord,
    DistributionNormalizationRunRecord,
    SchemeOptionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.services.distribution_quality import (
    DistributionNormalizationGateCategory,
    classify_distribution_record,
)

logger = logging.getLogger(__name__)

DISTRIBUTION_NORMALIZATION_VERSION = "amfi-distribution-2026.08.1"
_EVENT_TYPE = "idcw_cash"


@dataclass(frozen=True)
class DistributionNormalizationResult:
    run_id: str
    status: str
    normalization_version: str
    source_rows_examined: int
    candidate_rows: int
    blocked_rows: int
    events_inserted: int
    revisions_inserted: int
    rows_unchanged: int


class DistributionNormalizationService:
    """Publish only conservatively gated AMFI cash-distribution candidates."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def normalize(self) -> DistributionNormalizationResult:
        run = DistributionNormalizationRunRecord(
            status="running",
            normalization_version=DISTRIBUTION_NORMALIZATION_VERSION,
        )
        self._session.add(run)
        self._session.commit()
        try:
            result = self._normalize(run)
            self._session.commit()
        except (Exception, KeyboardInterrupt) as error:
            self._session.rollback()
            failed_run = self._session.get(DistributionNormalizationRunRecord, run.id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.completed_at = utc_now()
                error_message = str(error) or "interrupted by user"
                failed_run.error_details = f"{type(error).__name__}: {error_message}"[:4000]
                self._session.commit()
            raise
        logger.info(
            "distribution_normalization_completed",
            extra={"event_data": result.__dict__},
        )
        return result

    def _normalize(
        self, run: DistributionNormalizationRunRecord
    ) -> DistributionNormalizationResult:
        exact_option_ids = set(
            self._session.scalars(select(SchemeOptionRecord.amfi_scheme_code)).all()
        )
        source_records = self._session.scalars(
            select(AmfiDistributionRecord).order_by(
                AmfiDistributionRecord.observed_at,
                AmfiDistributionRecord.id,
            )
        ).all()
        assessments = {
            record.id: self._assess(record, exact_option_ids) for record in source_records
        }
        self._validate_event_key_categories(source_records, assessments, exact_option_ids)

        source_links = {
            link.source_distribution_record_id: link
            for link in self._session.scalars(select(DistributionEventRevisionSourceRecord)).all()
        }
        for source_id in source_links:
            if assessments.get(source_id) != "cash_amount_candidate":
                raise RuntimeError(
                    "previously normalized source row is no longer a cash amount candidate: "
                    f"{source_id}"
                )

        events = {
            (event.amfi_scheme_code, event.record_date, event.event_type): event
            for event in self._session.scalars(select(DistributionEventRecord)).all()
        }
        current_revisions = {
            revision.distribution_event_id: revision
            for revision in self._session.scalars(
                select(DistributionEventRevisionRecord).where(
                    DistributionEventRevisionRecord.is_current.is_(True)
                )
            ).all()
        }

        candidates = tuple(
            record for record in source_records if assessments[record.id] == "cash_amount_candidate"
        )
        events_inserted = revisions_inserted = rows_unchanged = 0
        now = utc_now()
        for source in candidates:
            if source.id in source_links:
                rows_unchanged += 1
                continue
            if source.source_value is None or source.source_value <= 0:
                raise RuntimeError(f"candidate source row {source.id} has no positive amount")

            event_key = (source.source_option_id, source.record_date, _EVENT_TYPE)
            event = events.get(event_key)
            if event is None:
                event = DistributionEventRecord(
                    id=new_id(),
                    amfi_scheme_code=source.source_option_id,
                    record_date=source.record_date,
                    event_type=_EVENT_TYPE,
                    created_at=now,
                )
                self._session.add(event)
                events[event_key] = event
                events_inserted += 1

            current = current_revisions.get(event.id)
            if current is not None and current.amount_per_unit_inr == source.source_value:
                revision = current
                rows_unchanged += 1
            else:
                latest_revision_number = (
                    current.revision_number
                    if current is not None
                    else (
                        self._session.scalar(
                            select(func.max(DistributionEventRevisionRecord.revision_number)).where(
                                DistributionEventRevisionRecord.distribution_event_id == event.id
                            )
                        )
                        or 0
                    )
                )
                revision_number = latest_revision_number + 1
                if current is not None:
                    current.is_current = False
                    # Retire the prior row before inserting its replacement so the partial
                    # unique current-revision index is never transiently violated.
                    self._session.flush()
                revision = DistributionEventRevisionRecord(
                    id=new_id(),
                    distribution_event_id=event.id,
                    amount_per_unit_inr=source.source_value,
                    revision_number=revision_number,
                    content_signature=self._revision_signature(
                        source.source_option_id,
                        source.record_date.isoformat(),
                        source.source_value,
                        revision_number,
                    ),
                    normalization_version=DISTRIBUTION_NORMALIZATION_VERSION,
                    normalization_run_id=run.id,
                    is_current=True,
                    normalized_at=now,
                )
                self._session.add(revision)
                current_revisions[event.id] = revision
                revisions_inserted += 1

            self._session.add(
                DistributionEventRevisionSourceRecord(
                    distribution_event_revision_id=revision.id,
                    source_distribution_record_id=source.id,
                    linked_at=now,
                )
            )

        run.status = "completed"
        run.source_rows_examined = len(source_records)
        run.candidate_rows = len(candidates)
        run.blocked_rows = len(source_records) - len(candidates)
        run.events_inserted = events_inserted
        run.revisions_inserted = revisions_inserted
        run.rows_unchanged = rows_unchanged
        run.completed_at = utc_now()
        return DistributionNormalizationResult(
            run_id=run.id,
            status=run.status,
            normalization_version=run.normalization_version,
            source_rows_examined=run.source_rows_examined,
            candidate_rows=run.candidate_rows,
            blocked_rows=run.blocked_rows,
            events_inserted=run.events_inserted,
            revisions_inserted=run.revisions_inserted,
            rows_unchanged=run.rows_unchanged,
        )

    @staticmethod
    def _assess(
        record: AmfiDistributionRecord, exact_option_ids: set[str]
    ) -> DistributionNormalizationGateCategory:
        return classify_distribution_record(
            source_option_is_exact=record.source_option_id in exact_option_ids,
            source_unit=record.source_unit,
            source_value=record.source_value,
            scheme_name=record.scheme_name,
            nav_name=record.nav_name,
        )

    @staticmethod
    def _validate_event_key_categories(
        records: Sequence[AmfiDistributionRecord],
        assessments: dict[str, DistributionNormalizationGateCategory],
        exact_option_ids: set[str],
    ) -> None:
        categories_by_key: dict[tuple[str, date], set[str]] = {}
        for record in records:
            if record.source_option_id not in exact_option_ids:
                continue
            key = (record.source_option_id, record.record_date)
            categories_by_key.setdefault(key, set()).add(assessments[record.id])
        for key, categories in categories_by_key.items():
            if "cash_amount_candidate" in categories and len(categories) > 1:
                raise RuntimeError(
                    "candidate and blocked source rows share a distribution event identity: "
                    f"option={key[0]}, record_date={key[1]}, categories={sorted(categories)}"
                )

    @staticmethod
    def _revision_signature(
        scheme_code: str, record_date: str, amount: Decimal, revision_number: int
    ) -> str:
        serialized = json.dumps(
            (scheme_code, record_date, _EVENT_TYPE, str(amount), revision_number),
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
