from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mf_strategy_tester.db.models import (
    SchemeClassificationAliasRecord,
    SchemeClassificationRecord,
    SchemeMetadataVersionRecord,
    utc_now,
)

CLASSIFICATION_MAPPING_VERSION = "amfi-classification-reference-2026.09.1"
SchemeStructure = Literal["open_ended", "close_ended", "interval", "other"]
SchemeProductType = Literal["mutual_fund", "index_fund", "etf"]

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STRUCTURE_PATTERNS = {
    "open_ended": "open ended",
    "close_ended": "close ended",
    "interval": "interval",
}
_ASSET_CLASS_PATTERNS = {
    "equity": "equity",
    "debt": "debt",
    "hybrid": "hybrid",
    "solution_oriented": "solution oriented",
    "other": "other scheme",
}
_SIMILARITY_STOP_WORDS = frozenset(
    {"open", "close", "ended", "scheme", "schemes", "fund", "funds", "oriented"}
)


@dataclass(frozen=True)
class ClassificationDefinition:
    classification_id: str
    display_name: str
    normalized_key: str
    match_type: str
    evidence_note: str


@dataclass(frozen=True)
class ClassificationMappingProposal:
    source_classification_id: str
    source_display_name: str
    candidate_classification_id: str
    candidate_display_name: str
    lexical_score: float
    token_score: float
    evidence: str


@dataclass(frozen=True)
class ClassificationReferenceReport:
    mapping_version: str
    source_labels: int
    canonical_classifications: int
    approved_aliases: int
    alias_match_counts: dict[str, int]
    unmapped_source_labels: tuple[str, ...]
    proposals: tuple[ClassificationMappingProposal, ...]


