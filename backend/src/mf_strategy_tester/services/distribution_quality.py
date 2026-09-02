from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

DistributionLabelClassification = Literal[
    "distribution",
    "bonus",
    "growth_or_cumulative",
    "bonus_or_distribution",
    "conflicting",
    "unknown",
]
DistributionNormalizationGateCategory = Literal[
    "cash_amount_candidate",
    "unmatched_option_identifier",
    "non_positive_scalar",
    "percentage_source_value",
    "ratio_source_value",
    "explicit_bonus_option",
    "explicit_growth_or_cumulative_option",
    "ambiguous_bonus_or_distribution_option",
    "conflicting_option_labels",
    "unknown_option_label",
]

DISTRIBUTION_NORMALIZATION_GATE_CATEGORIES: tuple[DistributionNormalizationGateCategory, ...] = (
    "cash_amount_candidate",
    "unmatched_option_identifier",
    "non_positive_scalar",
    "percentage_source_value",
    "ratio_source_value",
    "explicit_bonus_option",
    "explicit_growth_or_cumulative_option",
    "ambiguous_bonus_or_distribution_option",
    "conflicting_option_labels",
    "unknown_option_label",
)
_LABEL_GATE_CATEGORIES: dict[
    DistributionLabelClassification, DistributionNormalizationGateCategory
] = {
    "distribution": "cash_amount_candidate",
    "bonus": "explicit_bonus_option",
    "growth_or_cumulative": "explicit_growth_or_cumulative_option",
    "bonus_or_distribution": "ambiguous_bonus_or_distribution_option",
    "conflicting": "conflicting_option_labels",
    "unknown": "unknown_option_label",
}


@dataclass(frozen=True)
class DistributionLabelAssessment:
    classification: DistributionLabelClassification
    option_descriptor: str | None


def classify_distribution_record(
    *,
    source_option_is_exact: bool,
    source_unit: str,
    source_value: Decimal | None,
    scheme_name: str,
    nav_name: str,
) -> DistributionNormalizationGateCategory:
    """Apply the ordered, exhaustive normalization gate to one immutable source row."""

    if not source_option_is_exact:
        return "unmatched_option_identifier"
    if source_unit == "ratio":
        return "ratio_source_value"
    if source_value is None:
        raise RuntimeError("scalar distribution record has no source value")
    if source_value <= 0:
        return "non_positive_scalar"
    if source_unit == "percentage":
        return "percentage_source_value"
    if source_unit != "amount":
        raise RuntimeError(f"unsupported distribution source unit: {source_unit}")

    assessment = classify_distribution_label(scheme_name=scheme_name, nav_name=nav_name)
    return _LABEL_GATE_CATEGORIES[assessment.classification]


_WHITESPACE = re.compile(r"\s+")
_IDCW = re.compile(r"\bidcw\b", re.IGNORECASE)
_DISTRIBUTION_PHRASE = re.compile(
    r"\bincome\s+distribution\s+cum\s+capital\s+withdrawal\b", re.IGNORECASE
)
_DIVIDEND = re.compile(r"\bdividend\b", re.IGNORECASE)
_DIV_ABBREVIATION = re.compile(r"\bdiv\b", re.IGNORECASE)
_BONUS = re.compile(r"\bbonus\b", re.IGNORECASE)
_GROWTH = re.compile(r"\bgrowth\b", re.IGNORECASE)
_CUMULATIVE = re.compile(r"\bcumulative\b", re.IGNORECASE)
_TERMINAL_DIVIDEND = re.compile(
    r"(?:\bdiv(?:idend)?|\bidcw)(?:\s+(?:opt(?:ion)?|payout|reinvestment))?\s*$",
    re.IGNORECASE,
)
_TERMINAL_BONUS = re.compile(r"\bbonus(?:\s+option)?\s*$", re.IGNORECASE)
_TERMINAL_GROWTH = re.compile(r"\b(?:growth|cumulative)(?:\s+option)?\s*$", re.IGNORECASE)
_BONUS_DISTRIBUTION_PAIR = re.compile(r"\bbonus\b\s*/\s*(?:\bdividend\b|\bidcw\b)", re.IGNORECASE)
_EXPLICIT_DIVIDEND_OPTION = re.compile(
    r"\bdividend\s+(?:opt(?:ion)?|plan|payout|reinvestment)\b", re.IGNORECASE
)


