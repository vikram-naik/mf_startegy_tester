from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    SchemeClassificationAliasRecord,
    SchemeClassificationRecord,
    ScreenerClassificationAliasMemberRecord,
    ScreenerClassificationAliasRecord,
    ScreenerClassificationAliasRevisionRecord,
    new_id,
    utc_now,
)
from mf_strategy_tester.services.classification_reference import (
    SchemeStructure,
    scheme_classification_structure,
)

AliasStatus = Literal["active", "inactive"]


@dataclass(frozen=True)
class DefaultAliasDefinition:
    alias_id: str
    name: str
    structure_type: SchemeStructure
    canonical_display_names: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationSelection:
    selection_id: str
    display_name: str
    structure_type: SchemeStructure
    mapping_version: str
    classification_ids: tuple[str, ...]


def _open(*labels: str) -> tuple[str, ...]:
    return tuple(f"Open Ended Schemes ( {label} )" for label in labels)


DEFAULT_ALIAS_DEFINITIONS: tuple[DefaultAliasDefinition, ...] = (
    DefaultAliasDefinition(
        "screener-open-debt-banking-psu",
        "Banking and PSU Debt",
        "open_ended",
        _open(
            "Debt Scheme - Banking and PSU Fund",
            "Income/Debt Oriented Schemes - Banking and PSU Debt Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-corporate-bond",
        "Corporate Bond",
        "open_ended",
        _open(
            "Debt Scheme - Corporate Bond Fund",
            "Income/Debt Oriented Schemes - Corporate Bond Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-credit-risk",
        "Credit Risk",
        "open_ended",
        _open(
            "Debt Scheme - Credit Risk Fund",
            "Income/Debt Oriented Schemes - Credit Risk Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-dynamic-term",
        "Dynamic Term",
        "open_ended",
        _open(
            "Debt Scheme - Dynamic Bond",
            "Income/Debt Oriented Schemes - Dynamic Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-floating-rate",
        "Floating Interest Rates",
        "open_ended",
        _open(
            "Debt Scheme - Floater Fund",
            "Income/Debt Oriented Schemes - Floating Interest Rates Fund",
            "Floating Rate",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-gilt",
        "Gilt",
        "open_ended",
        _open(
            "Debt Scheme - Gilt Fund",
            "Income/Debt Oriented Schemes - Gilt Fund",
            "Gilt",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-gilt-10-year",
        "10-year Constant Maturity Gilt",
        "open_ended",
        _open(
            "Debt Scheme - Gilt Fund with 10 year constant duration",
            "Income/Debt Oriented Schemes - 10-year Constant Maturity Gilt Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-liquid",
        "Liquid",
        "open_ended",
        _open(
            "Debt Scheme - Liquid Fund",
            "Income/Debt Oriented Schemes - Liquid Fund",
            "Liquid",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-long-term",
        "Long Term",
        "open_ended",
        _open(
            "Debt Scheme - Long Duration Fund",
            "Income/Debt Oriented Schemes - Long Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-ultra-short-to-short",
        "Ultra Short to Short Term",
        "open_ended",
        _open(
            "Debt Scheme - Low Duration Fund",
            "Income/Debt Oriented Schemes - Ultra Short to Short Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-medium-term",
        "Medium Term",
        "open_ended",
        _open(
            "Debt Scheme - Medium Duration Fund",
            "Income/Debt Oriented Schemes - Medium Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-medium-long-term",
        "Medium to Long Term",
        "open_ended",
        _open(
            "Debt Scheme - Medium to Long Duration Fund",
            "Income/Debt Oriented Schemes - Medium to Long Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-money-market",
        "Money Market",
        "open_ended",
        _open(
            "Debt Scheme - Money Market Fund",
            "Income/Debt Oriented Schemes - Money Market Fund",
            "Money Market",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-overnight",
        "Overnight",
        "open_ended",
        _open(
            "Debt Scheme - Overnight Fund",
            "Income/Debt Oriented Schemes - Overnight Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-short-term",
        "Short Term",
        "open_ended",
        _open(
            "Debt Scheme - Short Duration Fund",
            "Income/Debt Oriented Schemes - Short Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-debt-ultra-short-term",
        "Ultra Short Term",
        "open_ended",
        _open(
            "Debt Scheme - Ultra Short Duration Fund",
            "Income/Debt Oriented Schemes - Ultra Short Term Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-equity-elss",
        "ELSS - Tax Saver",
        "open_ended",
        _open(
            "Equity Scheme - ELSS",
            "Equity Scheme - ELSS- Tax Saver Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-equity-sectoral-thematic",
        "Sectoral / Thematic Equity",
        "open_ended",
        _open(
            "Equity Scheme - Sectoral Fund",
            "Equity Scheme - Sectoral/ Thematic",
            "Equity Scheme - Thematic Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-hybrid-dynamic-allocation",
        "Dynamic Asset Allocation",
        "open_ended",
        _open(
            "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage",
            "Hybrid Scheme - Balanced Advantage Fund/ Dynamic Asset Allocation",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-hybrid-equity-savings",
        "Equity Savings",
        "open_ended",
        _open(
            "Hybrid Scheme - Equity Savings",
            "Hybrid Scheme - Equity Savings Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-hybrid-multi-asset",
        "Multi Asset Allocation",
        "open_ended",
        _open(
            "Hybrid Scheme - Multi Asset Allocation",
            "Hybrid Scheme - Multi Asset Allocation Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-solution-childrens",
        "Children's",
        "open_ended",
        _open(
            "Children’s Fund - Childrens' Fund",  # noqa: RUF001 - exact AMFI label
            "Solution Oriented Scheme - Children’s Fund",  # noqa: RUF001 - exact AMFI label
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-solution-retirement",
        "Retirement",
        "open_ended",
        _open(
            "Solution Oriented Scheme - Retirement Fund",
            "Solution Oriented Schemes ** - Retirement Fund",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-fof-domestic",
        "FoF - Domestic",
        "open_ended",
        _open(
            "Fund of Funds - Domestic",
            "Fund of Funds Scheme ( Domestic )- Fund of Funds Scheme ( Domestic )",
            "Other Scheme - FoF Domestic",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-fof-overseas",
        "FoF - Overseas",
        "open_ended",
        _open(
            "Fund of Funds - Overseas",
            "Other Scheme - FoF Overseas",
            "Overseas Fund of Funds - Fund of Funds investing overseas",
        ),
    ),
    DefaultAliasDefinition(
        "screener-open-etf-gold",
        "Gold ETF",
        "open_ended",
        _open(
            "Exchange Traded Funds ( ETFs )- Gold ETF",
            "GOLD ETFs",
            "Other Scheme - Gold ETF",
        ),
    ),
)

_DEFAULT_BY_DISPLAY_NAME = {
    display_name: definition
    for definition in DEFAULT_ALIAS_DEFINITIONS
    for display_name in definition.canonical_display_names
}
_STRUCTURE_WRAPPER = re.compile(
    r"^(?:Open Ended Schemes|Close Ended Schemes|Interval Fund Schemes)\s*\(\s*(.*?)\s*\)$",
    flags=re.IGNORECASE,
)
_LEAF_PREFIXES = (
    "children",
    "debt scheme",
    "equity scheme",
    "exchange traded funds",
    "fund of funds scheme",
    "hybrid scheme",
    "income/debt oriented schemes",
    "index funds",
    "life cycle funds",
    "other scheme",
    "overseas fund of funds",
    "solution oriented scheme",
    "solution oriented schemes",
)


def normalize_alias_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def simple_classification_name(value: str) -> str:
    """Return a short fallback label while retaining the stored AMFI classification."""
    normalized = " ".join(value.split())
    wrapper = _STRUCTURE_WRAPPER.match(normalized)
    inner = wrapper.group(1) if wrapper else normalized
    for prefix in _LEAF_PREFIXES:
        if not inner.casefold().startswith(prefix):
            continue
        remainder = re.sub(r"^.*?\)\s*-\s*", "", inner, count=1)
        if remainder == inner:
            remainder = re.sub(r"^[^-]+\s+-\s+", "", inner, count=1)
        if remainder != inner:
            inner = remainder
        break
    inner = re.sub(r"\s*\(\s*", " (", inner)
    inner = re.sub(r"\s*\)\s*", ")", inner)
    return " ".join(inner.split())


def singleton_alias_id(classification_id: str) -> str:
    return f"screener-singleton-{classification_id.removeprefix('amfi-')}"


def register_screener_alias_for_classification(
    session: Session, classification: SchemeClassificationRecord
) -> None:
    """Ensure a new canonical classification has an explicit local screener alias."""
    existing_member = session.get(ScreenerClassificationAliasMemberRecord, classification.id)
    if existing_member is not None:
        return
    definition = _DEFAULT_BY_DISPLAY_NAME.get(classification.display_name)
    alias = (
        session.get(ScreenerClassificationAliasRecord, definition.alias_id)
        if definition is not None
        else None
    )
    now = utc_now()
    if definition is not None and alias is None:
        alias = ScreenerClassificationAliasRecord(
            id=definition.alias_id,
            name=definition.name,
            normalized_name=normalize_alias_name(definition.name),
            structure_type=definition.structure_type,
            status="active",
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(alias)
        session.flush()
        version = 1
        reason = "Default reviewed AMFI predecessor/successor mapping"
    elif alias is not None and alias.status == "active":
        alias.version += 1
        alias.updated_at = now
        version = alias.version
        reason = "Default reviewed AMFI predecessor/successor mapping"
    else:
        alias = ScreenerClassificationAliasRecord(
            id=singleton_alias_id(classification.id),
            name=_singleton_alias_name(session, classification),
            normalized_name="",
            structure_type=scheme_classification_structure(classification.display_name),
            status="active",
            version=1,
            created_at=now,
            updated_at=now,
        )
        alias.normalized_name = normalize_alias_name(alias.name)
        session.add(alias)
        session.flush()
        version = 1
        reason = "Automatic singleton alias for one canonical AMFI classification"
    session.add(
        ScreenerClassificationAliasMemberRecord(
            classification_id=classification.id,
            alias_id=alias.id,
            added_at=now,
        )
    )
    session.flush()
    _record_revision(
        session,
        alias,
        version=version,
        reason=reason,
    )


def _singleton_alias_name(session: Session, classification: SchemeClassificationRecord) -> str:
    structure_type = scheme_classification_structure(classification.display_name)
    base_name = simple_classification_name(classification.display_name)
    other_classifications = session.scalars(
        select(SchemeClassificationRecord).where(
            SchemeClassificationRecord.id != classification.id,
            SchemeClassificationRecord.status == "active",
        )
    ).all()
    has_canonical_name_collision = any(
        scheme_classification_structure(other.display_name) == structure_type
        and normalize_alias_name(simple_classification_name(other.display_name))
        == normalize_alias_name(base_name)
        for other in other_classifications
    )
    occupied_names = set(
        session.scalars(
            select(ScreenerClassificationAliasRecord.normalized_name).where(
                ScreenerClassificationAliasRecord.structure_type == structure_type
            )
        ).all()
    )
    contextual_name = _classification_context_name(classification.display_name)
    preferred_names = (
        (contextual_name, base_name)
        if has_canonical_name_collision
        else (base_name, contextual_name)
    )
    for preferred_name in preferred_names:
        candidate = preferred_name[:100].rstrip()
        if normalize_alias_name(candidate) not in occupied_names:
            return candidate
    suffix = f" [{classification.id[-8:]}]"
    return f"{contextual_name[: 100 - len(suffix)].rstrip()}{suffix}"


def _classification_context_name(value: str) -> str:
    normalized = " ".join(value.split())
    wrapper = _STRUCTURE_WRAPPER.match(normalized)
    inner = wrapper.group(1) if wrapper else normalized
    return " ".join(inner.split())


def alias_assignments(
    session: Session, classification_ids: set[str]
) -> dict[str, ScreenerClassificationAliasRecord]:
    if not classification_ids:
        return {}
    rows = session.execute(
        select(
            ScreenerClassificationAliasMemberRecord.classification_id,
            ScreenerClassificationAliasRecord,
        )
        .join(
            ScreenerClassificationAliasRecord,
            ScreenerClassificationAliasRecord.id
            == ScreenerClassificationAliasMemberRecord.alias_id,
        )
        .where(
            ScreenerClassificationAliasMemberRecord.classification_id.in_(classification_ids),
            ScreenerClassificationAliasRecord.status == "active",
        )
    ).all()
    return {classification_id: alias for classification_id, alias in rows}


def resolve_classification_selection(
    session: Session,
    *,
    classification_id: str | None,
    classification: str | None,
) -> ClassificationSelection | None:
    if classification_id is not None:
        alias = session.get(ScreenerClassificationAliasRecord, classification_id)
    elif classification is not None:
        alias = session.scalar(
            select(ScreenerClassificationAliasRecord)
            .where(
                ScreenerClassificationAliasRecord.normalized_name
                == normalize_alias_name(classification)
            )
            .limit(1)
        )
    else:
        alias = None
    if alias is not None and alias.status == "active":
        member_ids = tuple(
            session.scalars(
                select(ScreenerClassificationAliasMemberRecord.classification_id)
                .where(ScreenerClassificationAliasMemberRecord.alias_id == alias.id)
                .order_by(ScreenerClassificationAliasMemberRecord.classification_id)
            ).all()
        )
        if member_ids:
            return ClassificationSelection(
                selection_id=alias.id,
                display_name=alias.name,
                structure_type=alias.structure_type,  # type: ignore[arg-type]
                mapping_version=f"{alias.id}:v{alias.version}",
                classification_ids=member_ids,
            )

    record: SchemeClassificationRecord | None
    if classification_id is not None:
        record = session.get(SchemeClassificationRecord, classification_id)
    elif classification is not None:
        record = session.scalar(
            select(SchemeClassificationRecord)
            .where(SchemeClassificationRecord.display_name == classification)
            .limit(1)
        )
        if record is None:
            record = session.scalar(
                select(SchemeClassificationRecord)
                .join(
                    SchemeClassificationAliasRecord,
                    SchemeClassificationAliasRecord.classification_id
                    == SchemeClassificationRecord.id,
                )
                .where(
                    SchemeClassificationAliasRecord.source_provider == "amfi",
                    SchemeClassificationAliasRecord.raw_classification == classification,
                )
                .limit(1)
            )
    else:
        record = None
    if record is None or record.status != "active":
        return None
    assignment = alias_assignments(session, {record.id}).get(record.id)
    if assignment is not None:
        return resolve_classification_selection(
            session, classification_id=assignment.id, classification=None
        )
    return ClassificationSelection(
        selection_id=record.id,
        display_name=simple_classification_name(record.display_name),
        structure_type=scheme_classification_structure(record.display_name),
        mapping_version=record.mapping_version,
        classification_ids=(record.id,),
    )


def create_screener_alias(
    session: Session,
    *,
    name: str,
    structure_type: SchemeStructure,
    status: AliasStatus,
    classification_ids: tuple[str, ...],
    reason: str,
) -> ScreenerClassificationAliasRecord:
    cleaned_reason = _validate_change_reason(reason)
    alias = ScreenerClassificationAliasRecord(
        id=f"screener-{new_id()}",
        name=" ".join(name.split()),
        normalized_name=normalize_alias_name(name),
        structure_type=structure_type,
        status=status,
        version=1,
    )
    _validate_alias_change(
        session,
        alias_id=None,
        name=alias.name,
        structure_type=structure_type,
        status=status,
        classification_ids=classification_ids,
    )
    session.add(alias)
    session.flush()
    _replace_members(session, alias, classification_ids)
    _record_revision(session, alias, version=1, reason=cleaned_reason)
    session.commit()
    return alias


def update_screener_alias(
    session: Session,
    *,
    alias_id: str,
    expected_version: int,
    name: str,
    structure_type: SchemeStructure,
    status: AliasStatus,
    classification_ids: tuple[str, ...],
    reason: str,
) -> ScreenerClassificationAliasRecord:
    cleaned_reason = _validate_change_reason(reason)
    alias = session.get(ScreenerClassificationAliasRecord, alias_id)
    if alias is None:
        raise LookupError("classification alias not found")
    if alias.version != expected_version:
        raise RuntimeError(
            f"classification alias changed from version {expected_version} to {alias.version}"
        )
    cleaned_name = " ".join(name.split())
    _validate_alias_change(
        session,
        alias_id=alias.id,
        name=cleaned_name,
        structure_type=structure_type,
        status=status,
        classification_ids=classification_ids,
    )
    alias.name = cleaned_name
    alias.normalized_name = normalize_alias_name(cleaned_name)
    alias.structure_type = structure_type
    alias.status = status
    alias.version += 1
    alias.updated_at = utc_now()
    _replace_members(session, alias, classification_ids)
    _record_revision(session, alias, version=alias.version, reason=cleaned_reason)
    session.commit()
    return alias


def _validate_alias_change(
    session: Session,
    *,
    alias_id: str | None,
    name: str,
    structure_type: SchemeStructure,
    status: AliasStatus,
    classification_ids: tuple[str, ...],
) -> None:
    if not name:
        raise ValueError("classification alias name cannot be blank")
    if len(name) > 100:
        raise ValueError("classification alias name cannot exceed 100 characters")
    if status == "active" and not classification_ids:
        raise ValueError("active classification alias requires at least one AMFI classification")
    if status == "inactive" and classification_ids:
        raise ValueError("inactive classification alias must release all AMFI classifications")
    if len(classification_ids) > 200:
        raise ValueError("classification alias cannot contain more than 200 AMFI classifications")
    if len(set(classification_ids)) != len(classification_ids):
        raise ValueError("classification IDs must be unique")
    name_conflict = session.scalar(
        select(ScreenerClassificationAliasRecord.id)
        .where(
            ScreenerClassificationAliasRecord.structure_type == structure_type,
            ScreenerClassificationAliasRecord.normalized_name == normalize_alias_name(name),
            ScreenerClassificationAliasRecord.id != (alias_id or ""),
        )
        .limit(1)
    )
    if name_conflict is not None:
        raise ValueError("classification alias name already exists for this structure")
    classifications = tuple(
        session.scalars(
            select(SchemeClassificationRecord).where(
                SchemeClassificationRecord.id.in_(classification_ids),
                SchemeClassificationRecord.status == "active",
            )
        ).all()
    )
    found_ids = {item.id for item in classifications}
    missing = sorted(set(classification_ids) - found_ids)
    if missing:
        raise ValueError(f"unknown or inactive AMFI classifications: {missing}")
    mismatched = sorted(
        item.id
        for item in classifications
        if scheme_classification_structure(item.display_name) != structure_type
    )
    if mismatched:
        raise ValueError(f"classification structure does not match alias: {mismatched}")
    existing = session.execute(
        select(
            ScreenerClassificationAliasMemberRecord.classification_id,
            ScreenerClassificationAliasMemberRecord.alias_id,
        ).where(
            ScreenerClassificationAliasMemberRecord.classification_id.in_(classification_ids),
            ScreenerClassificationAliasMemberRecord.alias_id != (alias_id or ""),
        )
    ).all()
    if existing:
        conflicts = {
            classification_id: existing_alias for classification_id, existing_alias in existing
        }
        raise ValueError(f"AMFI classifications already mapped to another alias: {conflicts}")


def _validate_change_reason(reason: str) -> str:
    cleaned_reason = " ".join(reason.split())
    if len(cleaned_reason) < 3:
        raise ValueError("classification alias change reason must contain at least 3 characters")
    if len(cleaned_reason) > 500:
        raise ValueError("classification alias change reason cannot exceed 500 characters")
    return cleaned_reason


def _replace_members(
    session: Session,
    alias: ScreenerClassificationAliasRecord,
    classification_ids: tuple[str, ...],
) -> None:
    existing = {
        member.classification_id: member
        for member in session.scalars(
            select(ScreenerClassificationAliasMemberRecord).where(
                ScreenerClassificationAliasMemberRecord.alias_id == alias.id
            )
        ).all()
    }
    desired = set(classification_ids)
    for classification_id, member in existing.items():
        if classification_id not in desired:
            session.delete(member)
    now = utc_now()
    for classification_id in sorted(desired - existing.keys()):
        session.add(
            ScreenerClassificationAliasMemberRecord(
                classification_id=classification_id,
                alias_id=alias.id,
                added_at=now,
            )
        )
    session.flush()


def _record_revision(
    session: Session,
    alias: ScreenerClassificationAliasRecord,
    *,
    version: int,
    reason: str,
) -> None:
    cleaned_reason = _validate_change_reason(reason)
    member_ids = list(
        session.scalars(
            select(ScreenerClassificationAliasMemberRecord.classification_id)
            .where(ScreenerClassificationAliasMemberRecord.alias_id == alias.id)
            .order_by(ScreenerClassificationAliasMemberRecord.classification_id)
        ).all()
    )
    session.add(
        ScreenerClassificationAliasRevisionRecord(
            alias_id=alias.id,
            version=version,
            name=alias.name,
            normalized_name=alias.normalized_name,
            structure_type=alias.structure_type,
            status=alias.status,
            member_classification_ids=member_ids,
            change_reason=cleaned_reason,
        )
    )
    session.flush()
