from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, insert, select, tuple_
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    AmfiFundRecord,
    DataQualityIssueRecord,
    IngestionBatchRecord,
    NavDatasetStatsRecord,
    NavRevisionRecord,
    NavRevisionSourceRecord,
    SchemeMetadataVersionRecord,
    SchemeOptionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.ingestion.amfi import (
    FundSourceRecord,
    InvalidNavSourceRecord,
    NavParsedRecord,
    NavSourceRecord,
)

_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass(frozen=True)
class NavPublicationStats:
    rows_received: int
    rows_inserted: int
    rows_unchanged: int
    rows_revised: int
    rows_quarantined: int
    latest_nav_date: date | None


class FundCatalogPublisher:
    """Publish a structurally validated AMFI fund catalog as a current snapshot."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(self, records: tuple[FundSourceRecord, ...], *, batch_id: str) -> None:
        now = utc_now()
        observed_ids = {record.mutual_fund_id for record in records}
        existing = {
            record.mutual_fund_id: record
            for record in self._session.scalars(select(AmfiFundRecord)).all()
        }
        for source in records:
            record = existing.get(source.mutual_fund_id)
            if record is None:
                self._session.add(
                    AmfiFundRecord(
                        mutual_fund_id=source.mutual_fund_id,
                        mutual_fund_name=source.mutual_fund_name,
                        is_active=True,
                        catalog_batch_id=batch_id,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
            else:
                record.mutual_fund_name = source.mutual_fund_name
                record.is_active = True
                record.catalog_batch_id = batch_id
                record.last_seen_at = now
        for identifier, record in existing.items():
            if identifier not in observed_ids:
                record.is_active = False
        self._session.commit()


class NormalizedNavPublisher:
    """Publish immutable AMFI NAV revisions while retaining source lineage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(
        self, records: tuple[NavParsedRecord, ...], *, batch_id: str
    ) -> NavPublicationStats:
        if not records:
            return NavPublicationStats(0, 0, 0, 0, 0, None)
        new_scheme_options = self._ensure_scheme_options(records, batch_id)
        metadata_ids = self._ensure_metadata_versions(records, batch_id)
        valid_records = tuple(record for record in records if isinstance(record, NavSourceRecord))
        current = self._current_revisions(valid_records)
        dataset_stats = self._dataset_stats()
        dataset_stats.scheme_options += new_scheme_options

        inserted = unchanged = revised = 0
        quarantined = len(records) - len(valid_records)
        recalculate_valid_bounds = False
        pending_revisions: list[dict[str, object]] = []
        revision_sources: list[dict[str, object]] = []
        for record in records:
            key = (record.scheme_code, record.nav_date)
            metadata_id = metadata_ids[self._metadata_signature(record)]
            if isinstance(record, InvalidNavSourceRecord):
                self._session.add(
                    DataQualityIssueRecord(
                        ingestion_batch_id=batch_id,
                        issue_code="INVALID_NAV_VALUE",
                        severity="error",
                        amfi_scheme_code=record.scheme_code,
                        nav_date=record.nav_date,
                        metadata_version_id=metadata_id,
                        details=(
                            f"AMFI supplied NAV {record.raw_nav_value!r}: {record.rejection_reason}"
                        ),
                    )
                )
                self._add_isin_warnings(record, metadata_id, batch_id)
                continue
            quality_status = "valid" if record.nav > 0 else "error"
            signature = self._content_signature(record, quality_status)
            existing = current.get(key)
            if quality_status == "error":
                quarantined += 1
                self._session.add(
                    DataQualityIssueRecord(
                        ingestion_batch_id=batch_id,
                        issue_code="NON_POSITIVE_NAV",
                        severity="error",
                        amfi_scheme_code=record.scheme_code,
                        nav_date=record.nav_date,
                        metadata_version_id=metadata_id,
                        details=f"AMFI supplied non-positive NAV {record.nav}",
                    )
                )
            self._add_isin_warnings(record, metadata_id, batch_id)
            if (
                existing is not None
                and existing.nav_value == record.nav
                and existing.quality_status == quality_status
            ):
                unchanged += 1
                revision_sources.append(
                    {
                        "nav_revision_id": existing.id,
                        "ingestion_batch_id": batch_id,
                        "observed_metadata_version_id": metadata_id,
                        "observed_at": utc_now(),
                    }
                )
                continue
            if existing is not None:
                existing.is_current = False
                revision_number = existing.revision_number + 1
                revised += 1
                if existing.quality_status != quality_status:
                    if existing.quality_status == "valid":
                        dataset_stats.valid_current_rows -= 1
                        dataset_stats.error_current_rows += 1
                        recalculate_valid_bounds = True
                    else:
                        dataset_stats.error_current_rows -= 1
                        dataset_stats.valid_current_rows += 1
            else:
                revision_number = 1
                inserted += 1
                if quality_status == "valid":
                    dataset_stats.valid_current_rows += 1
                else:
                    dataset_stats.error_current_rows += 1
            revision_id = new_id()
            pending_revisions.append(
                {
                    "id": revision_id,
                    "amfi_scheme_code": record.scheme_code,
                    "nav_date": record.nav_date,
                    "nav_value": record.nav,
                    "metadata_version_id": metadata_id,
                    "revision_number": revision_number,
                    "content_signature": signature,
                    "quality_status": quality_status,
                    "is_current": True,
                    "first_observed_batch_id": batch_id,
                    "observed_at": utc_now(),
                }
            )
            revision_sources.append(
                {
                    "nav_revision_id": revision_id,
                    "ingestion_batch_id": batch_id,
                    "observed_metadata_version_id": metadata_id,
                    "observed_at": utc_now(),
                }
            )
            if quality_status == "valid":
                dataset_stats.earliest_valid_nav_date = min(
                    dataset_stats.earliest_valid_nav_date or record.nav_date, record.nav_date
                )
                dataset_stats.latest_valid_nav_date = max(
                    dataset_stats.latest_valid_nav_date or record.nav_date, record.nav_date
                )

        # Retire prior rows before inserting replacements so the partial unique
        # current-row index is never transiently violated.
        self._session.flush()
        if pending_revisions:
            self._session.execute(insert(NavRevisionRecord), pending_revisions)
        if recalculate_valid_bounds:
            bounds = self._session.execute(
                select(
                    func.min(NavRevisionRecord.nav_date), func.max(NavRevisionRecord.nav_date)
                ).where(
                    NavRevisionRecord.is_current.is_(True),
                    NavRevisionRecord.quality_status == "valid",
                )
            ).one()
            dataset_stats.earliest_valid_nav_date = bounds[0]
            dataset_stats.latest_valid_nav_date = bounds[1]
        dataset_stats.updated_at = utc_now()
        if revision_sources:
            self._session.execute(insert(NavRevisionSourceRecord), revision_sources)
        batch = self._session.get(IngestionBatchRecord, batch_id)
        if batch is None:
            raise LookupError(f"ingestion batch {batch_id} does not exist")
        batch.rows_accepted = len(records) - quarantined
        batch.rows_rejected = quarantined
        self._session.commit()
        return NavPublicationStats(
            rows_received=len(records),
            rows_inserted=inserted,
            rows_unchanged=unchanged,
            rows_revised=revised,
            rows_quarantined=quarantined,
            latest_nav_date=(
                max(record.nav_date for record in valid_records) if valid_records else None
            ),
        )

    def _ensure_scheme_options(self, records: tuple[NavParsedRecord, ...], batch_id: str) -> int:
        dates_by_code: dict[str, tuple[date, date]] = {}
        for record in records:
            bounds = dates_by_code.get(record.scheme_code)
            if bounds is None:
                dates_by_code[record.scheme_code] = (record.nav_date, record.nav_date)
            else:
                dates_by_code[record.scheme_code] = (
                    min(bounds[0], record.nav_date),
                    max(bounds[1], record.nav_date),
                )
        existing: dict[str, SchemeOptionRecord] = {}
        for codes in _chunks(tuple(dates_by_code), 900):
            existing.update(
                {
                    item.amfi_scheme_code: item
                    for item in self._session.scalars(
                        select(SchemeOptionRecord).where(
                            SchemeOptionRecord.amfi_scheme_code.in_(codes)
                        )
                    ).all()
                }
            )
        now = utc_now()
        new_count = 0
        for code, (first_date, last_date) in dates_by_code.items():
            option = existing.get(code)
            if option is None:
                new_count += 1
                self._session.add(
                    SchemeOptionRecord(
                        amfi_scheme_code=code,
                        first_observed_nav_date=first_date,
                        last_observed_nav_date=last_date,
                        first_observed_batch_id=batch_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                option.first_observed_nav_date = min(option.first_observed_nav_date, first_date)
                option.last_observed_nav_date = max(option.last_observed_nav_date, last_date)
                option.updated_at = now
        self._session.flush()
        return new_count

    def _dataset_stats(self) -> NavDatasetStatsRecord:
        stats = self._session.get(NavDatasetStatsRecord, 1)
        if stats is None:
            stats = NavDatasetStatsRecord(
                id=1,
                scheme_options=0,
                valid_current_rows=0,
                error_current_rows=0,
            )
            self._session.add(stats)
        return stats

    def _ensure_metadata_versions(
        self, records: tuple[NavParsedRecord, ...], batch_id: str
    ) -> dict[str, str]:
        sources = {self._metadata_signature(record): record for record in records}
        existing_ids: set[str] = set()
        for identifiers in _chunks(tuple(sources), 900):
            existing_ids.update(
                self._session.scalars(
                    select(SchemeMetadataVersionRecord.id).where(
                        SchemeMetadataVersionRecord.id.in_(identifiers)
                    )
                ).all()
            )
        for identifier, record in sources.items():
            if identifier in existing_ids:
                continue
            plan_type, option_type, classification_method = _classify_plan_option(record)
            self._session.add(
                SchemeMetadataVersionRecord(
                    id=identifier,
                    amfi_scheme_code=record.scheme_code,
                    scheme_name=record.scheme_name,
                    fund_house_name=record.fund_house,
                    scheme_classification=record.scheme_classification,
                    isin_payout_or_growth=record.isin_payout_or_growth,
                    isin_reinvestment=record.isin_reinvestment,
                    plan_type=plan_type,
                    option_type=option_type,
                    classification_method=classification_method,
                    first_observed_batch_id=batch_id,
                )
            )
        self._session.flush()
        return {identifier: identifier for identifier in sources}

    def _current_revisions(
        self, records: tuple[NavSourceRecord, ...]
    ) -> dict[tuple[str, date], NavRevisionRecord]:
        keys = tuple({(record.scheme_code, record.nav_date) for record in records})
        result: dict[tuple[str, date], NavRevisionRecord] = {}
        for key_chunk in _chunks(keys, 400):
            rows = self._session.scalars(
                select(NavRevisionRecord).where(
                    NavRevisionRecord.is_current.is_(True),
                    tuple_(NavRevisionRecord.amfi_scheme_code, NavRevisionRecord.nav_date).in_(
                        key_chunk
                    ),
                )
            ).all()
            result.update({(row.amfi_scheme_code, row.nav_date): row for row in rows})
        return result

    def _add_isin_warnings(self, record: NavParsedRecord, metadata_id: str, batch_id: str) -> None:
        for label, value in (
            ("payout_or_growth", record.isin_payout_or_growth),
            ("reinvestment", record.isin_reinvestment),
        ):
            if value is not None and _ISIN_PATTERN.fullmatch(value) is None:
                self._session.add(
                    DataQualityIssueRecord(
                        ingestion_batch_id=batch_id,
                        issue_code=f"INVALID_ISIN_{label.upper()}",
                        severity="warning",
                        amfi_scheme_code=record.scheme_code,
                        nav_date=record.nav_date,
                        metadata_version_id=metadata_id,
                        details=f"AMFI supplied non-standard {label} ISIN {value!r}",
                    )
                )

    @staticmethod
    def _metadata_signature(record: NavParsedRecord) -> str:
        plan_type, option_type, classification_method = _classify_plan_option(record)
        return _hash_values(
            record.scheme_code,
            record.scheme_name,
            record.fund_house,
            record.scheme_classification,
            record.isin_payout_or_growth,
            record.isin_reinvestment,
            plan_type,
            option_type,
            classification_method,
        )

    @staticmethod
    def _content_signature(record: NavSourceRecord, quality_status: str) -> str:
        return _hash_values(str(record.nav), quality_status)


def _infer_plan_option(scheme_name: str) -> tuple[str, str]:
    normalized = " ".join(scheme_name.lower().replace("-", " ").split())
    if re.search(r"\bdirect\b", normalized):
        plan = "direct"
    elif re.search(r"\bregular\b", normalized):
        plan = "regular"
    else:
        plan = "unknown"
    if re.search(r"\b(idcw|dividend)\b", normalized):
        option = "idcw"
    elif re.search(r"\bgrowth\b", normalized):
        option = "growth"
    elif re.search(r"\bbonus\b", normalized):
        option = "bonus"
    else:
        option = "unknown"
    return plan, option


def _classify_plan_option(record: NavParsedRecord) -> tuple[str, str, str]:
    inferred_plan, inferred_option = _infer_plan_option(record.scheme_name)
    plan = _normalize_explicit_plan(record.source_plan) if record.source_plan is not None else None
    option = (
        _normalize_explicit_option(record.source_option)
        if record.source_option is not None
        else None
    )
    methods = (
        "explicit" if record.source_plan is not None else "name_heuristic",
        "explicit" if record.source_option is not None else "name_heuristic",
    )
    return (
        plan if plan is not None else inferred_plan,
        option if option is not None else inferred_option,
        f"amfi_plan_{methods[0]}_option_{methods[1]}_v1",
    )


def _normalize_explicit_plan(source_plan: str) -> str:
    normalized = " ".join(source_plan.casefold().replace("-", " ").split())
    if re.search(r"\bdirect\b", normalized):
        return "direct"
    if re.search(r"\bregular\b", normalized):
        return "regular"
    return "unknown"


def _normalize_explicit_option(source_option: str) -> str:
    normalized = " ".join(source_option.casefold().replace("-", " ").split())
    if (
        re.search(r"\b(idcw|idwc|dcw|dividend)\b", normalized)
        or "income distribution" in normalized
    ):
        return "idcw"
    if re.search(r"\bgrowth\b", normalized):
        return "growth"
    if re.search(r"\bbonus\b", normalized):
        return "bonus"
    return "unknown"


def _hash_values(*values: object) -> str:
    serialized = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _chunks[T](values: tuple[T, ...], size: int) -> Iterator[tuple[T, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]