def classify_distribution_label(*, scheme_name: str, nav_name: str) -> DistributionLabelAssessment:
    """Classify only option semantics explicitly present in an AMFI source label.

    The source scheme name is removed only when it is an exact normalized prefix of the NAV
    name. This prevents words such as "Growth" or "Dividend" in a fund's proper name from being
    interpreted as option semantics. When no option suffix can be isolated, only unambiguous
    IDCW terminology and terminal option markers are considered.
    """

    normalized_scheme = _normalize_label(scheme_name)
    normalized_nav = _normalize_label(nav_name)
    descriptor = _option_descriptor(normalized_scheme, normalized_nav)

    if descriptor is not None and descriptor:
        semantic_text = descriptor
        has_distribution = bool(
            _IDCW.search(semantic_text)
            or _DISTRIBUTION_PHRASE.search(semantic_text)
            or _DIVIDEND.search(semantic_text)
            or _DIV_ABBREVIATION.search(semantic_text)
        )
        has_bonus = bool(_BONUS.search(semantic_text))
        has_growth = bool(_GROWTH.search(semantic_text) or _CUMULATIVE.search(semantic_text))
    else:
        semantic_text = normalized_nav
        terminal_text = semantic_text.rstrip(" -:;/()[]")
        scheme_has_dividend_marker = bool(
            _DIVIDEND.search(normalized_scheme) or _DIV_ABBREVIATION.search(normalized_scheme)
        )
        nav_has_dividend_marker = bool(
            _DIVIDEND.search(semantic_text) or _DIV_ABBREVIATION.search(semantic_text)
        )
        explicit_pair = bool(_BONUS_DISTRIBUTION_PAIR.search(semantic_text))
        has_distribution = bool(
            _IDCW.search(semantic_text)
            or _DISTRIBUTION_PHRASE.search(semantic_text)
            or (nav_has_dividend_marker and not scheme_has_dividend_marker)
            or _TERMINAL_DIVIDEND.search(terminal_text)
            or explicit_pair
            or _EXPLICIT_DIVIDEND_OPTION.search(semantic_text)
        )
        has_bonus = bool(
            (_BONUS.search(semantic_text) and not _BONUS.search(normalized_scheme))
            or _TERMINAL_BONUS.search(terminal_text)
            or explicit_pair
        )
        has_growth = bool(
            (
                (_GROWTH.search(semantic_text) or _CUMULATIVE.search(semantic_text))
                and not (_GROWTH.search(normalized_scheme) or _CUMULATIVE.search(normalized_scheme))
            )
            or _TERMINAL_GROWTH.search(terminal_text)
        )

    if has_growth and (has_distribution or has_bonus):
        classification: DistributionLabelClassification = "conflicting"
    elif has_distribution and has_bonus:
        classification = "bonus_or_distribution"
    elif has_distribution:
        classification = "distribution"
    elif has_bonus:
        classification = "bonus"
    elif has_growth:
        classification = "growth_or_cumulative"
    else:
        classification = "unknown"
    return DistributionLabelAssessment(
        classification=classification,
        option_descriptor=descriptor or None,
    )


def _normalize_label(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _option_descriptor(scheme_name: str, nav_name: str) -> str | None:
    if not scheme_name or not nav_name.casefold().startswith(scheme_name.casefold()):
        return None
    if len(nav_name) > len(scheme_name) and nav_name[len(scheme_name)].isalnum():
        return None
    return nav_name[len(scheme_name) :].strip(" -:;/()[]")
