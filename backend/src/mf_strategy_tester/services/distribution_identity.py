from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AmfiDistributionRecord,
    AmfiDistributionRecordSource,
    DistributionIdentifierReviewRecord,
    IngestionBatchRecord,
    SchemeOptionRecord,
)

REVIEW_STATUSES = frozenset({"source_only", "mapped", "source_error"})
SOURCE_ONLY_DETAILS = (
    "Observed as an exact SD_ID in an immutable official AMFI scheme-dividend artifact; "
    "no identical AMFI scheme code exists in the ingested NAV option universe. This review "
    "does not infer an alias from names."
)


@dataclass(frozen=True)
class DistributionIdentifierReviewResult:
    identifiers_reviewed: int
    reviews_inserted: int
    reviews_unchanged: int


class DistributionIdentifierReviewService:
    """Append evidence-backed reviews without rewriting earlier identity conclusions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def survey_unmatched_source_identifiers(self) -> DistributionIdentifierReviewResult:
        rows = self._session.execute(
            select(
                AmfiDistributionRecord.source_option_id,
                AmfiDistributionRecord.first_observed_batch_id,
            )
            .outerjoin(
                SchemeOptionRecord,
                SchemeOptionRecord.amfi_scheme_code == AmfiDistributionRecord.source_option_id,
            )
            .where(SchemeOptionRecord.amfi_scheme_code.is_(None))
            .order_by(
                AmfiDistributionRecord.source_option_id,
                AmfiDistributionRecord.observed_at,
                AmfiDistributionRecord.id,
            )
        ).all()
        evidence_by_identifier: dict[str, str] = {}
        for source_option_id, evidence_batch_id in rows:
            evidence_by_identifier.setdefault(source_option_id, evidence_batch_id)

        inserted = unchanged = 0
        for source_option_id, evidence_batch_id in evidence_by_identifier.items():
            _, was_inserted = self.record_review(
                source_option_id=source_option_id,
                status="source_only",
                evidence_batch_id=evidence_batch_id,
                evidence_details=SOURCE_ONLY_DETAILS,
                commit=False,
            )
            inserted += was_inserted
            unchanged += not was_inserted
        self._session.commit()
        return DistributionIdentifierReviewResult(
            identifiers_reviewed=len(evidence_by_identifier),
            reviews_inserted=inserted,
            reviews_unchanged=unchanged,
        )

    def record_review(
        self,
        *,
        source_option_id: str,
        status: str,
        evidence_batch_id: str,
        evidence_details: str,
        matched_amfi_scheme_code: str | None = None,
        commit: bool = True,
    ) -> tuple[DistributionIdentifierReviewRecord, bool]:
        self._validate_review(
            source_option_id=source_option_id,
            status=status,
            evidence_batch_id=evidence_batch_id,
            evidence_details=evidence_details,
            matched_amfi_scheme_code=matched_amfi_scheme_code,
        )
        normalized_details = evidence_details.strip()
        signature = _review_signature(
            source_option_id,
            status,
            matched_amfi_scheme_code,
            evidence_batch_id,
            normalized_details,
        )
        existing = self._session.scalar(
            select(DistributionIdentifierReviewRecord).where(
                DistributionIdentifierReviewRecord.review_signature == signature
            )
        )
        if existing is not None:
            return existing, False
        review = DistributionIdentifierReviewRecord(
            source_option_id=source_option_id,
            status=status,
            matched_amfi_scheme_code=matched_amfi_scheme_code,
            evidence_batch_id=evidence_batch_id,
            evidence_details=normalized_details,
            review_signature=signature,
        )
        self._session.add(review)
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        return review, True

    def _validate_review(
        self,
        *,
        source_option_id: str,
        status: str,
        evidence_batch_id: str,
        evidence_details: str,
        matched_amfi_scheme_code: str | None,
    ) -> None:
        if not source_option_id.isdigit():
            raise ValueError("distribution source option ID must be numeric")
        if status not in REVIEW_STATUSES:
            raise ValueError(f"unsupported distribution identifier review status {status!r}")
        if not evidence_details.strip():
            raise ValueError("distribution identifier evidence details cannot be empty")
        source_exists = self._session.scalar(
            select(AmfiDistributionRecord.id)
            .where(AmfiDistributionRecord.source_option_id == source_option_id)
            .limit(1)
        )
        if source_exists is None:
            raise LookupError(f"distribution source option {source_option_id} does not exist")
        evidence_batch = self._session.get(IngestionBatchRecord, evidence_batch_id)
        if evidence_batch is None:
            raise LookupError(f"evidence ingestion batch {evidence_batch_id} does not exist")
        if evidence_batch.status != "completed" or evidence_batch.artifact_id is None:
            raise ValueError("evidence batch must be completed and linked to an immutable artifact")
        if status == "source_only":
            if evidence_batch.provider != "amfi" or evidence_batch.source_type != "distributions":
                raise ValueError(
                    "a source-only review requires an official AMFI distribution batch"
                )
            evidence_contains_identifier = self._session.scalar(
                select(AmfiDistributionRecordSource.distribution_record_id)
                .join(
                    AmfiDistributionRecord,
                    AmfiDistributionRecord.id
                    == AmfiDistributionRecordSource.distribution_record_id,
                )
                .where(
                    AmfiDistributionRecordSource.ingestion_batch_id == evidence_batch_id,
                    AmfiDistributionRecord.source_option_id == source_option_id,
                )
                .limit(1)
            )
            if evidence_contains_identifier is None:
                raise ValueError(
                    "source-only evidence batch does not contain the distribution identifier"
                )
        elif evidence_batch.source_type == "distributions":
            raise ValueError(
                "an AMFI distribution artifact proves source occurrence, not an identity "
                "mapping or source error"
            )
        if status == "mapped":
            if matched_amfi_scheme_code is None:
                raise ValueError("a mapped review requires an AMFI scheme code")
            if self._session.get(SchemeOptionRecord, matched_amfi_scheme_code) is None:
                raise LookupError(
                    f"mapped AMFI scheme code {matched_amfi_scheme_code} does not exist"
                )
        elif matched_amfi_scheme_code is not None:
            raise ValueError(f"review status {status!r} cannot include a mapped scheme code")


def _review_signature(
    source_option_id: str,
    status: str,
    matched_amfi_scheme_code: str | None,
    evidence_batch_id: str,
    evidence_details: str,
) -> str:
    serialized = json.dumps(
        (
            source_option_id,
            status,
            matched_amfi_scheme_code,
            evidence_batch_id,
            evidence_details,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()