def canonical_scheme_classification(value: str) -> str:
    """Collapse explicitly approved presentation drift without mutating source text."""
    normalized = " ".join(value.split())
    normalized = re.sub(r"\s*\(\s*", " ( ", normalized)
    normalized = re.sub(r"\s*\)\s*", " )", normalized)
    normalized = " ".join(normalized.split())
    normalized = re.sub(
        r"\b(Equity|Hybrid) Schemes(?=\s*-)",
        r"\1 Scheme",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\bMidcap\b", "Mid Cap", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\bLarge\s+and\s+Mid\s+Cap\b",
        "Large & Mid Cap",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def scheme_classification_key(value: str) -> str:
    return canonical_scheme_classification(value).casefold()


def scheme_classification_structure(value: str) -> SchemeStructure:
    """Return the AMFI top-level structure without guessing unknown classification families."""
    normalized = canonical_scheme_classification(value).casefold()
    if normalized.startswith("open ended schemes"):
        return "open_ended"
    if normalized.startswith("close ended schemes"):
        return "close_ended"
    if normalized.startswith("interval fund schemes"):
        return "interval"
    return "other"


def scheme_classification_product_type(value: str) -> SchemeProductType:
    """Classify an audited AMFI classification for screener navigation only."""
    normalized = canonical_scheme_classification(value).casefold()
    if (
        "exchange traded fund" in normalized
        or "gold etf" in normalized
        or "other etf" in normalized
        or "other  etf" in normalized
    ):
        return "etf"
    if "index fund" in normalized:
        return "index_fund"
    return "mutual_fund"


def classification_definition(raw_classification: str) -> ClassificationDefinition:
    if not raw_classification.strip():
        raise ValueError("scheme classification cannot be blank")
    display_name = canonical_scheme_classification(raw_classification)
    normalized_key = display_name.casefold()
    classification_id = f"amfi-{hashlib.sha256(normalized_key.encode('utf-8')).hexdigest()[:32]}"
    if raw_classification == display_name:
        match_type = "exact"
        evidence = "raw AMFI label already equals the canonical display label"
    elif _has_terminology_change(raw_classification, display_name):
        match_type = "terminology"
        evidence = (
            "approved deterministic terminology equivalence: Scheme/Schemes, Midcap/Mid Cap, "
            "or and/&; all other normalized classification tokens are identical"
        )
    else:
        match_type = "formatting"
        evidence = "approved deterministic whitespace, capitalization, or parenthesis normalization"
    return ClassificationDefinition(
        classification_id=classification_id,
        display_name=display_name,
        normalized_key=normalized_key,
        match_type=match_type,
        evidence_note=evidence,
    )


class ClassificationReferenceService:
    """Maintain audited aliases and produce non-mutating similarity review proposals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_approved_aliases(self, raw_classifications: Iterable[str]) -> None:
        raw_labels = tuple(sorted(set(raw_classifications)))
        if not raw_labels:
            return
        existing_aliases = {
            alias.raw_classification: alias
            for alias in self._session.scalars(
                select(SchemeClassificationAliasRecord).where(
                    SchemeClassificationAliasRecord.source_provider == "amfi",
                    SchemeClassificationAliasRecord.raw_classification.in_(raw_labels),
                )
            ).all()
        }
        missing_definitions = {
            raw: classification_definition(raw) for raw in raw_labels if raw not in existing_aliases
        }
        canonical_ids = {item.classification_id for item in missing_definitions.values()}
        existing_canonical_ids = set(
            self._session.scalars(
                select(SchemeClassificationRecord.id).where(
                    SchemeClassificationRecord.id.in_(canonical_ids)
                )
            ).all()
        )
        now = utc_now()
        created_classifications: list[SchemeClassificationRecord] = []
        for definition in missing_definitions.values():
            if definition.classification_id in existing_canonical_ids:
                continue
            classification = SchemeClassificationRecord(
                id=definition.classification_id,
                display_name=definition.display_name,
                normalized_key=definition.normalized_key,
                mapping_version=CLASSIFICATION_MAPPING_VERSION,
                status="active",
                created_at=now,
                updated_at=now,
            )
            self._session.add(classification)
            created_classifications.append(classification)
            existing_canonical_ids.add(definition.classification_id)
        self._session.flush()
        # Import locally to keep the source-normalization and user-facing alias layers separate.
        from mf_strategy_tester.services.screener_classification_alias import (
            register_screener_alias_for_classification,
        )

        for classification in created_classifications:
            register_screener_alias_for_classification(self._session, classification)
        for raw, definition in missing_definitions.items():
            self._session.add(
                SchemeClassificationAliasRecord(
                    source_provider="amfi",
                    raw_classification=raw,
                    classification_id=definition.classification_id,
                    match_type=definition.match_type,
                    mapping_version=CLASSIFICATION_MAPPING_VERSION,
                    evidence_note=definition.evidence_note,
                    approved_at=now,
                )
            )
        self._session.flush()

    def build_report(self, *, proposal_threshold: float = 0.82) -> ClassificationReferenceReport:
        if not 0 <= proposal_threshold <= 1:
            raise ValueError("proposal threshold must be between zero and one")
        source_labels = tuple(
            self._session.scalars(
                select(SchemeMetadataVersionRecord.scheme_classification).distinct()
            ).all()
        )
        aliases = tuple(self._session.scalars(select(SchemeClassificationAliasRecord)).all())
        classifications = tuple(
            self._session.scalars(
                select(SchemeClassificationRecord).where(
                    SchemeClassificationRecord.status == "active"
                )
            ).all()
        )
        mapped_labels = {alias.raw_classification for alias in aliases}
        proposals = _similarity_proposals(classifications, threshold=proposal_threshold)
        return ClassificationReferenceReport(
            mapping_version=CLASSIFICATION_MAPPING_VERSION,
            source_labels=len(source_labels),
            canonical_classifications=len(classifications),
            approved_aliases=len(aliases),
            alias_match_counts=dict(sorted(Counter(alias.match_type for alias in aliases).items())),
            unmapped_source_labels=tuple(sorted(set(source_labels) - mapped_labels)),
            proposals=proposals,
        )


def _has_terminology_change(raw: str, canonical: str) -> bool:
    lowered_raw = raw.casefold()
    lowered_canonical = canonical.casefold()
    return any(
        marker in lowered_raw or marker in lowered_canonical
        for marker in ("equity schemes -", "hybrid schemes -", "midcap", " large and mid cap")
    )


def _similarity_proposals(
    classifications: tuple[SchemeClassificationRecord, ...], *, threshold: float
) -> tuple[ClassificationMappingProposal, ...]:
    proposals: list[ClassificationMappingProposal] = []
    ordered = sorted(classifications, key=lambda item: item.id)
    for index, source in enumerate(ordered):
        for candidate in ordered[index + 1 :]:
            if _hard_classification_conflict(source.display_name, candidate.display_name):
                continue
            source_text = _comparison_text(source.display_name)
            candidate_text = _comparison_text(candidate.display_name)
            lexical_score = SequenceMatcher(None, source_text, candidate_text).ratio()
            source_tokens = set(source_text.split())
            candidate_tokens = set(candidate_text.split())
            token_score = len(source_tokens & candidate_tokens) / max(
                len(source_tokens | candidate_tokens), 1
            )
            score = max(lexical_score, token_score)
            if score < threshold:
                continue
            proposals.append(
                ClassificationMappingProposal(
                    source_classification_id=source.id,
                    source_display_name=source.display_name,
                    candidate_classification_id=candidate.id,
                    candidate_display_name=candidate.display_name,
                    lexical_score=round(lexical_score, 6),
                    token_score=round(token_score, 6),
                    evidence=(
                        "similarity proposal only; no alias was changed and semantic review "
                        "is required"
                    ),
                )
            )
    return tuple(
        sorted(
            proposals,
            key=lambda item: (
                -max(item.lexical_score, item.token_score),
                item.source_display_name,
                item.candidate_display_name,
            ),
        )
    )


def _hard_classification_conflict(left: str, right: str) -> bool:
    left_structure, left_asset = _classification_components(left)
    right_structure, right_asset = _classification_components(right)
    return bool(
        (left_structure and right_structure and left_structure != right_structure)
        or (left_asset and right_asset and left_asset != right_asset)
    )


def _classification_components(value: str) -> tuple[str | None, str | None]:
    lowered = value.casefold()
    structure = next(
        (name for name, marker in _STRUCTURE_PATTERNS.items() if marker in lowered), None
    )
    asset_class = next(
        (name for name, marker in _ASSET_CLASS_PATTERNS.items() if marker in lowered), None
    )
    return structure, asset_class


def _comparison_text(value: str) -> str:
    tokens = (
        token
        for token in _TOKEN_PATTERN.findall(value.casefold())
        if token not in _SIMILARITY_STOP_WORDS
    )
    return " ".join(tokens)
